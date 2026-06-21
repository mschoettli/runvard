import os

from modules import vms


class _GoodDomain:
    def state(self):
        return 1, 0

    def info(self):
        return 1, 2048, 1024, 2, 0

    def name(self):
        return "debian-vm"

    def UUIDString(self):
        return "12345678-1234-5678-1234-567812345678"

    def isActive(self):
        return 1

    def autostart(self):
        return 0


class _BrokenDomain:
    def state(self):
        raise RuntimeError("domain disappeared")


class _Conn:
    def listAllDomains(self):
        return [_BrokenDomain(), _GoodDomain()]


class _EmptyConn:
    def listAllDomains(self):
        return []


def test_list_vms_skips_unreadable_domains(monkeypatch):
    monkeypatch.setattr(vms, "HAS_LIBVIRT", True)
    monkeypatch.setattr(vms, "_connect", lambda: _Conn())

    listed = vms.list_vms()

    assert [vm["name"] for vm in listed] == ["debian-vm"]
    assert listed[0]["active"] is True
    assert listed[0]["max_mem"] == 2048 * 1024


def test_create_vm_reports_visible_domain(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, timeout=120):
        calls.append(args)
        assert args[:4] == ["qemu-img", "create", "-f", "qcow2"]
        assert args[-1] == "20G"
        return {"ok": True, "stdout": "disk created", "stderr": ""}

    def fake_virsh(args, timeout=120):
        calls.append(args)
        if args[0] == "define":
            assert os.path.exists(args[1])
            xml = open(args[1], encoding="utf-8").read()
            assert "<name>debian-vm</name>" in xml
            assert 'network="default"' in xml
            return {"ok": True, "stdout": "defined", "stderr": ""}
        if args == ["start", "debian-vm"]:
            return {"ok": True, "stdout": "started", "stderr": ""}
        return {"ok": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(vms, "_domain_exists", lambda name: False)
    monkeypatch.setattr(vms, "_ensure_network", lambda network: {"ok": True})
    monkeypatch.setattr(vms, "_run", fake_run)
    monkeypatch.setattr(vms, "_virsh", fake_virsh)
    monkeypatch.setattr(vms, "_wait_for_domain", lambda name: name == "debian-vm")
    monkeypatch.setattr(vms, "ISO_DIR", str(tmp_path))

    result = vms.create_vm("debian-vm", 2048, 2, 20, "", "default")

    assert result["ok"] is True
    assert result["visible"] is True
    assert result["started"] is True
    assert calls[0][0] == "qemu-img"
    assert calls[1][0] == "define"
    assert calls[2] == ["start", "debian-vm"]


def test_list_vms_falls_back_to_virsh(monkeypatch):
    monkeypatch.setattr(vms, "HAS_LIBVIRT", False)

    def fake_virsh(args, timeout=120):
        if args == ["list", "--all"]:
            return {
                "ok": True,
                "stdout": "\n Id   Name        State\n---------------------------\n -    debian-vm   shut off\n",
                "stderr": "",
            }
        if args == ["dominfo", "debian-vm"]:
            return {
                "ok": True,
                "stdout": (
                    "Id:             -\n"
                    "Name:           debian-vm\n"
                    "UUID:           12345678-1234-5678-1234-567812345678\n"
                    "State:          shut off\n"
                    "CPU(s):         2\n"
                    "Max memory:     2097152 KiB\n"
                    "Used memory:    0 KiB\n"
                    "Autostart:      disable\n"
                ),
                "stderr": "",
            }
        return {"ok": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)

    listed = vms.list_vms()

    assert listed == [{
        "name": "debian-vm",
        "uuid": "12345678-1234-5678-1234-567812345678",
        "state": "shut off",
        "active": False,
        "autostart": False,
        "max_mem": 2097152 * 1024,
        "mem": 0,
        "vcpus": 2,
    }]


def test_list_vms_uses_virsh_when_libvirt_is_empty(monkeypatch):
    monkeypatch.setattr(vms, "HAS_LIBVIRT", True)
    monkeypatch.setattr(vms, "_connect", lambda: _EmptyConn())

    def fake_virsh(args, timeout=120):
        if args == ["list", "--all"]:
            return {
                "ok": True,
                "stdout": "\n Id   Name        State\n---------------------------\n 1    fallback-vm running\n",
                "stderr": "",
            }
        if args == ["dominfo", "fallback-vm"]:
            return {
                "ok": True,
                "stdout": "Name: fallback-vm\nState: running\nCPU(s): 1\nMax memory: 1048576 KiB\nUsed memory: 524288 KiB\n",
                "stderr": "",
            }
        return {"ok": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)

    listed = vms.list_vms()

    assert [vm["name"] for vm in listed] == ["fallback-vm"]


def test_virsh_uses_system_uri(monkeypatch):
    seen = {}

    def fake_run(args, capture_output, text, timeout):
        seen["args"] = args
        class Result:
            returncode = 0
            stdout = ""
            stderr = ""
        return Result()

    monkeypatch.setattr(vms.subprocess, "run", fake_run)

    assert vms._virsh(["list", "--all"])["ok"] is True
    assert seen["args"][:3] == ["virsh", "-c", "qemu:///system"]


def test_default_network_falls_back_to_runvard_nat(monkeypatch):
    calls = []

    def fake_virsh(args, timeout=120):
        calls.append(args)
        if args == ["net-info", "default"]:
            return {"ok": True, "stdout": "Name: default\nActive: no\n", "stderr": ""}
        if args == ["net-start", "default"]:
            return {"ok": False, "stdout": "", "stderr": "Address already in use"}
        if args == ["net-info", "runvard123"]:
            return {"ok": False, "stdout": "", "stderr": "network not found"}
        if args[0] == "net-define":
            assert os.path.exists(args[1])
            xml = open(args[1], encoding="utf-8").read()
            assert "<name>runvard123</name>" in xml
            assert 'name="rvbr123"' in xml
            assert 'address="192.168.123.1"' in xml
            return {"ok": True, "stdout": "defined", "stderr": ""}
        if args == ["net-start", "runvard123"]:
            return {"ok": True, "stdout": "started", "stderr": ""}
        if args == ["net-autostart", "runvard123"]:
            return {"ok": True, "stdout": "", "stderr": ""}
        return {"ok": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)

    result = vms._ensure_network("default")

    assert result["ok"] is True
    assert result["network"] == "runvard123"
    assert ["net-start", "default"] in calls
    assert ["net-start", "runvard123"] in calls
