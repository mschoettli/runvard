from modules import files


def test_file_mount_inventory(monkeypatch):
    monkeypatch.setattr(files, "_device_source", lambda number, source: "/dev/sdb1" if number == "8:17" else source)
    monkeypatch.setattr(files, "_disk_model", lambda source: "SanDisk")
    lines = r"""11 1 8:2 / / rw - ext4 /dev/sda2 rw
12 1 8:1 / /boot/efi rw - vfat /dev/sda1 rw
13 1 8:17 / /media/Backup\040Disk rw - ext4 /dev/target rw
14 1 8:33 / /mnt/sdb rw - ext4 /dev/sdc1 rw
15 1 8:33 /apps /srv/appdata rw - ext4 /dev/sdc1 rw
16 1 0:9 / /mnt/network rw - cifs //nas/share rw
17 1 0:3 / /proc rw - proc proc rw
18 1 8:17 / /srv/old rw - ext4 /dev/sdb1 rw
19 1 0:5 / /srv/old rw - tmpfs tmpfs rw""".splitlines()
    mounts = {m["mountpoint"]: m for m in files._parse_mounts(lines, {"/dev/sdb1": "Backup"})}
    assert mounts.keys() == {"/media/Backup Disk", "/mnt/sdb", "/srv/appdata", "/mnt/network"}
    assert mounts["/media/Backup Disk"]["label"] == "Backup"
    assert mounts["/mnt/sdb"]["source"] == "/dev/sdc1"
    assert mounts["/mnt/sdb"]["label"] == "SanDisk"
    assert mounts["/srv/appdata"]["root"] == "/apps"
    assert mounts["/srv/appdata"]["device"] == mounts["/mnt/sdb"]["device"]
    assert mounts["/mnt/network"]["type"] == "SMB"
