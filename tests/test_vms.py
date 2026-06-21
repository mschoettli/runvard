import subprocess

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


def test_list_vms_skips_unreadable_domains(monkeypatch):
    monkeypatch.setattr(vms, "HAS_LIBVIRT", True)
    monkeypatch.setattr(vms, "_connect", lambda: _Conn())

    listed = vms.list_vms()

    assert [vm["name"] for vm in listed] == ["debian-vm"]
    assert listed[0]["active"] is True
    assert listed[0]["max_mem"] == 2048 * 1024


def test_create_vm_reports_visible_domain(monkeypatch):
    def fake_run(cmd, capture_output, text, timeout):
        assert cmd[:3] == ["virt-install", "--name", "debian-vm"]
        assert timeout == 600
        return subprocess.CompletedProcess(cmd, 0, "created", "")

    monkeypatch.setattr(vms.subprocess, "run", fake_run)
    monkeypatch.setattr(vms, "_wait_for_domain", lambda name: name == "debian-vm")

    result = vms.create_vm("debian-vm", 2048, 2, 20, "", "default")

    assert result == {"ok": True, "output": "created", "visible": True}


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
