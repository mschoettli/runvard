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

    def XMLDesc(self, flags=0):
        return "<domain><memory unit=\"KiB\">2048</memory><vcpu>2</vcpu></domain>"


class _BrokenDomain:
    def state(self):
        raise RuntimeError("domain disappeared")


class _Conn:
    def listAllDomains(self):
        return [_BrokenDomain(), _GoodDomain()]

    def lookupByName(self, name):
        if name != "debian-vm":
            raise RuntimeError("not found")
        return _GoodDomain()


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


def test_create_vm_can_define_networkless_domain(monkeypatch, tmp_path):
    calls = []

    def fake_run(args, timeout=120):
        calls.append(args)
        return {"ok": True, "stdout": "disk created", "stderr": ""}

    def fake_virsh(args, timeout=120):
        calls.append(args)
        if args[0] == "define":
            xml = open(args[1], encoding="utf-8").read()
            assert "<interface" not in xml
            assert 'dev="network"' not in xml
            return {"ok": True, "stdout": "defined", "stderr": ""}
        if args == ["start", "offline-vm"]:
            return {"ok": True, "stdout": "started", "stderr": ""}
        return {"ok": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(vms, "_domain_exists", lambda name: False)
    monkeypatch.setattr(
        vms,
        "_ensure_network",
        lambda network: {"ok": True, "network": "", "warning": "creating VM without network"},
    )
    monkeypatch.setattr(vms, "_run", fake_run)
    monkeypatch.setattr(vms, "_virsh", fake_virsh)
    monkeypatch.setattr(vms, "_wait_for_domain", lambda name: True)
    monkeypatch.setattr(vms, "ISO_DIR", str(tmp_path))

    result = vms.create_vm("offline-vm", 2048, 2, 20, "", "default")

    assert result["ok"] is True
    assert result["network"] == ""
    assert result["warning"] == "creating VM without network"
    assert calls[1][0] == "define"


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


def test_update_resources_persists_and_attempts_live_for_running_vm(monkeypatch):
    calls = []
    defined_xml = {}

    def fake_virsh(args, timeout=120):
        calls.append(args)
        if args == ["dumpxml", "--inactive", "debian-vm"]:
            return {
                "ok": True,
                "stdout": (
                    "<domain><name>debian-vm</name><memory unit=\"MiB\">2048</memory>"
                    "<currentMemory unit=\"MiB\">2048</currentMemory><vcpu>2</vcpu></domain>"
                ),
                "stderr": "",
            }
        if args[0] == "define":
            defined_xml["text"] = open(args[1], encoding="utf-8").read()
            return {"ok": True, "stdout": "defined", "stderr": ""}
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)
    monkeypatch.setattr(vms, "_domain_active", lambda name: True)

    result = vms.update_resources("debian-vm", 4096, 4)

    assert result["ok"] is True
    assert "<memory unit=\"MiB\">4096</memory>" in defined_xml["text"]
    assert "<currentMemory unit=\"MiB\">4096</currentMemory>" in defined_xml["text"]
    assert "<vcpu>4</vcpu>" in defined_xml["text"]
    assert calls == [
        ["dumpxml", "--inactive", "debian-vm"],
        ["define", calls[1][1]],
        ["setmem", "debian-vm", "4096M", "--live"],
        ["setvcpus", "debian-vm", "4", "--live"],
    ]


def test_domain_summary_prefers_config_resources(monkeypatch):
    monkeypatch.setattr(vms, "HAS_LIBVIRT", False)

    def fake_virsh(args, timeout=120):
        if args == ["dominfo", "debian-vm"]:
            return {
                "ok": True,
                "stdout": "Name: debian-vm\nState: running\nCPU(s): 2\nMax memory: 2097152 KiB\nUsed memory: 1048576 KiB\n",
                "stderr": "",
            }
        if args == ["dumpxml", "--inactive", "debian-vm"]:
            return {
                "ok": True,
                "stdout": "<domain><memory unit=\"MiB\">4096</memory><vcpu>4</vcpu></domain>",
                "stderr": "",
            }
        return {"ok": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)

    result = vms._domain_summary_virsh("debian-vm", "running")

    assert result["max_mem"] == 4096 * 1024 * 1024
    assert result["vcpus"] == 4


def test_domain_summary_libvirt_prefers_virsh_inactive_config(monkeypatch):
    monkeypatch.setattr(vms, "HAS_LIBVIRT", True)

    def fake_virsh(args, timeout=120):
        if args == ["dumpxml", "--inactive", "debian-vm"]:
            return {
                "ok": True,
                "stdout": "<domain><memory unit=\"MiB\">8192</memory><vcpu>4</vcpu></domain>",
                "stderr": "",
            }
        return {"ok": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)

    result = vms._domain_summary_libvirt(_GoodDomain())

    assert result["max_mem"] == 8192 * 1024 * 1024
    assert result["vcpus"] == 4


def test_list_hardware_prefers_saved_config_resources(monkeypatch):
    monkeypatch.setattr(vms, "HAS_LIBVIRT", True)
    monkeypatch.setattr(vms, "_connect", lambda: _Conn())

    def fake_virsh(args, timeout=120):
        if args == ["dumpxml", "--inactive", "debian-vm"]:
            return {
                "ok": True,
                "stdout": "<domain><memory unit=\"MiB\">8192</memory><vcpu>4</vcpu></domain>",
                "stderr": "",
            }
        return {"ok": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)

    result = vms.list_hardware("debian-vm")

    assert result["memory_mb"] == 8192
    assert result["current_memory_mb"] == 1
    assert result["vcpus"] == 4


