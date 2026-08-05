import importlib.util
import io
import tarfile

import pytest


SPEC = importlib.util.spec_from_file_location(
    "verify_release_archive", "scripts/verify-release-archive.py"
)
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def _archive(path, entries):
    with tarfile.open(path, "w:gz") as tar:
        for name, kind, value in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                data = value.encode()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                info.type = tarfile.SYMTYPE
                info.linkname = value
                tar.addfile(info)


def test_valid_release_archive(tmp_path):
    path = tmp_path / "release.tar.gz"
    _archive(path, [("runvard-v1/server.py", "file", "ok"),
                    ("runvard-v1/scripts/install-full.sh", "file", "ok")])
    assert VERIFIER.validate(str(path)) == "runvard-v1"


@pytest.mark.parametrize("name", ["../escape", "/absolute"])
def test_release_archive_rejects_path_traversal(tmp_path, name):
    path = tmp_path / "bad.tar.gz"
    _archive(path, [(name, "file", "bad")])
    with pytest.raises(ValueError, match="unsafe archive path"):
        VERIFIER.validate(str(path))


def test_release_archive_rejects_dangerous_symlink(tmp_path):
    path = tmp_path / "bad-link.tar.gz"
    _archive(path, [("runvard-v1/server.py", "file", "ok"),
                    ("runvard-v1/scripts/install-full.sh", "file", "ok"),
                    ("runvard-v1/link", "link", "../../etc/shadow")])
    with pytest.raises(ValueError, match="unsafe archive link"):
        VERIFIER.validate(str(path))


def test_remote_installer_requires_version_attestation_and_checksum():
    text = open("install.sh", encoding="utf-8").read()
    assert "--version" in text
    assert "gh attestation verify" in text
    assert "sha256sum" in text
    assert "curl -fsSL \"$ARCHIVE_URL\" -o" in text
    assert "curl -fsSL \"$ARCHIVE_URL\" | tar" not in text
