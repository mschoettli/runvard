"""Explicit read-only device grants. Device secrets are never session credentials."""
from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import tempfile
import threading
import time

FIELDS = (
    'status', 'cpu_percent', 'ram_percent', 'temperatures', 'disk_percent',
    'disk_health', 'network', 'containers', 'vms', 'updates', 'alerts', 'backups',
)


class DeviceAccessStore:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lock:
            fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
            with os.fdopen(fd, 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}

    def _save(self, rows):
        fd, name = tempfile.mkstemp(prefix='.device-access-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as out:
                json.dump(rows, out, ensure_ascii=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def public(row):
        return {k: row[k] for k in ('id', 'name', 'servers', 'fields', 'enabled', 'created_at')}

    def list(self):
        with self.transaction():
            return [self.public(row) for row in self._load().values()]

    def create(self, name, servers, fields):
        device_id = secrets.token_hex(16)
        token = 'rvd_' + device_id + '_' + secrets.token_urlsafe(32)
        row = dict(id=device_id, name=name, servers=servers, fields=fields,
                   enabled=True, created_at=int(time.time()),
                   token_hash=hashlib.sha256(token.encode()).hexdigest())
        with self.transaction():
            rows = self._load()
            if len(rows) >= 100:
                raise ValueError('device_limit')
            rows[device_id] = row
            self._save(rows)
        return {'device': self.public(row), 'token': token}

    def update(self, device_id, name, servers, fields, enabled):
        with self.transaction():
            rows = self._load()
            if device_id not in rows:
                raise KeyError(device_id)
            rows[device_id].update(name=name, servers=servers, fields=fields, enabled=enabled)
            self._save(rows)
            return self.public(rows[device_id])

    def set_enabled(self, device_id, enabled):
        with self.transaction():
            rows = self._load()
            if device_id not in rows:
                raise KeyError(device_id)
            rows[device_id]['enabled'] = enabled
            self._save(rows)

    def delete(self, device_id):
        with self.transaction():
            rows = self._load()
            if device_id not in rows:
                raise KeyError(device_id)
            del rows[device_id]
            self._save(rows)

    def authenticate(self, token):
        if not isinstance(token, str) or not token.startswith('rvd_') or len(token) > 160:
            return None
        device_id = token[4:36]
        try:
            with self.transaction():
                row = self._load().get(device_id)
                if row and row['enabled'] and hmac.compare_digest(
                    row['token_hash'], hashlib.sha256(token.encode()).hexdigest(),
                ):
                    return self.public(row)
        except (ValueError, KeyError, TypeError, OSError):
            return None
        return None


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def counts(value):
    if not isinstance(value, dict) or value.get('available') is False:
        return None
    return {key: number(value.get(key)) for key in ('total', 'running')}


def snapshot_metrics(source, fields):
    """Project peer snapshots; never forward raw peer payloads or URLs."""
    snapshot = source.get('snapshot') or {}
    result = {}
    for field in fields:
        if field == 'status':
            result[field] = {'online': source.get('health') == 'online',
                             'last_contact': number(source.get('last_contact'))}
        elif field == 'network':
            result[field] = {key: number(snapshot.get('network_' + key))
                             for key in ('up_rate', 'down_rate')}
        elif field in ('containers', 'vms'):
            result[field] = counts(snapshot.get('docker' if field == 'containers' else 'vms'))
        elif field in ('cpu_percent', 'ram_percent', 'disk_percent', 'updates', 'alerts'):
            result[field] = number(snapshot.get(field))
        else:
            result[field] = None
    return result


class LocalTelemetry:
    """Collect only selected categories, with a short cache for expensive readers."""
    def __init__(self):
        self.lock = threading.RLock()
        self.cache = {}

    def _read(self, key, collect):
        with self.lock:
            now = time.monotonic()
            if key not in self.cache or now - self.cache[key][0] >= 10:
                try:
                    value = collect()
                except Exception:
                    value = None
                self.cache[key] = (time.monotonic(), value)
            return self.cache[key][1]

    def collect(self, fields):
        from modules import system, storage, docker_mgr, vms, apps, monitoring, backup
        result = {}
        stats = {}
        if set(fields) & {'cpu_percent', 'ram_percent', 'network'}:
            stats = self._read('stats', system.get_stats) or {}
        for field in fields:
            if field == 'status':
                info = self._read('info', system.get_system_info) or {}
                result[field] = {'online': True, 'last_contact': int(time.time()),
                                 'uptime_seconds': number(info.get('uptime_seconds'))}
            elif field in ('cpu_percent', 'ram_percent'):
                group = stats.get('cpu' if field == 'cpu_percent' else 'memory') or {}
                result[field] = number(group.get('percent'))
            elif field == 'network':
                network = stats.get('network') or {}
                result[field] = {k: number(network.get(k)) for k in ('up_rate', 'down_rate')}
            elif field == 'disk_percent':
                rows = self._read('disks', system.get_disk_usage)
                root = next((r for r in rows or [] if r.get('mountpoint') == '/'), {})
                result[field] = number(root.get('percent'))
            elif field == 'temperatures':
                rows = self._read('temperatures', system.get_temps)
                result[field] = [
                    {'sensor': str(row.get('label') or chip)[:128], 'current': number(row.get('current'))}
                    for chip, entries in (rows or {}).items() for row in entries
                ] or None
            elif field == 'disk_health':
                def health():
                    devices = storage.list_block_devices()
                    if devices.get('error'):
                        return None
                    rows = []
                    for disk in devices.get('devices', [])[:32]:
                        if disk.get('type') != 'disk':
                            continue
                        smart = storage.smart_data(disk['name'])
                        rows.append({'device': disk['name'], 'available': bool(smart.get('available')),
                                     'healthy': smart.get('healthy') if isinstance(smart.get('healthy'), bool) else None,
                                     'temperature': number(smart.get('temperature'))})
                    return rows or None
                result[field] = self._read('disk_health', health)
            elif field in ('containers', 'vms'):
                rows = self._read(field, docker_mgr.list_containers if field == 'containers' else vms.list_vms)
                result[field] = None if rows is None else {
                    'total': len(rows), 'running': (sum(
                        1 for row in rows if row.get('state') == 'running' or row.get('status') == 'running'
                    ) if field == 'containers' else sum(1 for row in rows if row.get('active'))),
                }
            elif field == 'updates':
                updates = self._read('updates', apps._load_updates)
                result[field] = len(updates['updates']) if isinstance(updates, dict) and isinstance(updates.get('updates'), list) else None
            elif field == 'alerts':
                rows = self._read('alerts', monitoring.get_alert_history)
                result[field] = sum(1 for r in rows if not r.get('resolved')) if rows is not None else None
            elif field == 'backups':
                rows = self._read('backups', backup.get_history)
                latest = (rows or [{}])[0]
                result[field] = None if not rows else {
                    'last_run': number(latest.get('time')),
                    'success': latest.get('success') if isinstance(latest.get('success'), bool) else None,
                    'last_success': max((number(r.get('time')) or 0 for r in rows if r.get('success')), default=None),
                }
        return result