def test_resize_disk_rejects_shrinking(monkeypatch):
    monkeypatch.setattr(vms, "_disk_source_for_target", lambda name, target: "/vm/disk.qcow2")
    monkeypatch.setattr(
        vms,
        "_run",
        lambda args, timeout=120: {
            "ok": True,
            "stdout": '{"virtual-size": 21474836480}',
            "stderr": "",
        },
    )

    result = vms.resize_disk("debian-vm", "vda", 20)

    assert result["ok"] is False
    assert "groesser" in result["stderr"]


def test_resize_disk_runs_qemu_img_resize(monkeypatch):
    calls = []

    def fake_run(args, timeout=120):
        calls.append(args)
        if args[:3] == ["qemu-img", "info", "--output=json"]:
            return {"ok": True, "stdout": '{"virtual-size": 10737418240}', "stderr": ""}
        if args[:2] == ["qemu-img", "resize"]:
            return {"ok": True, "stdout": "resized", "stderr": ""}
        return {"ok": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(vms, "_disk_source_for_target", lambda name, target: "/vm/disk.qcow2")
    monkeypatch.setattr(vms, "_run", fake_run)

    result = vms.resize_disk("debian-vm", "vda", 30)

    assert result["ok"] is True
    assert calls[-1] == ["qemu-img", "resize", "/vm/disk.qcow2", "30G"]


def test_diagnostics_exposes_raw_virsh_and_parsed_vms(monkeypatch, tmp_path):
    monkeypatch.setattr(vms, "HAS_LIBVIRT", False)
    monkeypatch.setattr(vms, "ISO_DIR", str(tmp_path))
    (tmp_path / "installer.iso").write_text("iso")

    def fake_virsh(args, timeout=120):
        if args == ["list", "--all"]:
            return {
                "ok": True,
                "stdout": "\n Id   Name      State\n-------------------------\n -    diag-vm   shut off\n",
                "stderr": "",
            }
        if args == ["dominfo", "diag-vm"]:
            return {
                "ok": True,
                "stdout": "Name: diag-vm\nState: shut off\nCPU(s): 2\n",
                "stderr": "",
            }
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)

    diag = vms.diagnostics()

    assert diag["uri"] == "qemu:///system"
    assert diag["has_libvirt_python"] is False
    assert "diag-vm" in diag["virsh"]["list_all"]["stdout"]
    assert [vm["name"] for vm in diag["parsed_vms"]] == ["diag-vm"]
    assert diag["iso_dir_entries"] == ["installer.iso"]


def test_default_network_dns_conflict_falls_back_to_no_nic(monkeypatch):
    calls = []

    def fake_virsh(args, timeout=120):
        calls.append(args)
        if args == ["net-info", "default"]:
            return {"ok": True, "stdout": "Name: default\nActive: no\n", "stderr": ""}
        if args == ["net-start", "default"]:
            return {"ok": False, "stdout": "", "stderr": "failed to create listening socket for 192.168.122.1: Address already in use"}
        return {"ok": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)

    result = vms._ensure_network("default")

    assert result["ok"] is True
    assert result["network"] == ""
    assert "without network" in result["warning"]
    assert ["net-start", "default"] in calls
    assert not any(call and call[0] == "net-define" for call in calls)


def test_attach_nic_ensures_libvirt_network(monkeypatch):
    calls = []

    monkeypatch.setattr(vms, "_ensure_network", lambda network: {"ok": True, "network": "default"})
    monkeypatch.setattr(vms, "_scope_flags", lambda name: ["--config"])

    def fake_virsh(args, timeout=120):
        calls.append(args)
        return {"ok": True, "stdout": "attached", "stderr": ""}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)

    result = vms.attach_nic("debian-vm", "default", "virtio")

    assert result["ok"] is True
    assert calls == [[
        "attach-interface", "debian-vm", "network", "default",
        "--model", "virtio", "--config",
    ]]


def test_attach_nic_reports_unavailable_default_network(monkeypatch):
    monkeypatch.setattr(
        vms,
        "_ensure_network",
        lambda network: {"ok": True, "network": "", "warning": "creating VM without network"},
    )

    result = vms.attach_nic("debian-vm", "default", "virtio")

    assert result == {"ok": False, "stderr": "creating VM without network"}


def test_attach_nic_can_attach_host_bridge(monkeypatch):
    calls = []

    monkeypatch.setattr(vms, "_ensure_network", lambda network: (_ for _ in ()).throw(AssertionError("unexpected")))
    monkeypatch.setattr(vms, "_scope_flags", lambda name: ["--config", "--live"])

    def fake_virsh(args, timeout=120):
        calls.append(args)
        return {"ok": True, "stdout": "attached", "stderr": ""}

    monkeypatch.setattr(vms, "_virsh", fake_virsh)

    result = vms.attach_nic("debian-vm", "br0", "e1000", "bridge")

    assert result["ok"] is True
    assert calls == [[
        "attach-interface", "debian-vm", "bridge", "br0",
        "--model", "e1000", "--config", "--live",
    ]]


def test_attach_nic_rejects_invalid_bridge(monkeypatch):
    result = vms.attach_nic("debian-vm", "../br0", "virtio", "bridge")

    assert result == {"ok": False, "stderr": "Ungueltige Bridge"}
