"""Local discovery and evidence-based LAN/Zigbee topology; no device control."""
import asyncio
import copy
import ipaddress
import json
import math
import socket
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit, urlencode

import psutil
import requests
import websockets
from modules.external_servers.storage import _atomic_json, SecretStore
from modules.network_map_fritz import read_topology, parse_topology

PRIVATE = [ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')]


def private_ip(value):
    ip = ipaddress.ip_address(value)
    if ip.version != 4 or not any(ip in n for n in PRIVATE):
        raise ValueError('private_ipv4_required')
    return ip


def validate_network(value):
    net = ipaddress.ip_network(value, strict=False)
    if net.version != 4 or net.num_addresses > 256 or not any(net.subnet_of(n) for n in PRIVATE):
        raise ValueError('private_subnet_max_256')
    return net


def zigbee_endpoint(value):
    url = urlsplit(value)
    if url.scheme not in ('http', 'https') or url.username or url.password or url.query or url.fragment:
        raise ValueError('invalid_zigbee_url')
    private_ip(url.hostname or '')
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError('invalid_zigbee_url')
    return value.rstrip('/')


def local_networks():
    """Only directly attached physical LANs; never guess a /24 for a larger LAN."""
    result = []
    stats = psutil.net_if_stats()
    for name, addresses in psutil.net_if_addrs().items():
        if name.startswith(('lo', 'docker', 'veth', 'virbr', 'br-', 'tun', 'tap', 'wg')):
            continue
        if not stats.get(name) or not stats[name].isup:
            continue
        for addr in addresses:
            if addr.family != socket.AF_INET or not addr.netmask:
                continue
            try:
                result.append(str(validate_network(f'{addr.address}/{addr.netmask}')))
            except ValueError:
                continue
    return sorted(set(result))[:4]


def neighbor_macs():
    try:
        rows = json.loads(subprocess.check_output(['ip', '-j', 'neigh'], timeout=3))
        return {r['dst']: r['lladdr'].lower() for r in rows if r.get('lladdr')}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def probe(ip):
    """Small reachability probe; only explicit positive responses imply online."""
    alive = False
    try:
        alive = subprocess.run(['ping', '-n', '-c', '1', '-W', '1', ip],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1.5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        pass
    ports = []
    for port in (80, 443, 8080):
        try:
            with socket.create_connection((ip, port), timeout=.18):
                ports.append(port)
                alive = True
        except OSError:
            pass
    if not alive:
        return None
    device = {'ip': ip, 'name': ip, 'status': 'online', 'ports': ports, 'kind': 'device'}
    # AVM publishes its model without credentials. Never follow redirects off-host.
    with requests.Session() as session:
        session.trust_env = False
        try:
            with session.get(f'http://{ip}:49000/tr64desc.xml', timeout=(.4, 1), allow_redirects=False, stream=True) as response:
                if response.status_code == 200:
                    body = response.raw.read(180000)
                    root = ET.fromstring(body)
                    fields = {}
                    for e in root.iter():
                        fields.setdefault(e.tag.rsplit('}', 1)[-1], e.text)
                    device.update(name=fields.get('friendlyName') or ip,
                                  model=fields.get('modelName') or '',
                                  manufacturer=fields.get('manufacturer') or '', kind='router')
        except (requests.RequestException, ET.ParseError, OSError):
            pass
        if ports and not device.get('model'):
            port = next((p for p in ports if p != 443), None)
            if port:
                try:
                    with session.get(f'http://{ip}:{port}/', timeout=(.4, 1), allow_redirects=False, stream=True) as response:
                        body = response.raw.read(64000).decode('utf-8', 'replace')
                        if 'zigbee2mqtt' in body.lower():
                            device.update(name='Zigbee2MQTT', kind='bridge', zigbee_url=f'http://{ip}:{port}')
                except (requests.RequestException, OSError):
                    pass
    return device


class NetworkMap:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / 'network-map.json'
        self.secrets = SecretStore(Path(data_dir) / 'network-map-secrets')
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.mesh_request = threading.Event()
        self.thread = None
        self.zthread = None
        self.scanning = False
        self.connected = False
        self.error = ''
        self.zerror = ''
        self.fritz_error = ''
        self.state = {'devices': {}, 'edges': [], 'settings': {'networks': [], 'zigbee_url': ''}, 'scan_at': 0, 'mesh_at': 0, 'fritz_at': 0}
        try:
            value = json.loads(self.path.read_text())
            self.state.update({k: value[k] for k in self.state if k in value})
        except (OSError, ValueError):
            pass

    def save(self):
        _atomic_json(self.path, self.state)

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.state)
            result['devices'] = list(result['devices'].values())
            result.update(scanning=self.scanning, connected=self.connected, error=self.error, zigbee_error=self.zerror, fritz_error=self.fritz_error)
            result['settings']['fritz_password_saved'] = bool(self.secrets.get('fritz').get('password'))
            result['settings']['token_saved'] = bool(self.secrets.get('zigbee').get('token'))
            return result

    def configure(self, data):
        with self.lock:
            settings = dict(self.state['settings'])
            if 'networks' in data:
                if not isinstance(data['networks'], list) or len(data['networks']) > 4:
                    raise ValueError('private_subnet_max_256')
                settings['networks'] = [str(validate_network(n)) for n in data['networks']]
            if 'zigbee_url' in data:
                settings['zigbee_url'] = zigbee_endpoint(data['zigbee_url']) if data['zigbee_url'] else ''
            if 'fritz_host' in data:
                settings['fritz_host'] = str(private_ip(data['fritz_host'])) if data['fritz_host'] else ''
            if 'fritz_user' in data:
                if not isinstance(data['fritz_user'], str) or len(data['fritz_user']) > 160:
                    raise ValueError('invalid_request')
                settings['fritz_user'] = data['fritz_user']
            if data.get('fritz_password'):
                if not isinstance(data['fritz_password'], str) or len(data['fritz_password']) > 2048:
                    raise ValueError('invalid_request')
                self.secrets.set('fritz', {'password':data['fritz_password']})
            if data.get('clear_fritz_password'):
                self.secrets.delete('fritz')
            if 'token' in data and data['token']:
                if not isinstance(data['token'], str) or len(data['token']) > 2048:
                    raise ValueError('invalid_token')
                self.secrets.set('zigbee', {'token': data['token']})
            if data.get('clear_token'):
                self.secrets.delete('zigbee')
            self.state['settings'] = settings
            self.save()

    def edit_device(self, identity, data):
        with self.lock:
            device = self.state['devices'].get(identity)
            if device is None:
                raise ValueError('device_missing')
            patch = {}
            for key in ('label', 'room', 'notes', 'parent'):
                if key in data:
                    if not isinstance(data[key], str) or len(data[key]) > (1000 if key == 'notes' else 160):
                        raise ValueError('invalid_device')
                    patch[key] = data[key].strip()
            if 'layout_group' in data:
                if data['layout_group'] not in ('gateway','lan','wlan','zigbee','unknown'):
                    raise ValueError('invalid_device')
                patch['layout_group'] = data['layout_group']
            if 'connection_type' in data:
                if data['connection_type'] not in ('lan','wlan','unknown'):
                    raise ValueError('invalid_device')
                patch['connection_type'] = data['connection_type']
            for key in ('x', 'y'):
                if key in data:
                    v = float(data[key])
                    if not math.isfinite(v) or not 0 <= v <= 10000:
                        raise ValueError('invalid_position')
                    patch[key] = v
            parent = patch.get('parent', device.get('parent', ''))
            seen = {identity}
            cursor = parent
            while cursor:
                if cursor in seen or cursor not in self.state['devices']:
                    raise ValueError('invalid_parent')
                seen.add(cursor)
                cursor = self.state['devices'][cursor].get('parent', '')
            device.update(patch)
            self.state['edges'] = [e for e in self.state['edges'] if not (e['origin'] == 'manual' and e['target'] == identity)]
            if parent:
                self.state['edges'].append({'source': parent, 'target': identity, 'origin': 'manual', **({'type':device['connection_type']} if device.get('connection_type') else {})})
            self.save()

    def ingest_fritz(self, report, hosts, gateway_ip):
        with self.lock:
            devices, edges = parse_topology(report, hosts, self.state['devices'], gateway_ip)
            manual = {d['id'] for d in self.state['devices'].values() if d.get('parent')}
            for item in devices:
                d = self.state['devices'].setdefault(item['id'], {})
                if item['id'] in manual: item.pop('connection_type', None)
                d.update(item)
            self.state['edges'] = [e for e in self.state['edges'] if e['origin'] != 'fritz'] + [e for e in edges if e['target'] not in manual]
            self.state['fritz_at'] = time.time()
            self.fritz_error = ''
            self.save()

    def refresh_fritz(self):
        with self.lock:
            settings = dict(self.state['settings'])
        if not settings.get('fritz_host'): return
        try:
            topology, hosts = read_topology(settings['fritz_host'], settings.get('fritz_user',''), self.secrets.get('fritz').get('password',''))
            self.ingest_fritz(topology, hosts, settings['fritz_host'])
        except Exception:
            self.fritz_error = 'fritz_failed'

    def merge_lan(self, found):
        with self.lock:
            for d in self.state['devices'].values():
                if d.get('network') == 'lan':
                    d['status'] = 'offline'
            for item in found:
                identity = item.get('id') or 'lan:' + (item.get('mac') or item['ip'])
                # Promote IP identity once a MAC becomes available, preserving annotations.
                old_id = next((k for k, d in self.state['devices'].items() if k != identity and
                               d.get('network') == 'lan' and item.get('ip') and d.get('ip') == item.get('ip') and not d.get('mac')), None)
                if old_id:
                    self.state['devices'][identity] = self.state['devices'].pop(old_id)
                    for d in self.state['devices'].values():
                        if d.get('parent') == old_id: d['parent'] = identity
                    for e in self.state['edges']:
                        for key in ('source', 'target'):
                            if e[key] == old_id: e[key] = identity
                d = self.state['devices'].setdefault(identity, {})
                d.update(item, id=identity, network='lan', last_seen=time.time(), status='online')
            self.state['scan_at'] = time.time()
            self.save()

    def scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return False
        self.scanning = True
        try:
            with self.lock:
                networks = self.state['settings']['networks'] or local_networks()
            if not networks:
                raise ValueError('no_supported_network')
            hosts = [str(ip) for n in networks for ip in validate_network(n).hosts()]
            found = []
            with ThreadPoolExecutor(max_workers=24) as pool:
                for item in pool.map(probe, dict.fromkeys(hosts)):
                    if item: found.append(item)
            macs = neighbor_macs()
            for item in found:
                if item['ip'] in macs: item['mac'] = macs[item['ip']]
            self.merge_lan(found)
            self.refresh_fritz()
            self.error = ''
            return True
        except Exception:
            self.error = 'scan_failed'
            return False
        finally:
            self.scanning = False
            self.scan_lock.release()

    def request_scan(self):
        if self.scanning: return False
        threading.Thread(target=self.scan, daemon=True).start()
        return True

    def ingest_zigbee(self, topic, payload):
        with self.lock:
            devices = self.state['devices']
            if topic == 'bridge/devices' and isinstance(payload, list):
                active = set()
                for item in payload[:2000]:
                    if not isinstance(item, dict) or not item.get('ieee_address'): continue
                    identity = 'zigbee:' + str(item['ieee_address'])
                    active.add(identity)
                    d = devices.setdefault(identity, {})
                    definition = item.get('definition') or {}
                    d.update(id=identity, network='zigbee', name=item.get('friendly_name') or item['ieee_address'],
                             ieee=item['ieee_address'], kind=item.get('type', 'EndDevice'),
                             model=definition.get('model', ''), manufacturer=definition.get('vendor', ''),
                             power_source=item.get('power_source', ''), removed=False)
                    d.setdefault('status', 'unknown')
                for d in devices.values():
                    if d.get('network') == 'zigbee' and d['id'] not in active:
                        d.update(removed=True, status='offline')
            elif topic == 'bridge/response/networkmap' and isinstance(payload, dict):
                graph = (payload.get('data') or {}).get('value')
                if payload.get('status') != 'ok' or not isinstance(graph, dict):
                    self.zerror = 'mesh_failed'
                    return
                edges = []
                for link in graph.get('links', [])[:10000]:
                    def address(value):
                        return value.get('ieeeAddr', '') if isinstance(value, dict) else str(value)
                    source, target = 'zigbee:' + address(link.get('source')), 'zigbee:' + address(link.get('target'))
                    if source in devices and target in devices and source != target:
                        edges.append({'source': source, 'target': target, 'origin': 'zigbee', 'lqi': link.get('linkquality')})
                self.state['edges'] = [e for e in self.state['edges'] if e['origin'] != 'zigbee'] + edges
                self.state['mesh_at'] = time.time()
                self.zerror = ''
            else:
                availability = topic.endswith('/availability')
                name = topic[:-13] if availability else topic
                for d in devices.values():
                    if d.get('network') != 'zigbee' or d.get('name') != name: continue
                    if availability:
                        value = payload.get('state') if isinstance(payload, dict) else payload
                        if value in ('online', 'offline'): d['status'] = value
                    elif isinstance(payload, dict):
                        for key in ('battery', 'linkquality'):
                            if isinstance(payload.get(key), (int, float)): d[key] = payload[key]
                        d['last_seen'] = time.time()
            self.save()

    async def zigbee_session(self, endpoint):
        url = urlsplit(zigbee_endpoint(endpoint))
        token = self.secrets.get('zigbee').get('token', '')
        address = ('wss' if url.scheme == 'https' else 'ws') + '://' + url.netloc + url.path.rstrip('/') + '/api'
        if token: address += '?' + urlencode({'token': token})
        async with websockets.connect(address, open_timeout=5, close_timeout=2, max_size=4*1024*1024) as ws:
            self.connected = True
            self.zerror = ''
            while not self.stop_event.is_set():
                with self.lock:
                    if self.state['settings']['zigbee_url'] != endpoint: break
                if self.mesh_request.is_set():
                    self.mesh_request.clear()
                    await ws.send(json.dumps({'topic': 'bridge/request/networkmap', 'payload': {'type': 'raw', 'routes': True}}))
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1)
                    message = json.loads(raw)
                    if isinstance(message, dict): self.ingest_zigbee(message.get('topic', ''), message.get('payload'))
                except asyncio.TimeoutError:
                    continue

    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.stop_event.clear()
        def lan_loop():
            while not self.stop_event.is_set():
                self.scan()
                if self.stop_event.wait(300): break
        def zigbee_loop():
            while not self.stop_event.is_set():
                with self.lock: endpoint = self.state['settings']['zigbee_url']
                if endpoint:
                    try: asyncio.run(self.zigbee_session(endpoint))
                    except Exception: self.zerror = 'zigbee_failed'
                    finally: self.connected = False
                if self.stop_event.wait(5): break
        self.thread = threading.Thread(target=lan_loop, daemon=True)
        self.zthread = threading.Thread(target=zigbee_loop, daemon=True)
        self.thread.start()
        self.zthread.start()

    def stop(self):
        self.stop_event.set()
        for thread in (self.thread, self.zthread):
            if thread: thread.join(timeout=3)
