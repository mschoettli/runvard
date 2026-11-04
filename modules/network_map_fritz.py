"""Read-only FRITZ! Mesh topology via TR-064 Hosts:1 (no new dependency)."""
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit

import requests
from requests.auth import HTTPDigestAuth


def mac(value):
    value = str(value or '').lower().replace('-', ':')
    return value if re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', value) else ''


def read_topology(host, username, password):
    base = f'http://{host}:49000'
    service = 'urn:dslforum-org:service:Hosts:1'
    def read(response):
        if response.status_code != 200:
            raise ValueError('fritz_failed')
        body = response.raw.read(4*1024*1024+1)
        if len(body) > 4*1024*1024:
            raise ValueError('fritz_failed')
        return body
    with requests.Session() as session:
        session.trust_env = False
        session.headers['Accept-Encoding'] = 'identity'
        session.auth = HTTPDigestAuth(username, password)
        def document(action, output):
            envelope = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                        f'<u:{action} xmlns:u="{service}"/></s:Body></s:Envelope>')
            with session.post(base+'/upnp/control/hosts', data=envelope.encode(),
                              headers={'SOAPAction': f'"{service}#{action}"', 'Content-Type':'text/xml; charset=utf-8'},
                              timeout=(3, 8), allow_redirects=False, stream=True) as response:
                root = ET.fromstring(read(response))
            path = next((e.text for e in root.iter() if e.tag.rsplit('}',1)[-1] == output), '')
            target = urljoin(base+'/', path or '')
            if not path or urlsplit(target).netloc != urlsplit(base).netloc or urlsplit(target).scheme != 'http':
                raise ValueError('fritz_failed')
            with session.get(target, timeout=(3, 8), allow_redirects=False, stream=True) as response:
                return read(response)
        topology = json.loads(document('X_AVM-DE_GetMeshListPath', 'NewX_AVM-DE_MeshListPath'))
        hosts = []
        try:
            root = ET.fromstring(document('X_AVM-DE_GetHostListPath', 'NewX_AVM-DE_HostListPath'))
            for item in root.iter():
                if item.tag.rsplit('}',1)[-1] != 'Item':
                    continue
                fields = {e.tag.rsplit('}',1)[-1]: e.text for e in item}
                hosts.append({'mac': fields.get('MACAddress',''), 'ip': fields.get('IPAddress',''), 'name':fields.get('HostName','')})
        except (requests.RequestException, ValueError, ET.ParseError):
            pass  # The mesh itself still supplies identities and relationships.
    return topology, hosts


def parse_topology(report, hosts, existing, gateway_ip):
    if not isinstance(report, dict) or not isinstance(report.get('nodes'), list):
        raise ValueError('fritz_failed')
    known = {mac(d.get('mac')): d for d in existing.values() if mac(d.get('mac'))}
    addresses = {mac(h.get('mac')): h for h in hosts if mac(h.get('mac'))}
    devices, identities, raw_links = [], {}, []
    for node in report['nodes'][:2000]:
        if not isinstance(node, dict): continue
        address = mac(node.get('device_mac_address'))
        if not address or not node.get('uid'): continue
        old = known.get(address, {})
        host = addresses.get(address, {})
        identity = old.get('id') or 'lan:'+address
        identities[node['uid']] = identity
        d = {'id':identity, 'mac':address, 'name':node.get('device_name') or host.get('name') or address,
             'model':node.get('device_model') or old.get('model',''), 'manufacturer':node.get('device_manufacturer') or old.get('manufacturer',''),
             'network':'lan', 'status':old.get('status','unknown'), 'kind':old.get('kind','device')}
        ip = host.get('ip') or old.get('ip')
        if ip: d['ip'] = ip
        if ip == gateway_ip: d.update(gateway=True, kind='router')
        devices.append(d)
        for interface in node.get('node_interfaces', []):
            if isinstance(interface, dict):
                raw_links.extend(x for x in interface.get('node_links',[]) if isinstance(x,dict))
    if report['nodes'] and not devices: raise ValueError('fritz_failed')
    edges, seen = [], set()
    by_id = {d['id']:d for d in devices}
    for link in raw_links[:10000]:
        a,b = identities.get(link.get('node_1_uid')), identities.get(link.get('node_2_uid'))
        kind = str(link.get('type','')).upper()
        if not a or not b or a == b or str(link.get('state','')).upper() not in ('CONNECTED','ACTIVE'): continue
        key = tuple(sorted([a,b]))
        if key in seen: continue
        seen.add(key)
        # AVM reports interface endpoints, not necessarily an oriented tree.
        if by_id[b].get('gateway'): a,b = b,a
        connection_type = 'wlan' if kind in ('WLAN','WIFI','WI-FI') else 'lan' if kind in ('LAN','ETHERNET') else 'unknown'
        edges.append({'source':a,'target':b,'origin':'fritz','type':connection_type})
    adjacency = {identity:[] for identity in by_id}
    for edge in edges:
        adjacency[edge['source']].append(edge['target'])
        adjacency[edge['target']].append(edge['source'])
    depth = {d['id']:0 for d in devices if d.get('gateway')}
    queue = list(depth)
    while queue:
        identity = queue.pop(0)
        for other in adjacency[identity]:
            if other not in depth:
                depth[other] = depth[identity]+1
                queue.append(other)
    for edge in edges:
        a,b = edge['source'],edge['target']
        if depth.get(a,0)>depth.get(b,0):
            a,b = b,a
            edge['source'],edge['target'] = a,b
        if not by_id[b].get('gateway') and (b not in depth or depth.get(b,0)>depth.get(a,0)):
            by_id[b]['connection_type'] = edge['type']
    return devices, edges
