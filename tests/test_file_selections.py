import errno
import json
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNVARD_DATA_DIR", str(tmp_path))
    client = TestClient(server.app)
    client.cookies.set(server.COOKIE_NAME, server.make_token("tester", 3600, "admin", account_salt=None))
    return client


@pytest.mark.parametrize("structured", [False, True])
def test_zip_preserves_selected_paths(client, tmp_path, structured):
    paths = [tmp_path / ("été|2026.txt" if structured else "été.txt"), tmp_path / "B's.txt"]
    for path in paths:
        path.write_text(path.name)
    data = {"paths_json": json.dumps(list(map(str, paths)))} if structured else {"paths": "|".join(map(str, paths))}
    output = tmp_path / "selected.zip"
    response = client.post("/api/files/zip", data={**data, "output": str(output)})
    assert response.status_code == 200
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == [path.name for path in paths]


def test_zip_preserves_search_results_with_identical_names(client, tmp_path):
    first = tmp_path / "A" / "notes.txt"
    second = tmp_path / "B" / "notes.txt"
    for path in [first, second]:
        path.parent.mkdir()
        path.write_text(path.parent.name)
    (first.parent / "empty").mkdir()
    output = tmp_path / "results.zip"
    # A recursive search can select a parent and its child at the same time.
    selected = [str(first), str(second), str(first.parent)]
    response = client.post("/api/files/zip", data={"paths_json": json.dumps(selected), "output": str(output)})
    assert response.status_code == 200
    with zipfile.ZipFile(output) as archive:
        assert sorted(archive.namelist()) == ["A/empty/", "A/notes.txt", "B/notes.txt"]
        assert archive.read("A/notes.txt") == b"A"
        assert archive.read("B/notes.txt") == b"B"


@pytest.mark.parametrize("field,action,paths", [
    ("paths", "files:delete", ["/example/first", "/example/second"]),
    ("paths_json", "files:delete-selection", ["/example/Januar|Februar", "/example/second"]),
])
def test_delete_job_confirms_exact_selection(client, monkeypatch, field, action, paths):
    monkeypatch.setattr(server.files, "start_job", lambda action, paths, dst, output: {"paths": paths})
    payload = json.dumps(paths) if field == "paths_json" else "|".join(paths)
    form = {field: payload, "action": "delete"}
    for target, status in [(payload + "changed", 403), (payload, 200)]:
        token = client.post("/api/confirm-token", data={"action": action, "target": target}).json()["token"]
        response = client.post("/api/files/job", data={**form, "confirm_token": token})
        assert response.status_code == status
    assert response.json()["paths"] == paths


@pytest.mark.parametrize("selection", [
    {"paths_json": "null"}, {"paths_json": '[""]'}, {"paths_json": "invalid"},
    {"paths_json": '["/one"]', "paths": "/two"},
])
def test_invalid_selection_is_rejected(client, selection):
    assert client.post("/api/files/job", data={**selection, "action": "copy"}).status_code == 422


@pytest.mark.parametrize("kind", ["file", "directory", "dangling-link"])
def test_trash_restore_keeps_reused_names(client, tmp_path, monkeypatch, kind):
    trash = tmp_path / "trash"
    monkeypatch.setattr(server.files, "TRASH", str(trash))
    monkeypatch.setattr(server.files, "TRASHMETA", str(trash / ".meta.json"))
    original = tmp_path / "work"
    if kind == "directory":
        original.mkdir()
        (original / "original.txt").write_text("original")
    else:
        original.write_text("original")
    server.files.delete(str(original))
    item, = server.files.list_trash()
    if kind == "directory":
        original.mkdir()
    elif kind == "dangling-link":
        original.symlink_to(tmp_path / "missing")
    else:
        original.write_text("current")
    (tmp_path / "work copy").write_text("other")

    response = client.post("/api/files/trash/restore", data={"item_id": item["id"]})
    restored = tmp_path / "work copy 2"
    assert response.status_code == 200
    assert response.json()["dst"] == str(restored)
    assert (tmp_path / "work copy").read_text() == "other"
    if kind == "directory":
        assert list(original.iterdir()) == []
        assert (restored / "original.txt").read_text() == "original"
    else:
        assert restored.read_text() == "original"
        if kind == "dangling-link":
            assert original.is_symlink() and not original.exists()
        else:
            assert original.read_text() == "current"
    assert server.files.list_trash() == []


@pytest.mark.parametrize("directory", [False, True])
def test_cross_filesystem_restore_publishes_complete_copy(tmp_path, monkeypatch, directory):
    trash = tmp_path / "trash"
    trash.mkdir()
    item = trash / "saved"
    if directory:
        item.mkdir()
        (item / "notes").write_text("saved")
    else:
        item.write_text("saved")
    dst = tmp_path / "published"
    replace = os.replace

    def rename_across_mounts(source, target):
        if os.fspath(source) == str(item):
            raise OSError(errno.EXDEV, "Cross-device link")
        replace(source, target)

    monkeypatch.setattr(os, "replace", rename_across_mounts)
    assert server.files._restore_to_free_path(str(item), str(dst)) == str(dst)
    assert (dst / "notes" if directory else dst).read_text() == "saved"
    assert not item.exists()


