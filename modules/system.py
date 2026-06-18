"""System-Monitoring: CPU, RAM, Disk, Netzwerk, System-Info."""
import time
import socket
import platform
from types import SimpleNamespace
import psutil

_last_net = {"time": None, "sent": 0, "recv": 0}


def _safe_psutil(call, fallback):
    try:
        return call()
    except (PermissionError, OSError, psutil.Error):
        return fallback


def get_stats():
    """Live-Stats für die Dashboard-Widgets."""
    cpu_percent = psutil.cpu_percent(interval=None)
    per_cpu = psutil.cpu_percent(interval=None, percpu=True)
    mem = psutil.virtual_memory()
    swap = _safe_psutil(
        psutil.swap_memory,
        SimpleNamespace(total=0, used=0, percent=0),
    )
    freq = _safe_psutil(psutil.cpu_freq, None)

    # Netzwerk-Durchsatz berechnen
    net = _safe_psutil(
        psutil.net_io_counters,
        SimpleNamespace(bytes_sent=0, bytes_recv=0),
    )
    now = time.time()
    up_rate = down_rate = 0.0
    if _last_net["time"] is not None:
        dt = now - _last_net["time"]
        if dt > 0:
            up_rate = (net.bytes_sent - _last_net["sent"]) / dt
            down_rate = (net.bytes_recv - _last_net["recv"]) / dt
    _last_net.update({"time": now, "sent": net.bytes_sent, "recv": net.bytes_recv})

    return {
        "cpu": {
            "percent": round(cpu_percent, 1),
            "per_core": [round(c, 1) for c in per_cpu],
            "cores": psutil.cpu_count(logical=True),
            "freq": round(freq.current) if freq else None,
        },
        "memory": {
            "total": mem.total,
            "used": mem.used,
            "available": mem.available,
            "cached": getattr(mem, "cached", 0),
            "percent": mem.percent,
        },
        "swap": {"total": swap.total, "used": swap.used, "percent": swap.percent},
        "network": {
            "up_rate": round(up_rate),
            "down_rate": round(down_rate),
            "bytes_sent": net.bytes_sent,
            "bytes_recv": net.bytes_recv,
        },
    }


def get_disk_usage():
    """Übersicht aller gemounteten Partitionen."""
    disks = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({
            "device": part.device,
            "mountpoint": part.mountpoint,
            "fstype": part.fstype,
            "total": usage.total,
            "used": usage.used,
            "free": usage.free,
            "percent": usage.percent,
        })
    return disks


def get_system_info():
    """Statische System-Infos."""
    uname = platform.uname()
    boot = _safe_psutil(psutil.boot_time, time.time())
    return {
        "hostname": socket.gethostname(),
        "os": f"{uname.system} {uname.release}",
        "kernel": uname.version,
        "arch": uname.machine,
        "uptime_seconds": round(time.time() - boot),
        "boot_time": boot,
        "cpu_model": _cpu_model(),
    }


def _cpu_model():
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if "model name" in line:
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "Unknown"


def get_temps():
    """Systemtemperaturen via psutil (lm-sensors)."""
    result = {}
    try:
        temps = psutil.sensors_temperatures()
        for chip, entries in temps.items():
            result[chip] = [
                {"label": e.label or chip, "current": e.current,
                 "high": e.high, "critical": e.critical}
                for e in entries
            ]
    except (AttributeError, OSError):
        pass
    return result


def get_processes(sort_by="cpu", limit=15):
    """Top-Prozesse sortiert nach CPU oder RAM."""
    sort_by = sort_by if sort_by in ("cpu", "ram") else "cpu"
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 15
    limit = max(1, min(limit, 100))
    procs = []
    try:
        iterator = psutil.process_iter(["pid", "name", "cpu_percent",
                                        "memory_info", "status", "username"])
        for p in iterator:
            try:
                info = p.info
                procs.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "cpu": round(info["cpu_percent"] or 0, 1),
                    "ram": info["memory_info"].rss if info["memory_info"] else 0,
                    "status": info["status"],
                    "user": info["username"] or "",
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except (PermissionError, psutil.Error, OSError) as exc:
        return {"ok": False, "processes": [], "stderr": str(exc)}
    procs.sort(key=lambda x: x[sort_by], reverse=True)
    return {"ok": True, "processes": procs[:limit]}


def get_disk_io():
    """Disk-IO-Statistiken pro Device."""
    try:
        counters = psutil.disk_io_counters(perdisk=True)
        return {"ok": True, "disk_io": {
            dev: {
                "read_bytes": c.read_bytes,
                "write_bytes": c.write_bytes,
                "read_count": c.read_count,
                "write_count": c.write_count,
            }
            for dev, c in counters.items()
        }}
    except (AttributeError, OSError, psutil.Error) as exc:
        return {"ok": False, "disk_io": {}, "stderr": str(exc)}


def get_net_detail():
    """Detaillierte Netzwerk-Statistiken pro Interface."""
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        counters = psutil.net_io_counters(pernic=True)
    except (AttributeError, OSError, psutil.Error) as exc:
        return {"ok": False, "interfaces": [], "stderr": str(exc)}
    result = []
    try:
        for name in stats:
            if name == "lo":
                continue
            st = stats[name]
            ctr = counters.get(name)
            ipv4 = next((a.address for a in addrs.get(name, [])
                         if a.family == socket.AF_INET), None)
            result.append({
                "name": name,
                "ipv4": ipv4,
                "up": st.isup,
                "speed": st.speed,
                "mtu": st.mtu,
                "bytes_sent": ctr.bytes_sent if ctr else 0,
                "bytes_recv": ctr.bytes_recv if ctr else 0,
                "packets_sent": ctr.packets_sent if ctr else 0,
                "packets_recv": ctr.packets_recv if ctr else 0,
                "errin": ctr.errin if ctr else 0,
                "errout": ctr.errout if ctr else 0,
            })
    except Exception as exc:
        return {"ok": False, "interfaces": [], "stderr": str(exc)}
    return {"ok": True, "interfaces": result}
