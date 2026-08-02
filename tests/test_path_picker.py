import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server


def _picker():
    picker = getattr(server, "path_picker", None)
    assert picker is not None, "the shared path_picker module is not wired into server"
    return picker


def _client(*, expert=False, role="admin"):
    client = TestClient(server.app)
    client.cookies.set(
        server.COOKIE_NAME,
        server.make_token("tester", 3600, role, expert=expert),
    )
    return client


def _safe_picker(tmp_path, monkeypatch):
    picker = _picker()
    safe = tmp_path / "mnt"
    safe.mkdir()
    monkeypatch.setattr(picker, "BASE_SAFE_ROOTS", (str(safe),))
    monkeypatch.setattr(picker, "PURPOSE_EXTRA_ROOTS", {})
    monkeypatch.setattr(picker.psutil, "disk_partitions", lambda all=True: [])
    return picker, safe


def test_normal_mode_lists_only_safe_storage_roots(tmp_path, monkeypatch):
    picker, safe = _safe_picker(tmp_path, monkeypatch)

    result = picker.list_roots("share", expert=False)

    assert [row["path"] for row in result["roots"]] == [str(safe)]
    assert result["expert"] is False
    assert result["manual"] is False


def test_normal_mode_does_not_promote_unrelated_system_mounts(tmp_path, monkeypatch):
    picker, safe = _safe_picker(tmp_path, monkeypatch)
    system_mount = tmp_path / "system-volume"
    system_mount.mkdir()
    monkeypatch.setattr(
        picker.psutil,
        "disk_partitions",
        lambda all=True: [SimpleNamespace(mountpoint=str(system_mount))],
    )

    result = picker.list_roots("share", expert=False)

    assert [row["path"] for row in result["roots"]] == [str(safe)]


def test_normal_mode_rejects_symlink_escape_from_safe_root(tmp_path, monkeypatch):
    picker, safe = _safe_picker(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (safe / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PermissionError, match="allowed"):
        picker.browse(str(safe / "escape"), "share", "folder", expert=False)


def test_file_mode_filters_vm_images_but_keeps_directories(tmp_path, monkeypatch):
    picker, safe = _safe_picker(tmp_path, monkeypatch)
    (safe / "images").mkdir()
    (safe / "disk.qcow2").write_text("image")
    (safe / "notes.txt").write_text("notes")

    result = picker.browse(str(safe), "vm-image", "file", expert=False)

    assert [(row["name"], row["type"]) for row in result["entries"]] == [
        ("images", "folder"),
        ("disk.qcow2", "file"),
    ]


def test_device_mode_uses_storage_device_payload_and_hides_protected_disks(monkeypatch):
    picker = _picker()
    monkeypatch.setattr(
        picker.storage,
        "list_block_devices",
        lambda: {"devices": [
            {"name": "sda", "protected": True},
            {"name": "sdb", "protected": False, "size": 4096},
        ]},
    )

    result = picker.browse("/dev", "vm-image", "device", expert=True)

    assert result["entries"] == [
        {"name": "sdb", "path": "/dev/sdb", "type": "device", "size": 4096},
    ]


def test_folder_creation_rejects_path_segments_in_name(tmp_path, monkeypatch):
    picker, safe = _safe_picker(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="name"):
        picker.create_folder(str(safe), "../escape", "share", expert=False)


def test_path_picker_api_uses_signed_session_expert_state(tmp_path, monkeypatch):
    picker, safe = _safe_picker(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "login_enabled", lambda: True)

    normal = _client().get(
        "/api/path-picker/roots", params={"purpose": "share", "mode": "folder"},
    )
    expert = _client(expert=True).get(
        "/api/path-picker/roots", params={"purpose": "share", "mode": "folder"},
    )

    assert normal.status_code == 200
    assert normal.json()["roots"][0]["path"] == str(safe)
    assert normal.json()["manual"] is False
    assert expert.status_code == 200
    assert expert.json()["expert"] is True
    assert expert.json()["manual"] is True
    assert any(row["path"] == "/" for row in expert.json()["roots"])


def test_path_picker_api_blocks_normal_browsing_outside_safe_roots(tmp_path, monkeypatch):
    _picker_module, _safe = _safe_picker(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "login_enabled", lambda: True)

    response = _client().get(
        "/api/path-picker/browse",
        params={"path": str(tmp_path), "purpose": "share", "mode": "folder"},
    )

    assert response.status_code == 403


def test_path_picker_create_folder_requires_admin(tmp_path, monkeypatch):
    _picker_module, safe = _safe_picker(tmp_path, monkeypatch)
    monkeypatch.setattr(server, "login_enabled", lambda: True)

    response = _client(role="readonly").post(
        "/api/path-picker/folder",
        data={"parent": str(safe), "name": "Backups", "purpose": "share"},
    )

    assert response.status_code == 403


def test_path_picker_validation_reports_type_and_write_state(tmp_path, monkeypatch):
    picker, safe = _safe_picker(tmp_path, monkeypatch)

    result = picker.validate(str(safe), "share", "folder", expert=False)

    assert result["ok"] is True
    assert result["type"] == "folder"
    assert result["readable"] is True
    assert "writable" in result


def test_vm_image_validation_rejects_wrong_file_extension(tmp_path, monkeypatch):
    picker, safe = _safe_picker(tmp_path, monkeypatch)
    image = safe / "disk.qcow2"
    note = safe / "notes.txt"
    image.write_text("image")
    note.write_text("notes")

    assert picker.validate(str(image), "vm-image", "file", expert=False)["ok"] is True
    assert picker.validate(str(note), "vm-image", "file", expert=False)["ok"] is False


def test_destination_validation_requires_write_access(tmp_path, monkeypatch):
    picker, safe = _safe_picker(tmp_path, monkeypatch)
    original_access = picker.os.access
    monkeypatch.setattr(
        picker.os,
        "access",
        lambda path, mode: False if mode == os.W_OK else original_access(path, mode),
    )

    result = picker.validate(str(safe), "backup-destination", "folder", expert=False)

    assert result["writable"] is False
    assert result["ok"] is False