@pytest.mark.parametrize("raw", [b"#!/bin/sh\nexit 0\n", b"hello\r\nworld\r\n",
                                  b"\xef\xbb\xbfhello\n", b"hello\rworld\r"])
def test_browser_save_preserves_file_format(client, tmp_path, raw):
    path = tmp_path / "script"
    path.write_bytes(raw)
    path.chmod(0o750)
    read = client.get("/api/files/read", params={"path": str(path)}).json()
    form_text = read["content"].replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")
    result = client.post("/api/files/write", data={"path": str(path), "content": form_text,
                                                   "revision": read["revision"]})
    assert result.status_code == 200 and path.read_bytes() == raw
    changed = client.post("/api/files/write", data={"path": str(path), "content": "x" + form_text,
                                                    "revision": result.json()["revision"]})
    assert changed.status_code == 200
    expected = raw.replace(b"hello", b"xhello", 1) if b"hello" in raw else b"x" + raw
    assert path.read_bytes() == expected
    assert path.stat().st_mode & 0o777 == 0o750
    stale = client.post("/api/files/write", data={"path": str(path), "content": "stale", "revision": read["revision"]})
    assert stale.status_code == 409
    assert b"stale" not in path.read_bytes()


@pytest.mark.parametrize("data,reason", [(b"caf\xe9", "unsupportedEncoding"), (b"first\nsecond\r\n", "mixedEndings")])
def test_file_editor_protects_formats_it_cannot_roundtrip(client, tmp_path, data, reason):
    path = tmp_path / "notes"
    path.write_bytes(data)
    preview = client.get("/api/files/read", params={"path": str(path)}).json()
    assert preview["readonly"] and preview["reason"] == reason
    response = client.post("/api/files/write", data={"path": str(path), "content": preview["content"],
                                                     "revision": preview["revision"]})
    assert response.status_code == 409 and path.read_bytes() == data


def test_rename_and_unpack_preserve_existing_files(client, tmp_path):
    original, existing = tmp_path / "A", tmp_path / "B"
    original.write_text("alpha")
    existing.write_text("beta")
    assert client.post("/api/files/rename", data={"path": str(original), "new_name": "B"}).status_code == 409
    assert original.read_text() == "alpha" and existing.read_text() == "beta"
    archive = tmp_path / "docs.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("free.txt", "new")
        stream.writestr("B", "overwrite")
    assert client.post("/api/files/unzip", data={"path": str(archive), "dst_dir": str(tmp_path)}).status_code == 409
    assert not (tmp_path / "free.txt").exists() and existing.read_text() == "beta"
    existing.unlink()
    assert client.post("/api/files/unzip", data={"path": str(archive), "dst_dir": str(tmp_path)}).status_code == 200
    assert existing.read_text() == "overwrite" and (tmp_path / "free.txt").read_text() == "new"


def test_directory_link_downloads_zip_with_regular_contents(client, tmp_path, monkeypatch):
    import io
    folder = tmp_path / "Données"
    folder.mkdir()
    (folder / "empty").mkdir()
    (folder / "file").write_bytes(b"contents")
    (folder / "link").symlink_to(tmp_path / "outside")
    (tmp_path / "outside").write_bytes(b"unshared")
    monkeypatch.setattr(server.files, "SHAREDB", str(tmp_path / "shares.json"))
    share = client.post("/api/files/share", data={"path": str(folder)}).json()
    result = TestClient(server.app).get("/dl/" + share["token"])
    assert result.status_code == 200 and result.headers["content-type"] == "application/zip"
    assert result.headers["content-disposition"].endswith("Donn%C3%A9es.zip")
    with zipfile.ZipFile(io.BytesIO(result.content)) as archive:
        assert set(archive.namelist()) == {"empty/", "file"}
        assert archive.read("file") == b"contents"


def test_zip_creation_keeps_existing_archive(client, tmp_path):
    source, archive = tmp_path / "report", tmp_path / "report.zip"
    source.write_text("new")
    archive.write_bytes(b"existing archive")
    result = client.post("/api/files/zip", data={"paths_json": json.dumps([str(source)]), "output": str(archive)})
    assert result.status_code == 409 and archive.read_bytes() == b"existing archive"



def test_edits_to_hardlinks_in_different_directories_share_one_revision(client, tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    left = tmp_path / "left" / "notes"
    right = tmp_path / "right" / "notes"
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_text("original")
    os.link(left, right)
    revision = client.get("/api/files/read", params={"path": str(left)}).json()["revision"]
    inode = left.stat().st_ino
    lock_attempts = []
    ready = Barrier(2)
    flock = server.files.fcntl.flock

    def synchronize_writers(fd, operation):
        if os.fstat(fd).st_ino == inode and operation == server.files.fcntl.LOCK_EX:
            lock_attempts.append(fd)
            ready.wait(timeout=5)
        return flock(fd, operation)

    monkeypatch.setattr(server.files.fcntl, "flock", synchronize_writers)

    def save(path):
        return client.post("/api/files/write", data={
            "path": str(path), "content": path.parent.name, "revision": revision,
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, (left, right)))
    assert len(lock_attempts) == 2
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = "left" if responses[0].status_code == 200 else "right"
    assert left.read_text() == right.read_text() == winner
    assert left.samefile(right)
