"""System-Monitoring: CPU, RAM, Disk, Netzwerk, System-Info."""
import time
import socket
import platform
import psutil

_last_net = {"time": None, "sent": 0, "recv": 0}


def get_stats():
    """Live-Stats für die Dashboard-Widgets."""
    cpu_percent = psutil.cpu_percent(interval=None)
    per_cpu = psutil.cpu_percent(interval=None, percpu=True)
    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()

    # Netzwerk-Durchsatz berechnen
    net = psutil.net_io_counters()
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
            "freq": round(psutil.cpu_freq().current) if psutil.cpu_freq() else None,
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
    boot = psutil.boot_time()
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
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent",
                                   "memory_info", "status", "username"]):
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
    key = "cpu" if sort_by == "cpu" else "ram"
    procs.sort(key=lambda x: x[key], reverse=True)
    return procs[:limit]


def get_disk_io():
    """Disk-IO-Statistiken pro Device."""
    try:
        counters = psutil.disk_io_counters(perdisk=True)
        return {
            dev: {
                "read_bytes": c.read_bytes,
                "write_bytes": c.write_bytes,
                "read_count": c.read_count,
                "write_count": c.write_count,
            }
            for dev, c in counters.items()
        }
    except (AttributeError, OSError):
        return {}


def get_net_detail():
    """Detaillierte Netzwerk-Statistiken pro Interface."""
    addrs = psutil.net_if_addrs()
    stats = psutil.net_if_stats()
    counters = psutil.net_io_counters(pernic=True)
    result = []
    for name in stats:
        if name == "lo":
            continue
        st = stats[name]
        ctr = counters.get(name)
        ipv4 = next((a.address for a in addrs.get(name, [])
                     if a.family.name == "AF_INET"), None)
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
    return result
