"""Historische Systemmetriken: Hintergrund-Sampler + Ring-Puffer."""
import time
import threading
import collections

import psutil

# ~4 h Verlauf bei 5 s Intervall
RING = collections.deque(maxlen=2880)
_last_net = None
_started = False
_lock = threading.Lock()


def _sample():
    global _last_net
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    net = psutil.net_io_counters()
    now = time.time()
    down = up = 0.0
    if _last_net:
        dt = now - _last_net[0]
        if dt > 0:
            down = max(0.0, (net.bytes_recv - _last_net[1]) / dt)
            up = max(0.0, (net.bytes_sent - _last_net[2]) / dt)
    _last_net = (now, net.bytes_recv, net.bytes_sent)
    try:
        disk = psutil.disk_usage("/").percent
    except Exception:
        disk = 0
    with _lock:
        RING.append({
            "t": int(now),
            "cpu": round(cpu, 1),
            "ram": round(ram, 1),
            "net_down": round(down, 1),
            "net_up": round(up, 1),
            "disk": round(disk, 1),
        })


def start_sampler(interval=5):
    """Sampler-Thread einmalig starten (idempotent)."""
    global _started
    if _started:
        return
    _started = True
    psutil.cpu_percent(interval=None)  # priming call

    def loop():
        while True:
            try:
                _sample()
            except Exception:
                pass
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True).start()


def get_history(minutes=60):
    cutoff = time.time() - minutes * 60
    with _lock:
        pts = [p for p in RING if p["t"] >= cutoff]
    # auf ~240 Punkte downsamplen
    if len(pts) > 240:
        step = len(pts) // 240 + 1
        pts = pts[::step]
    return {"points": pts}
