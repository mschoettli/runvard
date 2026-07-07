from types import SimpleNamespace

from modules import system


def test_get_disk_usage_includes_mounted_data_and_network_disks(monkeypatch):
    partitions = [
        SimpleNamespace(device="/dev/sda1", mountpoint="/", fstype="ext4"),
        SimpleNamespace(device="/dev/sdb1", mountpoint="/mnt/media", fstype="ext4"),
        SimpleNamespace(device="//nas/media", mountpoint="/mnt/nas", fstype="cifs"),
    ]

    def fake_disk_usage(mountpoint):
        return SimpleNamespace(total=1000, used=250, free=750, percent=25)

    monkeypatch.setattr(system.psutil, "disk_partitions", lambda all=True: partitions)
    monkeypatch.setattr(system.psutil, "disk_usage", fake_disk_usage)

    disks = system.get_disk_usage()

    assert [d["mountpoint"] for d in disks] == ["/", "/mnt/media", "/mnt/nas"]


def test_get_disk_usage_filters_pseudo_and_docker_mounts(monkeypatch):
    partitions = [
        SimpleNamespace(device="proc", mountpoint="/proc", fstype="proc"),
        SimpleNamespace(device="tmpfs", mountpoint="/run", fstype="tmpfs"),
        SimpleNamespace(device="overlay", mountpoint="/var/lib/docker/overlay2/x", fstype="overlay"),
        SimpleNamespace(device="/dev/sdb1", mountpoint="/mnt/data", fstype="xfs"),
    ]

    def fake_disk_usage(mountpoint):
        return SimpleNamespace(total=1000, used=250, free=750, percent=25)

    monkeypatch.setattr(system.psutil, "disk_partitions", lambda all=True: partitions)
    monkeypatch.setattr(system.psutil, "disk_usage", fake_disk_usage)

    disks = system.get_disk_usage()

    assert [d["mountpoint"] for d in disks] == ["/mnt/data"]
