import json
import zipfile

import pytest
from fastapi.testclient import TestClient

import server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNVARD_DATA_DIR", str(tmp_path))
    client = TestClient(server.app)
    client.cookies.set(server.COOKIE_NAME, server.make_token("tester", 3600, "admin"))
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
