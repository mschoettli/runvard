"""Native, read-only status connectors for external servers."""

from __future__ import annotations

import io
import json
import math
import time
from urllib.parse import quote

import paramiko
import requests


def _number(value, *, minimum=0.0, maximum=None):
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return round(result, 1)


def normalize_snapshot(values, now=None):
    values = values or {}
    updates = values.get("updates")
    try:
        updates = max(0, int(updates)) if updates is not None else None
    except (TypeError, ValueError):
        updates = None
    return {
        "captured_at": int(time.time() if now is None else now),
        "cpu_percent": _number(
            values.get("cpu_percent"), maximum=100.0,
        ),
        "ram_percent": _number(
            values.get("ram_percent"), maximum=100.0,
        ),
        "network_down_rate": _number(values.get("network_down_rate")),
        "network_up_rate": _number(values.get("network_up_rate")),
        "updates": updates,
    }


class GenericHttpConnector:
    def __init__(self, session=None):
        self.session = session or requests.Session()

    def collect(self, config, credentials):
        response = self.session.get(
            config["status_url"], timeout=(3, 10),
            verify=config.get("verify_tls", True), allow_redirects=False,
        )
        response.raise_for_status()
        return normalize_snapshot({})

    def collect_updates(self, config, credentials):
        return None


class ProxmoxConnector:
    def __init__(self, session=None):
        self.session = session or requests.Session()

    @staticmethod
    def _headers(credentials):
        token_id = str(credentials.get("token_id") or "")
        token_secret = str(credentials.get("token_secret") or "")
        if not token_id or not token_secret:
            raise ValueError("Proxmox API token is incomplete")
        return {
            "Authorization": f"PVEAPIToken={token_id}={token_secret}",
            "Accept": "application/json",
        }

    def _get(self, config, credentials, path):
        response = self.session.get(
            config["status_url"].rstrip("/") + path,
            headers=self._headers(credentials),
            timeout=(3, 10),
            verify=config.get("verify_tls", True),
            allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "data" not in payload:
            raise ValueError("invalid Proxmox API response")
        return payload["data"]

    def _node(self, config, credentials):
        configured = str(config.get("node") or "").strip()
        if configured:
            return configured
        nodes = self._get(config, credentials, "/api2/json/nodes")
        online = next(
            (row for row in nodes if row.get("status") == "online"), None,
        )
        selected = online or (nodes[0] if nodes else None)
        if not selected or not selected.get("node"):
            raise ValueError("no Proxmox node is available")
        return str(selected["node"])

    def collect(self, config, credentials):
        node = self._node(config, credentials)
        encoded = quote(node, safe="")
        status = self._get(
            config, credentials, f"/api2/json/nodes/{encoded}/status",
        ) or {}
        memory = status.get("memory") or {}
        total = float(memory.get("total") or 0)
        used = float(memory.get("used") or 0)
        rrd = self._get(
            config, credentials,
            f"/api2/json/nodes/{encoded}/rrddata?timeframe=hour&cf=AVERAGE",
        ) or []
        latest = next((row for row in reversed(rrd) if row), {})
        return normalize_snapshot({
            "cpu_percent": float(status.get("cpu") or 0) * 100,
            "ram_percent": (used / total * 100) if total > 0 else None,
            "network_down_rate": latest.get("netin"),
            "network_up_rate": latest.get("netout"),
        })

    def collect_updates(self, config, credentials):
        node = self._node(config, credentials)
        rows = self._get(
            config, credentials,
            f"/api2/json/nodes/{quote(node, safe='')}/apt/update",
        ) or []
        return len(rows)


def _cpu_values(line):
    parts = line.split()
    if not parts or parts[0] != "cpu":
        raise ValueError("Linux CPU sample is missing")
    values = [float(item) for item in parts[1:]]
    total = sum(values)
    idle = sum(values[index] for index in (3, 4) if index < len(values))
    return total, idle


def _network_values(lines):
    received = sent = 0.0
    for line in lines:
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        if name.strip() == "lo":
            continue
        parts = values.split()
        if len(parts) >= 9:
            received += float(parts[0])
            sent += float(parts[8])
    return received, sent


def parse_linux_sample(value, elapsed=0.2):
    first, second = str(value).split("RV2", 1)
    first_lines = [
        line.strip() for line in first.splitlines()
        if line.strip() and line.strip() != "RV1"
    ]
    second_lines = [
        line.strip() for line in second.splitlines() if line.strip()
    ]
    cpu_first = next(line for line in first_lines if line.startswith("cpu "))
    cpu_second = next(line for line in second_lines if line.startswith("cpu "))
    total_a, idle_a = _cpu_values(cpu_first)
    total_b, idle_b = _cpu_values(cpu_second)
    total_delta = total_b - total_a
    idle_delta = idle_b - idle_a
    cpu = (
        (1 - idle_delta / total_delta) * 100 if total_delta > 0 else None
    )
    mem_total = mem_available = None
    for line in first_lines:
        if line.startswith("MemTotal:"):
            mem_total = float(line.split()[1])
        elif line.startswith("MemAvailable:"):
            mem_available = float(line.split()[1])
    ram = (
        (1 - mem_available / mem_total) * 100
        if mem_total and mem_available is not None else None
    )
    net_a = _network_values(first_lines)
    net_b = _network_values(second_lines)
    interval = max(0.01, float(elapsed))
    return normalize_snapshot({
        "cpu_percent": cpu,
        "ram_percent": ram,
        "network_down_rate": max(0, net_b[0] - net_a[0]) / interval,
        "network_up_rate": max(0, net_b[1] - net_a[1]) / interval,
    })


class _PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = str(expected or "").strip()

    def missing_host_key(self, client, hostname, key):
        actual = key.fingerprint
        if actual != self.expected:
            raise paramiko.SSHException(
                f"SSH host key mismatch; received {actual}",
            )
        client.get_host_keys().add(hostname, key.get_name(), key)


def _private_key(value, passphrase=None):
    source = io.StringIO(str(value or ""))
    errors = []
    key_types = (
        paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey,
    )
    for key_type in key_types:
        source.seek(0)
        try:
            return key_type.from_private_key(source, password=passphrase or None)
        except (paramiko.SSHException, ValueError) as exc:
            errors.append(exc)
    raise ValueError("unsupported or invalid SSH private key") from errors[-1]


LINUX_SAMPLE_COMMAND = (
    "printf 'RV1\\n'; cat /proc/stat; cat /proc/meminfo; "
    "cat /proc/net/dev; sleep 0.2; printf 'RV2\\n'; "
    "cat /proc/stat; cat /proc/net/dev"
)

LINUX_UPDATES_COMMAND = (
    "if command -v apt-get >/dev/null 2>&1; then "
    "apt-get -s upgrade 2>/dev/null | awk '/^Inst /{n++} END{print n+0}'; "
    "elif command -v dnf >/dev/null 2>&1; then "
    "dnf -q check-update 2>/dev/null | awk 'NF>=3 && $1 !~ /^Last/{n++} END{print n+0}'; "
    "elif command -v yum >/dev/null 2>&1; then "
    "yum -q check-update 2>/dev/null | awk 'NF>=3{n++} END{print n+0}'; "
    "elif command -v zypper >/dev/null 2>&1; then "
    "zypper -q list-updates 2>/dev/null | awk -F'|' '/^v/{n++} END{print n+0}'; "
    "elif command -v apk >/dev/null 2>&1; then "
    "apk version -l '<' 2>/dev/null | wc -l; "
    "elif command -v checkupdates >/dev/null 2>&1; then "
    "checkupdates 2>/dev/null | wc -l; "
    "else printf '\\n'; fi"
)


class LinuxConnector:
    def __init__(self, client_factory=None):
        self.client_factory = client_factory or paramiko.SSHClient

    def _client(self, config, credentials):
        client = self.client_factory()
        client.set_missing_host_key_policy(
            _PinnedHostKeyPolicy(config.get("host_key")),
        )
        client.connect(
            hostname=config["host"],
            port=int(config.get("port") or 22),
            username=config["username"],
            pkey=_private_key(
                credentials.get("private_key"),
                credentials.get("passphrase"),
            ),
            timeout=3,
            banner_timeout=5,
            auth_timeout=5,
            allow_agent=False,
            look_for_keys=False,
        )
        return client

    def _run(self, config, credentials, command):
        client = self._client(config, credentials)
        try:
            _, stdout, stderr = client.exec_command(command, timeout=10)
            exit_code = stdout.channel.recv_exit_status()
            output = stdout.read().decode("utf-8", "replace")
            error = stderr.read().decode("utf-8", "replace")
            if exit_code and not output.strip():
                raise ValueError(error.strip() or "Linux status command failed")
            return output
        finally:
            client.close()

    def collect(self, config, credentials):
        return parse_linux_sample(
            self._run(config, credentials, LINUX_SAMPLE_COMMAND),
        )

    def collect_updates(self, config, credentials):
        value = self._run(
            config, credentials, LINUX_UPDATES_COMMAND,
        ).strip()
        return max(0, int(value)) if value else None


WINDOWS_STATUS_SCRIPT = r"""
$cpu = (Get-CimInstance Win32_Processor |
  Measure-Object -Property LoadPercentage -Average).Average
$os = Get-CimInstance Win32_OperatingSystem
$before = Get-NetAdapterStatistics |
  Measure-Object -Property ReceivedBytes,SentBytes -Sum
Start-Sleep -Milliseconds 200
$after = Get-NetAdapterStatistics |
  Measure-Object -Property ReceivedBytes,SentBytes -Sum
$result = @{
  cpu_percent = [double]$cpu
  ram_percent = [math]::Round(
    (1 - ([double]$os.FreePhysicalMemory /
    [double]$os.TotalVisibleMemorySize)) * 100, 1)
  network_down_rate = [math]::Max(
    0, ([double]$after[0].Sum - [double]$before[0].Sum) / 0.2)
  network_up_rate = [math]::Max(
    0, ([double]$after[1].Sum - [double]$before[1].Sum) / 0.2)
}
$result | ConvertTo-Json -Compress
""".strip()

WINDOWS_UPDATES_SCRIPT = r"""
$session = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()
$searcher.Search("IsInstalled=0 and IsHidden=0").Updates.Count
""".strip()


def parse_windows_sample(response):
    if int(response.status_code) != 0:
        detail = response.std_err.decode("utf-8", "replace").strip()
        raise ValueError(detail or "Windows status query failed")
    try:
        value = json.loads(response.std_out.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid Windows status response") from exc
    return {
        key: normalize_snapshot(value)[key]
        for key in (
            "cpu_percent", "ram_percent",
            "network_down_rate", "network_up_rate",
        )
    }


class WindowsConnector:
    def __init__(self, session_factory=None):
        if session_factory is None:
            try:
                import winrm
            except ImportError as exc:
                raise RuntimeError("pywinrm is not installed") from exc
            session_factory = winrm.Session
        self.session_factory = session_factory

    def _session(self, config, credentials):
        return self.session_factory(
            config["status_url"],
            auth=(config["username"], credentials.get("password") or ""),
            transport="ntlm",
            server_cert_validation=(
                "validate" if config.get("verify_tls", True) else "ignore"
            ),
            operation_timeout_sec=8,
            read_timeout_sec=10,
        )

    def collect(self, config, credentials):
        return normalize_snapshot(parse_windows_sample(
            self._session(config, credentials).run_ps(WINDOWS_STATUS_SCRIPT),
        ))

    def collect_updates(self, config, credentials):
        response = self._session(config, credentials).run_ps(
            WINDOWS_UPDATES_SCRIPT,
        )
        if int(response.status_code) != 0:
            raise ValueError("Windows update query failed")
        value = response.std_out.decode("utf-8", "replace").strip()
        return max(0, int(value)) if value else None


def connector_for(kind):
    connectors = {
        "generic": GenericHttpConnector,
        "proxmox": ProxmoxConnector,
        "linux": LinuxConnector,
        "windows": WindowsConnector,
    }
    try:
        return connectors[str(kind)]()
    except KeyError as exc:
        raise ValueError("unsupported server type") from exc
