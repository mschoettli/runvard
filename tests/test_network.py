from modules import network


def test_classify_physical_interface_by_sysfs_device(monkeypatch):
    monkeypatch.setattr(network.os.path, "exists", lambda path: path.endswith("/enp3s0/device"))

    interface_type, label = network._classify_interface("enp3s0")

    assert interface_type == "physical"
    assert label == "Port"


def test_classify_virtual_interfaces_before_sysfs_fallback(monkeypatch):
    monkeypatch.setattr(network.os.path, "exists", lambda path: True)

    assert network._classify_interface("docker0") == ("bridge", "Bridge")
    assert network._classify_interface("veth1234") == ("container", "Container")
    assert network._classify_interface("eth0.10") == ("vlan", "VLAN")
    assert network._classify_interface("bond0") == ("bond", "Bond")


def test_classify_unknown_without_device_as_logical(monkeypatch):
    monkeypatch.setattr(network.os.path, "exists", lambda path: False)

    interface_type, label = network._classify_interface("dummy0")

    assert interface_type == "logical"
    assert label == "Logical"
