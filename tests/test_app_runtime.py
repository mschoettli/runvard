"""FastAPI runtime checks for runvard.

These tests are skipped when the application dependencies are not installed.
They exercise the real ASGI app with a temporary data directory.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
except ImportError:  # pragma: no cover - dependency-light environments.
    TestClient = None
    WebSocketDisconnect = None


@unittest.skipIf(TestClient is None, "FastAPI dependencies are not installed")
class FastApiRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls._old_data = os.environ.get("RUNVARD_DATA_DIR")
        cls._old_user = os.environ.get("RUNVARD_USER")
        cls._old_pass = os.environ.get("RUNVARD_PASS")
        os.environ["RUNVARD_DATA_DIR"] = cls._tmp.name
        os.environ["RUNVARD_USER"] = "admin"
        os.environ["RUNVARD_PASS"] = "runvard"
        sys.modules.pop("server", None)
        cls.server = importlib.import_module("server")
        cls.client = TestClient(cls.server.app)

    def setUp(self) -> None:
        self.client.cookies.clear()
        self.server._login_attempts.clear()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.docker_mgr.close_client()
        cls.client.close()
        cls._tmp.cleanup()
        for key, value in {
            "RUNVARD_DATA_DIR": cls._old_data,
            "RUNVARD_USER": cls._old_user,
            "RUNVARD_PASS": cls._old_pass,
        }.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_root_redirects_to_login_when_unauthenticated(self) -> None:
        response = self.client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login")

    def test_api_errors_are_json(self) -> None:
        response = self.client.get("/api/system/info")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["ok"], False)
        self.assertEqual(response.json()["status"], 401)

        validation = self.client.post("/api/login", data={"username": "admin"})
        self.assertEqual(validation.status_code, 422)
        self.assertEqual(validation.json()["ok"], False)
        self.assertEqual(validation.json()["status"], 422)

    def test_login_cookie_allows_protected_api(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)
        self.assertTrue(login.json()["ok"])

        response = self.client.get("/api/auth/status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"], "admin")
        self.assertEqual(response.json()["role"], "admin")

    def test_login_rejects_invalid_remember_value(self) -> None:
        response = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "maybe"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["ok"], False)
        self.assertIn("Remember", response.json()["error"])
        self.assertIsNone(response.cookies.get(self.server.COOKIE_NAME))

    def test_config_returns_runtime_data_dir(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data_dir"], self._tmp.name)

    def test_corrupt_auth_config_is_quarantined_and_keeps_login_enabled(self) -> None:
        auth_config = Path(self.server.AUTH_CFG_FILE)
        auth_config.write_text("{broken-auth", encoding="utf-8")

        enabled = self.server.login_enabled()
        quarantined = list(auth_config.parent.glob("auth.json.corrupt-*"))
        quarantine_content = quarantined[0].read_text(encoding="utf-8")

        self.assertTrue(enabled)
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantine_content, "{broken-auth")
        self.assertFalse(auth_config.exists())

    def test_files_list_default_uses_home(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        response = self.client.get("/api/files/list")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["path"].endswith("/home"))

    def test_file_download_and_preview_validate_paths(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        with tempfile.TemporaryDirectory() as tmp:
            valid_file = Path(tmp) / "note.txt"
            valid_file.write_text("hello", encoding="utf-8")

            download = self.client.get(
                "/api/files/download", params={"path": str(valid_file)}
            )
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.text, "hello")

            preview = self.client.get(
                "/api/files/preview", params={"path": str(valid_file)}
            )
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(preview.text, "hello")

            missing = self.client.get(
                "/api/files/download", params={"path": str(Path(tmp) / "missing.txt")}
            )
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.json()["ok"], False)

            directory = self.client.get(
                "/api/files/preview", params={"path": tmp}
            )
            self.assertEqual(directory.status_code, 400)
            self.assertEqual(directory.json()["ok"], False)

        blocked = self.client.get(
            "/api/files/download", params={"path": "/proc/cpuinfo"}
        )
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(blocked.json()["ok"], False)

    def test_file_share_links_only_serve_regular_files(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        old_share_db = self.server.files.SHAREDB
        with tempfile.TemporaryDirectory() as tmp:
            try:
                self.server.files.SHAREDB = str(Path(tmp) / "shares.json")
                regular_file = Path(tmp) / "shared.txt"
                regular_file.write_text("shared", encoding="utf-8")

                token = self.client.post(
                    "/api/confirm-token",
                    data={"action": "files-share-link", "target": str(regular_file)},
                )
                self.assertEqual(token.status_code, 200)
                created = self.client.post(
                    "/api/files/share",
                    data={
                        "path": str(regular_file),
                        "confirm_token": token.json()["token"],
                    },
                )
                self.assertEqual(created.status_code, 200)

                downloaded = self.client.get(f"/dl/{created.json()['token']}")
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(downloaded.text, "shared")

                folder_token = self.client.post(
                    "/api/confirm-token",
                    data={"action": "files-share-link", "target": tmp},
                )
                self.assertEqual(folder_token.status_code, 200)
                folder_share = self.client.post(
                    "/api/files/share",
                    data={"path": tmp, "confirm_token": folder_token.json()["token"]},
                )
                self.assertEqual(folder_share.status_code, 400)
                self.assertEqual(folder_share.json()["ok"], False)

                Path(self.server.files.SHAREDB).write_text(
                    self.server.json.dumps(
                        {
                            "foldertoken": {
                                "path": tmp,
                                "name": "folder",
                                "created": 0,
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                legacy_folder = self.client.get("/dl/foldertoken")
                self.assertEqual(legacy_folder.status_code, 404)
                self.assertEqual(legacy_folder.json()["ok"], False)
            finally:
                self.server.files.SHAREDB = old_share_db

    def test_discovery_get_routes_do_not_raise_server_errors(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        routes = [
            "/api/system/stats",
            "/api/system/info",
            "/api/system/disks",
            "/api/system/temps",
            "/api/storage/devices",
            "/api/storage/lvm",
            "/api/storage/zfs",
            "/api/docker/available",
            "/api/docker/containers",
            "/api/services/list",
            "/api/vms/available",
            "/api/vms/list",
            "/api/backup/jobs",
            "/api/shares/samba",
            "/api/shares/nfs",
            "/api/shares/ftp",
            "/api/network/interfaces",
            "/api/network/firewall",
            "/api/security/users",
            "/api/security/groups",
            "/api/monitoring/alerts",
            "/api/sysmgr/updates",
            "/api/sysmgr/cron",
            "/api/apps/catalog",
            "/api/dashboard",
        ]

        for route in routes:
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertLess(response.status_code, 500)

    def test_structured_discovery_routes_expose_expected_keys(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        routes = {
            "/api/system/processes": ("processes",),
            "/api/docker/containers": ("containers",),
            "/api/docker/images": ("images",),
            "/api/docker/volumes": ("volumes",),
            "/api/services/list": ("services",),
            "/api/system/disk-io": ("disk_io",),
            "/api/system/net-detail": ("interfaces",),
            "/api/storage/lvm": ("pvs", "vgs", "lvs"),
            "/api/network/interfaces": ("interfaces",),
            "/api/security/smb-users": ("users",),
            "/api/security/certs": ("certificates",),
            "/api/vms/list": ("vms",),
            "/api/vms/networks": ("networks",),
            "/api/vms/pools": ("pools",),
        }

        for route, keys in routes.items():
            with self.subTest(route=route):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertIsInstance(data, dict)
                for key in keys:
                    self.assertIn(key, data)

    def test_validation_errors_are_not_500s(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        response = self.client.post("/api/storage/unmount", data={"mountpoint": "/"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["ok"], False)
        self.assertEqual(response.json()["status"], 403)

    def test_invalid_docker_container_ref_errors_are_json(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        stats = self.client.get(
            "/api/docker/stats",
            params={"container_id": "../bad"},
        )
        self.assertEqual(stats.status_code, 400)
        self.assertEqual(stats.json()["ok"], False)

        logs = self.client.get(
            "/api/docker/logs",
            params={"container_id": "../bad"},
        )
        self.assertEqual(logs.status_code, 200)
        self.assertFalse(logs.json()["ok"])

    def test_parameterized_read_routes_have_json_safe_validation_errors(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        snapshots = self.client.get(
            "/api/vms/snapshots",
            params={"name": "../bad"},
        )
        self.assertEqual(snapshots.status_code, 400)
        self.assertEqual(snapshots.json()["ok"], False)

        hardware = self.client.get(
            "/api/vms/hardware",
            params={"name": "../bad"},
        )
        self.assertEqual(hardware.status_code, 200)
        self.assertFalse(hardware.json()["ok"])

        secinfo = self.client.get(
            "/api/security/users/secinfo",
            params={"name": "../bad"},
        )
        self.assertEqual(secinfo.status_code, 200)
        self.assertFalse(secinfo.json()["ok"])

        ssh_keys = self.client.get(
            "/api/security/users/ssh-keys",
            params={"name": "../bad"},
        )
        self.assertEqual(ssh_keys.status_code, 400)
        self.assertEqual(ssh_keys.json()["ok"], False)

    def test_service_logs_validate_line_count(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        bad_type = self.client.get(
            "/api/services/logs",
            params={"name": "cron.service", "lines": "bad"},
        )
        self.assertEqual(bad_type.status_code, 422)
        self.assertEqual(bad_type.json()["ok"], False)

        out_of_range = self.client.get(
            "/api/services/logs",
            params={"name": "cron.service", "lines": "999999"},
        )
        self.assertEqual(out_of_range.status_code, 400)
        self.assertEqual(out_of_range.json()["ok"], False)

    def test_audit_log_validates_line_count(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        bad_type = self.client.get(
            "/api/monitoring/audit",
            params={"lines": "bad"},
        )
        self.assertEqual(bad_type.status_code, 422)
        self.assertEqual(bad_type.json()["ok"], False)

        out_of_range = self.client.get(
            "/api/monitoring/audit",
            params={"lines": "999999"},
        )
        self.assertEqual(out_of_range.status_code, 400)
        self.assertEqual(out_of_range.json()["ok"], False)
        self.assertIn("lines", out_of_range.json()["error"])

    def test_websocket_host_params_reject_invalid_values(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        for path in ("/ws/docker-exec?id=../bad", "/ws/vnc?name=bad name"):
            with self.subTest(path=path):
                with self.assertRaises(WebSocketDisconnect) as ctx:
                    with self.client.websocket_connect(path):
                        pass
                self.assertEqual(ctx.exception.code, 1008)

    def test_terminal_websocket_messages_are_validated(self) -> None:
        class FakeSession:
            def __init__(self) -> None:
                self.writes = []
                self.resizes = []

            def write(self, data: str) -> None:
                self.writes.append(data)

            def resize(self, rows: int, cols: int) -> None:
                self.resizes.append((rows, cols))

        session = FakeSession()

        self.assertTrue(
            self.server._apply_terminal_ws_message(
                session, self.server.json.dumps({"type": "input", "data": "ls\n"})
            )
        )
        self.assertTrue(
            self.server._apply_terminal_ws_message(
                session, self.server.json.dumps({"type": "resize", "rows": 30, "cols": 120})
            )
        )
        self.assertFalse(self.server._apply_terminal_ws_message(session, "{bad-json"))
        self.assertFalse(
            self.server._apply_terminal_ws_message(
                session, self.server.json.dumps({"type": "resize", "rows": 0, "cols": 120})
            )
        )
        self.assertFalse(
            self.server._apply_terminal_ws_message(
                session, self.server.json.dumps({"type": "input", "data": "x" * 9000})
            )
        )
        self.assertEqual(session.writes, ["ls\n"])
        self.assertEqual(session.resizes, [(30, 120)])

    def test_vnc_port_values_are_normalized_before_connect(self) -> None:
        self.assertEqual(self.server._parse_vnc_port("5901"), 5901)
        self.assertEqual(self.server._parse_vnc_port(5902), 5902)

        for value in (None, "", "-1", "None", "not-a-port", "70000", "0"):
            with self.subTest(value=value):
                self.assertIsNone(self.server._parse_vnc_port(value))

    def test_dangerous_actions_require_matching_confirm_token(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        no_token = self.client.post(
            "/api/sysmgr/power", data={"action": "reboot", "delay": 0}
        )
        self.assertEqual(no_token.status_code, 403)

        issued = self.client.post(
            "/api/confirm-token",
            data={"action": "power:reboot", "target": "reboot"},
        )
        self.assertEqual(issued.status_code, 200)
        token = issued.json()["token"]

        mismatch = self.client.post(
            "/api/sysmgr/power",
            data={"action": "shutdown", "delay": 0, "confirm_token": token},
        )
        self.assertEqual(mismatch.status_code, 403)
        self.assertIn("does not match", mismatch.json()["error"])

    def test_power_delay_value_is_validated_before_host_action(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_power_action = self.server.system_mgr.power_action

        def fake_power_action(action, delay_min=0):
            calls.append((action, delay_min))
            return {"ok": True}

        self.server.system_mgr.power_action = fake_power_action
        try:
            valid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "power:reboot", "target": "reboot"},
            )
            self.assertEqual(valid_token.status_code, 200)
            valid = self.client.post(
                "/api/sysmgr/power",
                data={
                    "action": "reboot",
                    "delay": "15",
                    "confirm_token": valid_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "power:reboot", "target": "reboot"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/sysmgr/power",
                data={
                    "action": "reboot",
                    "delay": "-1",
                    "confirm_token": invalid_token.json()["token"],
                },
            )

            invalid_action_token = self.client.post(
                "/api/confirm-token",
                data={"action": "power:hibernate", "target": "hibernate"},
            )
            self.assertEqual(invalid_action_token.status_code, 200)
            invalid_action = self.client.post(
                "/api/sysmgr/power",
                data={
                    "action": "hibernate",
                    "delay": "0",
                    "confirm_token": invalid_action_token.json()["token"],
                },
            )
        finally:
            self.server.system_mgr.power_action = original_power_action

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(calls, [("reboot", 15)])
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("Power delay", invalid.json()["error"])
        self.assertEqual(invalid_action.status_code, 400)
        self.assertEqual(invalid_action.json()["ok"], False)
        self.assertIn("Power action", invalid_action.json()["error"])

    def test_confirm_token_rejects_invalid_action_shape(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        valid = self.client.post(
            "/api/confirm-token",
            data={"action": "files-job:copy", "target": "/tmp/demo"},
        )
        self.assertEqual(valid.status_code, 200)

        bad_action = self.client.post(
            "/api/confirm-token",
            data={"action": "../files-delete", "target": "/tmp/demo"},
        )
        bad_target = self.client.post(
            "/api/confirm-token",
            data={"action": "files-delete", "target": "bad\npath"},
        )

        self.assertEqual(bad_action.status_code, 400)
        self.assertEqual(bad_action.json()["ok"], False)
        self.assertIn("Bestaetigung", bad_action.json()["error"])
        self.assertEqual(bad_target.status_code, 400)
        self.assertEqual(bad_target.json()["ok"], False)
        self.assertIn("Bestaetigung", bad_target.json()["error"])

    def test_storage_format_requires_confirm_token_before_host_command(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        response = self.client.post(
            "/api/storage/format",
            data={"partition": "/dev/sdz1", "fstype": "ext4"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["ok"], False)

    def test_confirm_tokens_are_single_use(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        issued = self.client.post(
            "/api/confirm-token",
            data={"action": "storage-format", "target": "/dev/sdz1"},
        )
        self.assertEqual(issued.status_code, 200)
        token = issued.json()["token"]

        old_format = self.server.storage.format_partition
        try:
            self.server.storage.format_partition = lambda partition, fstype: {
                "ok": True,
                "partition": partition,
                "fstype": fstype,
            }
            first = self.client.post(
                "/api/storage/format",
                data={
                    "partition": "/dev/sdz1",
                    "fstype": "ext4",
                    "confirm_token": token,
                },
            )
            self.assertEqual(first.status_code, 200)
            self.assertTrue(first.json()["ok"])

            second = self.client.post(
                "/api/storage/format",
                data={
                    "partition": "/dev/sdz1",
                    "fstype": "ext4",
                    "confirm_token": token,
                },
            )
            self.assertEqual(second.status_code, 403)
        finally:
            self.server.storage.format_partition = old_format

    def test_delete_routes_require_confirm_token_before_host_tools(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        cases = [
            ("/api/storage/lvm/lv-remove", {"lv_path": "/dev/vg0/data"}),
            ("/api/storage/zfs/destroy", {"name": "tank"}),
            ("/api/docker/action", {"container_id": "abc123", "action": "remove"}),
            ("/api/docker/images/remove", {"image_id": "sha256:abc"}),
            ("/api/docker/volumes/remove", {"name": "data"}),
            ("/api/docker/compose/remove", {"name": "demo"}),
            ("/api/vms/action", {"name": "demo", "action": "delete"}),
            ("/api/vms/pool/action", {"name": "default", "action": "delete"}),
            ("/api/vms/pool/vol-delete", {"pool": "default", "vol": "disk.qcow2"}),
            (
                "/api/vms/snapshot/action",
                {"name": "demo", "snap_name": "before", "action": "delete"},
            ),
        ]

        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["ok"], False)
                self.assertIn("confirmation token", response.json()["error"])

    def test_storage_mutations_require_confirm_token_before_host_tools(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        cases = [
            (
                "/api/storage/partition-table",
                {"device": "/dev/sdz", "label": "gpt"},
            ),
            ("/api/storage/partition", {"device": "/dev/sdz"}),
            (
                "/api/storage/mount",
                {"partition": "/dev/sdz1", "mountpoint": "/mnt/data"},
            ),
            ("/api/storage/unmount", {"mountpoint": "/mnt/data"}),
            (
                "/api/storage/swap/create",
                {"path": "/swapfile", "size_mb": "512", "persist": "false"},
            ),
            ("/api/storage/swap/action", {"target": "/swapfile", "action": "off"}),
            (
                "/api/storage/raid/create",
                {"name": "md0", "level": "1", "devices": "/dev/sdb,/dev/sdc"},
            ),
            (
                "/api/storage/lvm/vg-create",
                {"name": "vgdata", "devices": "/dev/sdb"},
            ),
            (
                "/api/storage/lvm/lv-create",
                {"vg": "vgdata", "name": "lvdata", "size": "10G"},
            ),
            (
                "/api/storage/lvm/lv-extend",
                {"lv_path": "/dev/vgdata/lvdata", "size": "+1G"},
            ),
            (
                "/api/storage/luks/format",
                {"device": "/dev/sdz1", "passphrase": "secret"},
            ),
            (
                "/api/storage/luks/open",
                {"device": "/dev/sdz1", "name": "cryptdata", "passphrase": "secret"},
            ),
            ("/api/storage/luks/close", {"name": "cryptdata"}),
            (
                "/api/storage/fs-grow",
                {"device": "/dev/sdz1", "mountpoint": "/mnt/data", "size": "max"},
            ),
            (
                "/api/storage/zfs/create",
                {"name": "tank", "raid": "stripe", "devices": "/dev/sdb"},
            ),
            ("/api/storage/zfs/scrub", {"name": "tank"}),
            (
                "/api/storage/btrfs/create",
                {"label": "data", "profile": "single", "devices": "/dev/sdb"},
            ),
            ("/api/storage/btrfs/scrub", {"mountpoint": "/mnt/data"}),
            (
                "/api/storage/iscsi/discover",
                {"portal": "127.0.0.1:3260"},
            ),
            (
                "/api/storage/iscsi/login",
                {"portal": "127.0.0.1:3260", "target": "iqn.2026-01.test"},
            ),
            (
                "/api/storage/iscsi/logout",
                {"portal": "127.0.0.1:3260", "target": "iqn.2026-01.test"},
            ),
        ]

        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["ok"], False)
                self.assertIn("confirmation token", response.json()["error"])

    def test_storage_form_values_are_validated_before_host_actions(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_mount = self.server.storage.mount_device
        original_swap = self.server.storage.create_swapfile
        original_swap_action = self.server.storage.swap_action
        original_create_raid = self.server.storage.create_raid
        original_ptable = self.server.storage.create_partition_table
        original_format = self.server.storage.format_partition
        original_zpool = self.server.storage.zpool_create
        original_btrfs = self.server.storage.btrfs_create

        def fake_mount(partition, mountpoint, persist=False):
            calls.append(("mount", partition, mountpoint, persist))
            return {"ok": True}

        def fake_swap(path, size_mb, persist=False):
            calls.append(("swap", path, size_mb, persist))
            return {"ok": True}

        def fake_swap_action(target, action):
            calls.append(("swap-action", target, action))
            return {"ok": True}

        def fake_create_raid(name, level, devices):
            calls.append(("raid", name, level, devices))
            return {"ok": True}

        def fake_ptable(device, label="gpt"):
            calls.append(("ptable", device, label))
            return {"ok": True}

        def fake_format(partition, fstype="ext4"):
            calls.append(("format", partition, fstype))
            return {"ok": True}

        def fake_zpool(name, raid, devices):
            calls.append(("zpool", name, raid, devices))
            return {"ok": True}

        def fake_btrfs(label, profile, devices):
            calls.append(("btrfs", label, profile, devices))
            return {"ok": True}

        self.server.storage.mount_device = fake_mount
        self.server.storage.create_swapfile = fake_swap
        self.server.storage.swap_action = fake_swap_action
        self.server.storage.create_raid = fake_create_raid
        self.server.storage.create_partition_table = fake_ptable
        self.server.storage.format_partition = fake_format
        self.server.storage.zpool_create = fake_zpool
        self.server.storage.btrfs_create = fake_btrfs
        try:
            ptable_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-partition-table", "target": "/dev/sdz"},
            )
            self.assertEqual(ptable_token.status_code, 200)
            ptable = self.client.post(
                "/api/storage/partition-table",
                data={
                    "device": "/dev/sdz",
                    "label": "msdos",
                    "confirm_token": ptable_token.json()["token"],
                },
            )

            format_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-format", "target": "/dev/sdz1"},
            )
            self.assertEqual(format_token.status_code, 200)
            formatted = self.client.post(
                "/api/storage/format",
                data={
                    "partition": "/dev/sdz1",
                    "fstype": "xfs",
                    "confirm_token": format_token.json()["token"],
                },
            )

            mount_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-mount", "target": "/dev/sdz1"},
            )
            self.assertEqual(mount_token.status_code, 200)
            mount = self.client.post(
                "/api/storage/mount",
                data={
                    "partition": "/dev/sdz1",
                    "mountpoint": "/mnt/data",
                    "persist": "true",
                    "confirm_token": mount_token.json()["token"],
                },
            )

            swap_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-swap-create", "target": "/swapfile"},
            )
            self.assertEqual(swap_token.status_code, 200)
            swap = self.client.post(
                "/api/storage/swap/create",
                data={
                    "path": "/swapfile",
                    "size_mb": "512",
                    "persist": "0",
                    "confirm_token": swap_token.json()["token"],
                },
            )

            raid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-raid-create", "target": "md0"},
            )
            self.assertEqual(raid_token.status_code, 200)
            raid = self.client.post(
                "/api/storage/raid/create",
                data={
                    "name": "md0",
                    "level": "1",
                    "devices": "/dev/sdb,/dev/sdc",
                    "confirm_token": raid_token.json()["token"],
                },
            )

            swap_action_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-swap-action:off", "target": "/swapfile"},
            )
            self.assertEqual(swap_action_token.status_code, 200)
            swap_action = self.client.post(
                "/api/storage/swap/action",
                data={
                    "target": "/swapfile",
                    "action": "off",
                    "confirm_token": swap_action_token.json()["token"],
                },
            )

            zfs_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-zfs-create", "target": "tank"},
            )
            self.assertEqual(zfs_token.status_code, 200)
            zfs = self.client.post(
                "/api/storage/zfs/create",
                data={
                    "name": "tank",
                    "raid": "mirror",
                    "devices": "/dev/sdb,/dev/sdc",
                    "confirm_token": zfs_token.json()["token"],
                },
            )

            btrfs_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-btrfs-create", "target": "data"},
            )
            self.assertEqual(btrfs_token.status_code, 200)
            btrfs = self.client.post(
                "/api/storage/btrfs/create",
                data={
                    "label": "data",
                    "profile": "raid1",
                    "devices": "/dev/sdb,/dev/sdc",
                    "confirm_token": btrfs_token.json()["token"],
                },
            )

            invalid_bool_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-mount", "target": "/dev/sdz2"},
            )
            self.assertEqual(invalid_bool_token.status_code, 200)
            invalid_bool = self.client.post(
                "/api/storage/mount",
                data={
                    "partition": "/dev/sdz2",
                    "mountpoint": "/mnt/other",
                    "persist": "forever",
                    "confirm_token": invalid_bool_token.json()["token"],
                },
            )

            invalid_size_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-swap-create", "target": "/too-big-swap"},
            )
            self.assertEqual(invalid_size_token.status_code, 200)
            invalid_size = self.client.post(
                "/api/storage/swap/create",
                data={
                    "path": "/too-big-swap",
                    "size_mb": str(1024 * 1024 + 1),
                    "confirm_token": invalid_size_token.json()["token"],
                },
            )

            invalid_action_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-swap-action:off", "target": "/swapfile"},
            )
            self.assertEqual(invalid_action_token.status_code, 200)
            invalid_action = self.client.post(
                "/api/storage/swap/action",
                data={
                    "target": "/swapfile",
                    "action": "toggle",
                    "confirm_token": invalid_action_token.json()["token"],
                },
            )

            invalid_format_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-format", "target": "/dev/sdz2"},
            )
            self.assertEqual(invalid_format_token.status_code, 200)
            invalid_format = self.client.post(
                "/api/storage/format",
                data={
                    "partition": "/dev/sdz2",
                    "fstype": "ntfs",
                    "confirm_token": invalid_format_token.json()["token"],
                },
            )

            invalid_zfs_token = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-zfs-create", "target": "tank2"},
            )
            self.assertEqual(invalid_zfs_token.status_code, 200)
            invalid_zfs = self.client.post(
                "/api/storage/zfs/create",
                data={
                    "name": "tank2",
                    "raid": "raid6",
                    "devices": "/dev/sdd",
                    "confirm_token": invalid_zfs_token.json()["token"],
                },
            )
        finally:
            self.server.storage.mount_device = original_mount
            self.server.storage.create_swapfile = original_swap
            self.server.storage.swap_action = original_swap_action
            self.server.storage.create_raid = original_create_raid
            self.server.storage.create_partition_table = original_ptable
            self.server.storage.format_partition = original_format
            self.server.storage.zpool_create = original_zpool
            self.server.storage.btrfs_create = original_btrfs

        self.assertEqual(ptable.status_code, 200)
        self.assertEqual(formatted.status_code, 200)
        self.assertEqual(mount.status_code, 200)
        self.assertEqual(swap.status_code, 200)
        self.assertEqual(raid.status_code, 200)
        self.assertEqual(swap_action.status_code, 200)
        self.assertEqual(zfs.status_code, 200)
        self.assertEqual(btrfs.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("ptable", "/dev/sdz", "msdos"),
                ("format", "/dev/sdz1", "xfs"),
                ("mount", "/dev/sdz1", "/mnt/data", True),
                ("swap", "/swapfile", 512, False),
                ("raid", "md0", 1, ["/dev/sdb", "/dev/sdc"]),
                ("swap-action", "/swapfile", "off"),
                ("zpool", "tank", "mirror", ["/dev/sdb", "/dev/sdc"]),
                ("btrfs", "data", "raid1", ["/dev/sdb", "/dev/sdc"]),
            ],
        )
        self.assertEqual(invalid_bool.status_code, 400)
        self.assertEqual(invalid_bool.json()["ok"], False)
        self.assertIn("Persist-Wert", invalid_bool.json()["error"])
        self.assertEqual(invalid_size.status_code, 400)
        self.assertEqual(invalid_size.json()["ok"], False)
        self.assertIn("Swap size", invalid_size.json()["error"])
        self.assertEqual(invalid_action.status_code, 400)
        self.assertEqual(invalid_action.json()["ok"], False)
        self.assertIn("Swap action", invalid_action.json()["error"])
        self.assertEqual(invalid_format.status_code, 400)
        self.assertEqual(invalid_format.json()["ok"], False)
        self.assertIn("Filesystem type", invalid_format.json()["error"])
        self.assertEqual(invalid_zfs.status_code, 400)
        self.assertEqual(invalid_zfs.json()["ok"], False)
        self.assertIn("ZFS RAID layout", invalid_zfs.json()["error"])

    def test_storage_targets_are_validated_before_confirm_token_use(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_format = self.server.storage.format_partition

        def fake_format(partition, fstype="ext4"):
            calls.append((partition, fstype))
            return {"ok": True}

        self.server.storage.format_partition = fake_format
        try:
            issued = self.client.post(
                "/api/confirm-token",
                data={"action": "storage-format", "target": "/dev/../sdz1"},
            )
            self.assertEqual(issued.status_code, 200)
            token = issued.json()["token"]

            response = self.client.post(
                "/api/storage/format",
                data={
                    "partition": "/dev/../sdz1",
                    "fstype": "ext4",
                    "confirm_token": token,
                },
            )
            self.assertIn(token, self.server.security_tokens._confirm_tokens)
            self.server.security_tokens._confirm_tokens.pop(token, None)
        finally:
            self.server.storage.format_partition = original_format

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["ok"], False)
        self.assertIn("Invalid device", response.json()["error"])
        self.assertEqual(calls, [])

    def test_identity_share_backup_routes_require_confirm_token(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        cases = [
            (
                "/api/files/samba-share",
                {"path": "/srv/share", "name": "media", "writable": "true"},
            ),
            (
                "/api/files/mount-smb",
                {
                    "server": "nas.local",
                    "share_name": "media",
                    "mountpoint": "/mnt/media",
                },
            ),
            (
                "/api/files/mount-nfs",
                {
                    "server": "nas.local",
                    "export": "/srv/media",
                    "mountpoint": "/mnt/media",
                },
            ),
            (
                "/api/backup/add",
                {
                    "name": "home",
                    "source": "/home",
                    "dest": "/mnt/backup/home",
                    "schedule": "manual",
                },
            ),
            ("/api/backup/run", {"job_id": "1"}),
            (
                "/api/shares/samba/add",
                {"name": "media", "path": "/srv/media", "writable": "true"},
            ),
            ("/api/shares/nfs/add", {"path": "/srv/media", "clients": "*"}),
            ("/api/security/users/add", {"name": "alice"}),
            (
                "/api/security/users/password",
                {"name": "alice", "password": "secret"},
            ),
            (
                "/api/security/users/smb-password",
                {"name": "alice", "password": "secret"},
            ),
            (
                "/api/security/users/ssh-keys/add",
                {"name": "alice", "key": "ssh-ed25519 AAAA test"},
            ),
            (
                "/api/security/users/ssh-keys/remove",
                {"name": "alice", "key": "ssh-ed25519 AAAA test"},
            ),
            (
                "/api/security/users/sudo",
                {"name": "alice", "enable": "true", "nopasswd": "false"},
            ),
            ("/api/security/users/aging", {"name": "alice", "max_days": "90"}),
            ("/api/security/users/expire", {"name": "alice"}),
            ("/api/security/groups/add", {"name": "developers"}),
            ("/api/security/groups/delete", {"name": "developers"}),
            (
                "/api/security/groups/add-member",
                {"group": "developers", "member": "alice"},
            ),
            (
                "/api/security/groups/remove-member",
                {"group": "developers", "member": "alice"},
            ),
            ("/api/security/certs/generate", {"common_name": "runvard.local"}),
        ]

        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["ok"], False)
                self.assertIn("confirmation token", response.json()["error"])

    def test_backup_job_id_is_validated_before_run(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_run_job = self.server.backup.run_job

        def fake_run_job(job_id):
            calls.append(job_id)
            return {"ok": True}

        self.server.backup.run_job = fake_run_job
        try:
            valid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "backup-run", "target": "42"},
            )
            self.assertEqual(valid_token.status_code, 200)
            valid = self.client.post(
                "/api/backup/run",
                data={"job_id": "42", "confirm_token": valid_token.json()["token"]},
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "backup-run", "target": "0"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/backup/run",
                data={"job_id": "0", "confirm_token": invalid_token.json()["token"]},
            )
        finally:
            self.server.backup.run_job = original_run_job

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(calls, [42])
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("Backup job", invalid.json()["error"])

    def test_security_sudo_boolean_values_are_validated(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_set_sudo = self.server.security.set_sudo

        def fake_set_sudo(name, enable, nopasswd=False):
            calls.append((name, enable, nopasswd))
            return {"ok": True}

        self.server.security.set_sudo = fake_set_sudo
        try:
            valid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "security-sudo", "target": "alice"},
            )
            self.assertEqual(valid_token.status_code, 200)
            valid = self.client.post(
                "/api/security/users/sudo",
                data={
                    "name": "alice",
                    "enable": "true",
                    "nopasswd": "false",
                    "confirm_token": valid_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "security-sudo", "target": "alice"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/security/users/sudo",
                data={
                    "name": "alice",
                    "enable": "maybe",
                    "nopasswd": "false",
                    "confirm_token": invalid_token.json()["token"],
                },
            )
        finally:
            self.server.security.set_sudo = original_set_sudo

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(calls, [("alice", True, False)])
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("sudo-Wert", invalid.json()["error"])

    def test_samba_writable_boolean_values_are_validated(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_add_samba_share = self.server.shares.add_samba_share

        def fake_add_samba_share(name, path, writable=True, guest=False):
            calls.append((name, path, writable, guest))
            return {"ok": True}

        self.server.shares.add_samba_share = fake_add_samba_share
        try:
            files_token = self.client.post(
                "/api/confirm-token",
                data={"action": "files-samba-share", "target": "media"},
            )
            self.assertEqual(files_token.status_code, 200)
            files_share = self.client.post(
                "/api/files/samba-share",
                data={
                    "path": "/srv/share",
                    "name": "media",
                    "writable": "false",
                    "confirm_token": files_token.json()["token"],
                },
            )

            shares_token = self.client.post(
                "/api/confirm-token",
                data={"action": "share-samba-add", "target": "backup"},
            )
            self.assertEqual(shares_token.status_code, 200)
            shares_share = self.client.post(
                "/api/shares/samba/add",
                data={
                    "name": "backup",
                    "path": "/srv/backup",
                    "writable": "1",
                    "confirm_token": shares_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "share-samba-add", "target": "bad"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/shares/samba/add",
                data={
                    "name": "bad",
                    "path": "/srv/bad",
                    "writable": "maybe",
                    "confirm_token": invalid_token.json()["token"],
                },
            )
        finally:
            self.server.shares.add_samba_share = original_add_samba_share

        self.assertEqual(files_share.status_code, 200)
        self.assertEqual(shares_share.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("media", "/srv/share", False, False),
                ("backup", "/srv/backup", True, False),
            ],
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("Writable-Wert", invalid.json()["error"])

    def test_network_risk_routes_require_confirm_token_before_host_tools(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        cases = [
            ("/api/network/bond/delete", {"name": "bond0"}),
            ("/api/network/firewall/remove", {"num": "1"}),
            (
                "/api/network/configure-ip",
                {"iface": "eth0", "mode": "dhcp", "persist": "false"},
            ),
            ("/api/network/link/delete", {"name": "br0"}),
        ]

        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["ok"], False)
                self.assertIn("confirmation token", response.json()["error"])

    def test_network_persist_boolean_value_is_validated(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_configure_ip = self.server.network.configure_ip
        original_create_bond = self.server.network.create_bond

        def fake_configure_ip(iface, mode="static", ip="", netmask="24",
                              gateway="", dns="", persist=False):
            calls.append(("ip", iface, mode, ip, netmask, gateway, dns, persist))
            return {"ok": True}

        def fake_create_bond(name, members, mode="802.3ad"):
            calls.append(("bond", name, members, mode))
            return {"ok": True}

        self.server.network.configure_ip = fake_configure_ip
        self.server.network.create_bond = fake_create_bond
        try:
            bond_token = self.client.post(
                "/api/confirm-token",
                data={"action": "network-bond-create", "target": "bond0"},
            )
            self.assertEqual(bond_token.status_code, 200)
            bond = self.client.post(
                "/api/network/bond/create",
                data={
                    "name": "bond0",
                    "members": "eth0,eth1",
                    "mode": "active-backup",
                    "confirm_token": bond_token.json()["token"],
                },
            )

            valid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "network-configure-ip", "target": "eth0"},
            )
            self.assertEqual(valid_token.status_code, 200)
            valid = self.client.post(
                "/api/network/configure-ip",
                data={
                    "iface": "eth0",
                    "mode": "dhcp",
                    "persist": "true",
                    "confirm_token": valid_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "network-configure-ip", "target": "eth1"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/network/configure-ip",
                data={
                    "iface": "eth1",
                    "mode": "dhcp",
                    "persist": "sometimes",
                    "confirm_token": invalid_token.json()["token"],
                },
            )

            invalid_mode_token = self.client.post(
                "/api/confirm-token",
                data={"action": "network-configure-ip", "target": "eth2"},
            )
            self.assertEqual(invalid_mode_token.status_code, 200)
            invalid_mode = self.client.post(
                "/api/network/configure-ip",
                data={
                    "iface": "eth2",
                    "mode": "manual",
                    "confirm_token": invalid_mode_token.json()["token"],
                },
            )
        finally:
            self.server.network.configure_ip = original_configure_ip
            self.server.network.create_bond = original_create_bond

        self.assertEqual(bond.status_code, 200)
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("bond", "bond0", ["eth0", "eth1"], "active-backup"),
                ("ip", "eth0", "dhcp", "", "24", "", "", True),
            ],
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("Persist-Wert", invalid.json()["error"])
        self.assertEqual(invalid_mode.status_code, 400)
        self.assertEqual(invalid_mode.json()["ok"], False)
        self.assertIn("Network mode", invalid_mode.json()["error"])

    def test_network_numeric_values_are_validated_before_host_actions(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_firewall_add = self.server.network.firewall_add_rule
        original_firewall_remove = self.server.network.firewall_remove_rule
        original_create_vlan = self.server.network.create_vlan

        def fake_firewall_add(port, proto="tcp", action="allow"):
            calls.append(("add", port, proto, action))
            return {"ok": True}

        def fake_firewall_remove(num):
            calls.append(("remove", num))
            return {"ok": True}

        def fake_create_vlan(parent, vlan_id, name=""):
            calls.append(("vlan", parent, vlan_id, name))
            return {"ok": True}

        self.server.network.firewall_add_rule = fake_firewall_add
        self.server.network.firewall_remove_rule = fake_firewall_remove
        self.server.network.create_vlan = fake_create_vlan
        try:
            add_token = self.client.post(
                "/api/confirm-token",
                data={"action": "network-firewall-add", "target": "443"},
            )
            self.assertEqual(add_token.status_code, 200)
            add = self.client.post(
                "/api/network/firewall/add",
                data={
                    "port": "443",
                    "proto": "tcp",
                    "action": "allow",
                    "confirm_token": add_token.json()["token"],
                },
            )

            remove_token = self.client.post(
                "/api/confirm-token",
                data={"action": "network-firewall-remove", "target": "7"},
            )
            self.assertEqual(remove_token.status_code, 200)
            remove = self.client.post(
                "/api/network/firewall/remove",
                data={"num": "7", "confirm_token": remove_token.json()["token"]},
            )

            vlan_token = self.client.post(
                "/api/confirm-token",
                data={"action": "network-vlan-create", "target": "eth0.10"},
            )
            self.assertEqual(vlan_token.status_code, 200)
            vlan = self.client.post(
                "/api/network/vlan/create",
                data={
                    "parent": "eth0",
                    "vlan_id": "10",
                    "confirm_token": vlan_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "network-firewall-add", "target": "70000"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/network/firewall/add",
                data={
                    "port": "70000",
                    "confirm_token": invalid_token.json()["token"],
                },
            )

            invalid_action_token = self.client.post(
                "/api/confirm-token",
                data={"action": "network-firewall-add", "target": "443"},
            )
            self.assertEqual(invalid_action_token.status_code, 200)
            invalid_action = self.client.post(
                "/api/network/firewall/add",
                data={
                    "port": "443",
                    "proto": "icmp",
                    "action": "open",
                    "confirm_token": invalid_action_token.json()["token"],
                },
            )
        finally:
            self.server.network.firewall_add_rule = original_firewall_add
            self.server.network.firewall_remove_rule = original_firewall_remove
            self.server.network.create_vlan = original_create_vlan

        self.assertEqual(add.status_code, 200)
        self.assertEqual(remove.status_code, 200)
        self.assertEqual(vlan.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("add", 443, "tcp", "allow"),
                ("remove", 7),
                ("vlan", "eth0", 10, ""),
            ],
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("Firewall port", invalid.json()["error"])
        self.assertEqual(invalid_action.status_code, 400)
        self.assertEqual(invalid_action.json()["ok"], False)
        self.assertIn("Firewall protocol", invalid_action.json()["error"])

    def test_vm_network_mutations_require_confirm_token(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        cases = [
            ("/api/vms/action", {"name": "demo", "action": "start"}),
            (
                "/api/vms/disk/attach",
                {"name": "demo", "source": "/var/lib/libvirt/images/disk.qcow2", "target": "vdb"},
            ),
            ("/api/vms/disk/detach", {"name": "demo", "target": "vdb"}),
            ("/api/vms/nic/attach", {"name": "demo", "network": "default"}),
            (
                "/api/vms/nic/detach",
                {"name": "demo", "type": "network", "mac": "52:54:00:00:00:01"},
            ),
            (
                "/api/vms/pool/create",
                {"name": "default", "ptype": "dir", "target": "/var/lib/libvirt/images"},
            ),
            ("/api/vms/pool/action", {"name": "default", "action": "start"}),
            (
                "/api/vms/pool/vol-create",
                {"pool": "default", "name": "disk.qcow2", "size_gb": "10"},
            ),
            ("/api/vms/clone", {"name": "demo", "newname": "demo-copy"}),
            ("/api/vms/cdrom", {"name": "demo", "iso": "debian.iso"}),
            (
                "/api/vms/create",
                {
                    "name": "demo",
                    "memory_mb": "1024",
                    "vcpus": "1",
                    "disk_gb": "10",
                    "network": "default",
                },
            ),
            ("/api/vms/snapshot", {"name": "demo", "snap_name": "before"}),
            (
                "/api/vms/snapshot/action",
                {"name": "demo", "snap_name": "before", "action": "revert"},
            ),
            (
                "/api/network/bond/create",
                {"name": "bond0", "members": "eth0,eth1", "mode": "active-backup"},
            ),
            (
                "/api/network/firewall/add",
                {"port": "22", "proto": "tcp", "action": "allow"},
            ),
            (
                "/api/network/bridge/create",
                {"name": "br0", "members": "eth0"},
            ),
            (
                "/api/network/vlan/create",
                {"parent": "eth0", "vlan_id": "10", "name": "eth0.10"},
            ),
        ]

        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["ok"], False)
                self.assertIn("confirmation token", response.json()["error"])

    def test_vm_resource_values_are_validated_before_host_actions(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_vol_create = self.server.vms.vol_create
        original_create_vm = self.server.vms.create_vm
        original_pool_create = self.server.vms.pool_create

        def fake_vol_create(pool, name, size_gb, fmt="qcow2"):
            calls.append(("volume", pool, name, size_gb, fmt))
            return {"ok": True}

        def fake_create_vm(name, memory_mb, vcpus, disk_gb, iso, network="default"):
            calls.append(("vm", name, memory_mb, vcpus, disk_gb, iso, network))
            return {"ok": True}

        def fake_pool_create(name, ptype, target):
            calls.append(("pool-create", name, ptype, target))
            return {"ok": True}

        self.server.vms.vol_create = fake_vol_create
        self.server.vms.create_vm = fake_create_vm
        self.server.vms.pool_create = fake_pool_create
        try:
            pool_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-pool-create", "target": "default"},
            )
            self.assertEqual(pool_token.status_code, 200)
            pool = self.client.post(
                "/api/vms/pool/create",
                data={
                    "name": "default",
                    "ptype": "fs",
                    "target": "/var/lib/libvirt/images",
                    "confirm_token": pool_token.json()["token"],
                },
            )

            volume_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-volume-create", "target": "default/disk"},
            )
            self.assertEqual(volume_token.status_code, 200)
            volume = self.client.post(
                "/api/vms/pool/vol-create",
                data={
                    "pool": "default",
                    "name": "disk",
                    "size_gb": "20",
                    "format": "qcow2",
                    "confirm_token": volume_token.json()["token"],
                },
            )

            vm_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-create", "target": "demo"},
            )
            self.assertEqual(vm_token.status_code, 200)
            vm = self.client.post(
                "/api/vms/create",
                data={
                    "name": "demo",
                    "memory_mb": "2048",
                    "vcpus": "2",
                    "disk_gb": "40",
                    "network": "default",
                    "confirm_token": vm_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-create", "target": "tiny"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/vms/create",
                data={
                    "name": "tiny",
                    "memory_mb": "128",
                    "vcpus": "1",
                    "disk_gb": "10",
                    "confirm_token": invalid_token.json()["token"],
                },
            )

            invalid_pool_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-pool-create", "target": "badpool"},
            )
            self.assertEqual(invalid_pool_token.status_code, 200)
            invalid_pool = self.client.post(
                "/api/vms/pool/create",
                data={
                    "name": "badpool",
                    "ptype": "netfs",
                    "target": "/var/lib/libvirt/images",
                    "confirm_token": invalid_pool_token.json()["token"],
                },
            )

            invalid_volume_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-volume-create", "target": "default/disk2"},
            )
            self.assertEqual(invalid_volume_token.status_code, 200)
            invalid_volume = self.client.post(
                "/api/vms/pool/vol-create",
                data={
                    "pool": "default",
                    "name": "disk2",
                    "size_gb": "20",
                    "format": "vmdk",
                    "confirm_token": invalid_volume_token.json()["token"],
                },
            )
        finally:
            self.server.vms.vol_create = original_vol_create
            self.server.vms.create_vm = original_create_vm
            self.server.vms.pool_create = original_pool_create

        self.assertEqual(pool.status_code, 200)
        self.assertEqual(volume.status_code, 200)
        self.assertEqual(vm.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("pool-create", "default", "fs", "/var/lib/libvirt/images"),
                ("volume", "default", "disk", 20, "qcow2"),
                ("vm", "demo", 2048, 2, 40, "", "default"),
            ],
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("VM memory", invalid.json()["error"])
        self.assertEqual(invalid_pool.status_code, 400)
        self.assertEqual(invalid_pool.json()["ok"], False)
        self.assertIn("VM pool type", invalid_pool.json()["error"])
        self.assertEqual(invalid_volume.status_code, 400)
        self.assertEqual(invalid_volume.json()["ok"], False)
        self.assertIn("VM volume format", invalid_volume.json()["error"])

    def test_vm_action_values_are_validated_before_host_actions(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_vm_action = self.server.vms.vm_action
        original_pool_action = self.server.vms.pool_action
        original_snapshot_action = self.server.vms.snapshot_action

        def fake_vm_action(name, action):
            calls.append(("vm", name, action))
            return {"ok": True}

        def fake_pool_action(name, action):
            calls.append(("pool", name, action))
            return {"ok": True}

        def fake_snapshot_action(name, snap_name, action):
            calls.append(("snapshot", name, snap_name, action))
            return {"ok": True}

        self.server.vms.vm_action = fake_vm_action
        self.server.vms.pool_action = fake_pool_action
        self.server.vms.snapshot_action = fake_snapshot_action
        try:
            vm_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-action:start", "target": "demo"},
            )
            self.assertEqual(vm_token.status_code, 200)
            vm = self.client.post(
                "/api/vms/action",
                data={
                    "name": "demo",
                    "action": "start",
                    "confirm_token": vm_token.json()["token"],
                },
            )

            pool_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-pool-action:autostart-on", "target": "default"},
            )
            self.assertEqual(pool_token.status_code, 200)
            pool = self.client.post(
                "/api/vms/pool/action",
                data={
                    "name": "default",
                    "action": "autostart-on",
                    "confirm_token": pool_token.json()["token"],
                },
            )

            snapshot_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-snapshot-action:revert", "target": "demo/before"},
            )
            self.assertEqual(snapshot_token.status_code, 200)
            snapshot = self.client.post(
                "/api/vms/snapshot/action",
                data={
                    "name": "demo",
                    "snap_name": "before",
                    "action": "revert",
                    "confirm_token": snapshot_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-action:start", "target": "demo"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/vms/action",
                data={
                    "name": "demo",
                    "action": "pause",
                    "confirm_token": invalid_token.json()["token"],
                },
            )
        finally:
            self.server.vms.vm_action = original_vm_action
            self.server.vms.pool_action = original_pool_action
            self.server.vms.snapshot_action = original_snapshot_action

        self.assertEqual(vm.status_code, 200)
        self.assertEqual(pool.status_code, 200)
        self.assertEqual(snapshot.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("vm", "demo", "start"),
                ("pool", "default", "autostart-on"),
                ("snapshot", "demo", "before", "revert"),
            ],
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("VM action", invalid.json()["error"])

    def test_vm_attachment_choice_values_are_validated_before_host_actions(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_attach_disk = self.server.vms.attach_disk
        original_attach_nic = self.server.vms.attach_nic
        original_detach_nic = self.server.vms.detach_nic

        def fake_attach_disk(name, source, target, bus="virtio"):
            calls.append(("disk", name, source, target, bus))
            return {"ok": True}

        def fake_attach_nic(name, network, model="virtio"):
            calls.append(("nic-attach", name, network, model))
            return {"ok": True}

        def fake_detach_nic(name, itype, mac):
            calls.append(("nic-detach", name, itype, mac))
            return {"ok": True}

        self.server.vms.attach_disk = fake_attach_disk
        self.server.vms.attach_nic = fake_attach_nic
        self.server.vms.detach_nic = fake_detach_nic
        try:
            disk_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-disk-attach", "target": "demo/vdb"},
            )
            self.assertEqual(disk_token.status_code, 200)
            disk = self.client.post(
                "/api/vms/disk/attach",
                data={
                    "name": "demo",
                    "source": "/var/lib/libvirt/images/disk.qcow2",
                    "target": "vdb",
                    "bus": "sata",
                    "confirm_token": disk_token.json()["token"],
                },
            )

            nic_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-nic-attach", "target": "demo/default"},
            )
            self.assertEqual(nic_token.status_code, 200)
            nic = self.client.post(
                "/api/vms/nic/attach",
                data={
                    "name": "demo",
                    "network": "default",
                    "model": "e1000",
                    "confirm_token": nic_token.json()["token"],
                },
            )

            detach_token = self.client.post(
                "/api/confirm-token",
                data={
                    "action": "vm-nic-detach",
                    "target": "demo/52:54:00:00:00:01",
                },
            )
            self.assertEqual(detach_token.status_code, 200)
            detached = self.client.post(
                "/api/vms/nic/detach",
                data={
                    "name": "demo",
                    "type": "bridge",
                    "mac": "52:54:00:00:00:01",
                    "confirm_token": detach_token.json()["token"],
                },
            )

            invalid_disk_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-disk-attach", "target": "demo/vdc"},
            )
            self.assertEqual(invalid_disk_token.status_code, 200)
            invalid_disk = self.client.post(
                "/api/vms/disk/attach",
                data={
                    "name": "demo",
                    "source": "/var/lib/libvirt/images/disk.qcow2",
                    "target": "vdc",
                    "bus": "usb",
                    "confirm_token": invalid_disk_token.json()["token"],
                },
            )

            invalid_nic_token = self.client.post(
                "/api/confirm-token",
                data={"action": "vm-nic-attach", "target": "demo/default"},
            )
            self.assertEqual(invalid_nic_token.status_code, 200)
            invalid_nic = self.client.post(
                "/api/vms/nic/attach",
                data={
                    "name": "demo",
                    "network": "default",
                    "model": "vmxnet3",
                    "confirm_token": invalid_nic_token.json()["token"],
                },
            )

            invalid_detach_token = self.client.post(
                "/api/confirm-token",
                data={
                    "action": "vm-nic-detach",
                    "target": "demo/52:54:00:00:00:02",
                },
            )
            self.assertEqual(invalid_detach_token.status_code, 200)
            invalid_detach = self.client.post(
                "/api/vms/nic/detach",
                data={
                    "name": "demo",
                    "type": "tap",
                    "mac": "52:54:00:00:00:02",
                    "confirm_token": invalid_detach_token.json()["token"],
                },
            )
        finally:
            self.server.vms.attach_disk = original_attach_disk
            self.server.vms.attach_nic = original_attach_nic
            self.server.vms.detach_nic = original_detach_nic

        self.assertEqual(disk.status_code, 200)
        self.assertEqual(nic.status_code, 200)
        self.assertEqual(detached.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("disk", "demo", "/var/lib/libvirt/images/disk.qcow2", "vdb", "sata"),
                ("nic-attach", "demo", "default", "e1000"),
                ("nic-detach", "demo", "bridge", "52:54:00:00:00:01"),
            ],
        )
        self.assertEqual(invalid_disk.status_code, 400)
        self.assertEqual(invalid_disk.json()["ok"], False)
        self.assertIn("VM disk bus", invalid_disk.json()["error"])
        self.assertEqual(invalid_nic.status_code, 400)
        self.assertEqual(invalid_nic.json()["ok"], False)
        self.assertIn("VM NIC model", invalid_nic.json()["error"])
        self.assertEqual(invalid_detach.status_code, 400)
        self.assertEqual(invalid_detach.json()["ok"], False)
        self.assertIn("VM NIC type", invalid_detach.json()["error"])

    def test_account_auth_routes_require_confirm_token(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        cases = [
            ("/api/auth/toggle", {"enabled": "1"}),
            (
                "/api/accounts/add",
                {"username": "viewer2", "password": "secret", "role": "readonly"},
            ),
            (
                "/api/accounts/password",
                {"username": "viewer2", "password": "secret2"},
            ),
            ("/api/accounts/role", {"username": "viewer2", "role": "admin"}),
            ("/api/accounts/delete", {"username": "viewer2"}),
        ]

        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["ok"], False)
                self.assertIn("confirmation token", response.json()["error"])

    def test_account_roles_are_validated_before_persisting(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_add_user = self.server.accounts.add_user
        original_set_role = self.server.accounts.set_role

        def fake_add_user(username, password, role="readonly"):
            calls.append(("add", username, password, role))
            return {"ok": True}

        def fake_set_role(username, role):
            calls.append(("role", username, role))
            return {"ok": True}

        self.server.accounts.add_user = fake_add_user
        self.server.accounts.set_role = fake_set_role
        try:
            add_token = self.client.post(
                "/api/confirm-token",
                data={"action": "account-add", "target": "viewer_role"},
            )
            self.assertEqual(add_token.status_code, 200)
            added = self.client.post(
                "/api/accounts/add",
                data={
                    "username": "viewer_role",
                    "password": "secret",
                    "role": "readonly",
                    "confirm_token": add_token.json()["token"],
                },
            )

            role_token = self.client.post(
                "/api/confirm-token",
                data={"action": "account-role", "target": "viewer_role"},
            )
            self.assertEqual(role_token.status_code, 200)
            role = self.client.post(
                "/api/accounts/role",
                data={
                    "username": "viewer_role",
                    "role": "admin",
                    "confirm_token": role_token.json()["token"],
                },
            )

            invalid_add_token = self.client.post(
                "/api/confirm-token",
                data={"action": "account-add", "target": "viewer_bad_role"},
            )
            self.assertEqual(invalid_add_token.status_code, 200)
            invalid_add = self.client.post(
                "/api/accounts/add",
                data={
                    "username": "viewer_bad_role",
                    "password": "secret",
                    "role": "owner",
                    "confirm_token": invalid_add_token.json()["token"],
                },
            )

            invalid_role_token = self.client.post(
                "/api/confirm-token",
                data={"action": "account-role", "target": "viewer_role"},
            )
            self.assertEqual(invalid_role_token.status_code, 200)
            invalid_role = self.client.post(
                "/api/accounts/role",
                data={
                    "username": "viewer_role",
                    "role": "owner",
                    "confirm_token": invalid_role_token.json()["token"],
                },
            )
        finally:
            self.server.accounts.add_user = original_add_user
            self.server.accounts.set_role = original_set_role

        self.assertEqual(added.status_code, 200)
        self.assertEqual(role.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("add", "viewer_role", "secret", "readonly"),
                ("role", "viewer_role", "admin"),
            ],
        )
        self.assertEqual(invalid_add.status_code, 400)
        self.assertEqual(invalid_add.json()["ok"], False)
        self.assertIn("Account role", invalid_add.json()["error"])
        self.assertEqual(invalid_role.status_code, 400)
        self.assertEqual(invalid_role.json()["ok"], False)
        self.assertIn("Account role", invalid_role.json()["error"])

    def test_auth_toggle_rejects_invalid_enabled_value(self) -> None:
        old_auth_config = self.server.AUTH_CFG_FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.server.AUTH_CFG_FILE = os.path.join(tmp, "auth.json")
                self.server.set_login_enabled(True)
                login = self.client.post(
                    "/api/login",
                    data={"username": "admin", "password": "runvard", "remember": "0"},
                )
                self.assertEqual(login.status_code, 200)
                token = self.client.post(
                    "/api/confirm-token",
                    data={"action": "auth-toggle", "target": "banana"},
                )
                self.assertEqual(token.status_code, 200)

                response = self.client.post(
                    "/api/auth/toggle",
                    data={
                        "enabled": "banana",
                        "confirm_token": token.json()["token"],
                    },
                )
                auth_config = Path(self.server.AUTH_CFG_FILE).read_text(
                    encoding="utf-8"
                )
        finally:
            self.server.AUTH_CFG_FILE = old_auth_config

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["ok"], False)
        self.assertIn("Login-Status", response.json()["error"])
        self.assertIn('"login_enabled": true', auth_config)

    def test_file_mutations_require_confirm_token(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        cases = [
            ("/api/files/write", {"path": "/tmp/runvard-test.txt", "content": "x"}),
            ("/api/files/rename", {"path": "/tmp/runvard-test.txt", "new_name": "renamed.txt"}),
            ("/api/files/copy", {"src": "/tmp/runvard-test.txt", "dst_dir": "/tmp"}),
            ("/api/files/move", {"src": "/tmp/runvard-test.txt", "dst_dir": "/tmp"}),
            ("/api/files/mkdir", {"path": "/tmp", "name": "runvard-dir"}),
            ("/api/files/delete", {"path": "/tmp/runvard-test.txt"}),
            (
                "/api/files/zip",
                {"paths": "/tmp/runvard-test.txt", "output": "/tmp/runvard-test.zip"},
            ),
            (
                "/api/files/unzip",
                {"path": "/tmp/runvard-test.zip", "dst_dir": "/tmp"},
            ),
            (
                "/api/files/job",
                {"action": "copy", "paths": "/tmp/runvard-test.txt", "dst_dir": "/tmp"},
            ),
            ("/api/files/trash/restore", {"item_id": "missing"}),
            ("/api/files/trash/empty", {}),
            ("/api/files/share", {"path": "/tmp/runvard-test.txt"}),
            ("/api/files/shares/delete", {"token": "missing"}),
        ]

        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["ok"], False)
                self.assertIn("confirmation token", response.json()["error"])

        upload = self.client.post(
            "/api/files/upload",
            data={"path": "/tmp"},
            files={"file": ("runvard-test.txt", b"x", "text/plain")},
        )
        self.assertEqual(upload.status_code, 403)
        self.assertEqual(upload.json()["ok"], False)
        self.assertIn("confirmation token", upload.json()["error"])

    def test_file_upload_rejects_invalid_filename(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        with tempfile.TemporaryDirectory() as tmp:
            token = self.client.post(
                "/api/confirm-token",
                data={"action": "files-upload", "target": tmp},
            )
            self.assertEqual(token.status_code, 200)

            response = self.client.post(
                "/api/files/upload",
                data={"path": tmp, "confirm_token": token.json()["token"]},
                files={"file": ("..", b"x", "text/plain")},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["ok"], False)
        self.assertIn("filename", response.json()["error"].lower())

    def test_file_job_action_is_validated_before_starting_job(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_start_job = self.server.files.start_job

        def fake_start_job(action, paths, dst_dir="", output=""):
            calls.append((action, paths, dst_dir, output))
            return {"ok": True, "id": "job1"}

        self.server.files.start_job = fake_start_job
        try:
            token = self.client.post(
                "/api/confirm-token",
                data={"action": "files-job:copy", "target": "/tmp/a"},
            )
            self.assertEqual(token.status_code, 200)
            valid = self.client.post(
                "/api/files/job",
                data={
                    "action": "copy",
                    "paths": "/tmp/a",
                    "dst_dir": "/tmp",
                    "confirm_token": token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "files-job:copy", "target": "/tmp/b"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/files/job",
                data={
                    "action": "sync",
                    "paths": "/tmp/b",
                    "dst_dir": "/tmp",
                    "confirm_token": invalid_token.json()["token"],
                },
            )

            bad_paths_token = self.client.post(
                "/api/confirm-token",
                data={"action": "files-job:copy", "target": "/tmp/a||/tmp/b"},
            )
            self.assertEqual(bad_paths_token.status_code, 200)
            bad_paths = self.client.post(
                "/api/files/job",
                data={
                    "action": "copy",
                    "paths": "/tmp/a||/tmp/b",
                    "dst_dir": "/tmp",
                    "confirm_token": bad_paths_token.json()["token"],
                },
            )
        finally:
            self.server.files.start_job = original_start_job

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(calls, [("copy", ["/tmp/a"], "/tmp", "")])
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("File job action", invalid.json()["error"])
        self.assertEqual(bad_paths.status_code, 400)
        self.assertEqual(bad_paths.json()["ok"], False)
        self.assertIn("Dateiauswahl", bad_paths.json()["error"])

    def test_install_update_routes_require_confirm_token_before_jobs(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        cases = [
            ("/api/sysmgr/updates/apply", {}),
            ("/api/sysmgr/runvard-update/apply", {}),
            ("/api/sysmgr/packages/install", {"name": "curl"}),
            ("/api/sysmgr/packages/remove", {"name": "curl"}),
            ("/api/apps/install", {"app_id": "demo", "content": "services: {}\n"}),
            ("/api/apps/action", {"app_id": "demo", "action": "start"}),
            ("/api/apps/action", {"app_id": "demo", "action": "stop"}),
            ("/api/apps/action", {"app_id": "demo", "action": "restart"}),
            ("/api/apps/action", {"app_id": "demo", "action": "update"}),
            ("/api/apps/action", {"app_id": "demo", "action": "down"}),
        ]

        for path, data in cases:
            with self.subTest(path=path, data=data):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["ok"], False)
                self.assertIn("confirmation token", response.json()["error"])

    def test_package_routes_validate_name_before_starting_jobs(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        from modules import jobs

        calls = []
        original_start_job = jobs.start_job

        def fake_start_job(*args, **kwargs):
            calls.append((args, kwargs))
            return {"ok": True, "id": "pkg-job"}

        jobs.start_job = fake_start_job
        try:
            install_token = self.client.post(
                "/api/confirm-token",
                data={"action": "sysmgr-package-install", "target": "curl"},
            )
            self.assertEqual(install_token.status_code, 200)
            install = self.client.post(
                "/api/sysmgr/packages/install",
                data={"name": "curl", "confirm_token": install_token.json()["token"]},
            )

            remove_token = self.client.post(
                "/api/confirm-token",
                data={"action": "sysmgr-package-remove", "target": "curl"},
            )
            self.assertEqual(remove_token.status_code, 200)
            remove = self.client.post(
                "/api/sysmgr/packages/remove",
                data={"name": "curl", "confirm_token": remove_token.json()["token"]},
            )

            bad_install_token = self.client.post(
                "/api/confirm-token",
                data={"action": "sysmgr-package-install", "target": "-bad"},
            )
            self.assertEqual(bad_install_token.status_code, 200)
            bad_install = self.client.post(
                "/api/sysmgr/packages/install",
                data={
                    "name": "-bad",
                    "confirm_token": bad_install_token.json()["token"],
                },
            )

            bad_remove_token = self.client.post(
                "/api/confirm-token",
                data={"action": "sysmgr-package-remove", "target": "bad/pkg"},
            )
            self.assertEqual(bad_remove_token.status_code, 200)
            bad_remove = self.client.post(
                "/api/sysmgr/packages/remove",
                data={
                    "name": "bad/pkg",
                    "confirm_token": bad_remove_token.json()["token"],
                },
            )
        finally:
            jobs.start_job = original_start_job

        self.assertEqual(install.status_code, 200)
        self.assertEqual(remove.status_code, 200)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0][0], "apt-install")
        self.assertEqual(calls[0][0][2], "curl")
        self.assertEqual(calls[1][0][0], "apt-remove")
        self.assertEqual(calls[1][0][2], "curl")
        self.assertEqual(bad_install.status_code, 400)
        self.assertEqual(bad_install.json()["ok"], False)
        self.assertIn("package name", bad_install.json()["error"])
        self.assertEqual(bad_remove.status_code, 400)
        self.assertEqual(bad_remove.json()["ok"], False)
        self.assertIn("package name", bad_remove.json()["error"])

    def test_docker_compose_mutations_require_confirm_token(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        cases = [
            ("/api/docker/action", {"container_id": "abc123", "action": "start"}),
            (
                "/api/docker/create",
                {"image": "nginx:latest", "name": "web", "restart": "no"},
            ),
            ("/api/docker/update", {"container_id": "abc123", "cpus": "1"}),
            ("/api/docker/images/pull", {"name": "nginx:latest"}),
            (
                "/api/docker/compose/save",
                {"name": "demo", "content": "services: {}\n"},
            ),
            ("/api/docker/compose/action", {"name": "demo", "action": "up"}),
        ]

        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["ok"], False)
                self.assertIn("confirmation token", response.json()["error"])

    def test_lifecycle_action_values_are_validated_before_host_actions(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_container_action = self.server.docker_mgr.container_action
        original_compose_action = self.server.docker_mgr.compose_action
        original_service_action = self.server.services.service_action
        original_app_action = self.server.apps.action

        def fake_container_action(container_id, action):
            calls.append(("container", container_id, action))
            return {"ok": True}

        def fake_compose_action(name, action):
            calls.append(("compose", name, action))
            return {"ok": True}

        def fake_service_action(name, action):
            calls.append(("service", name, action))
            return {"ok": True}

        def fake_app_action(app_id, action):
            calls.append(("app", app_id, action))
            return {"ok": True}

        self.server.docker_mgr.container_action = fake_container_action
        self.server.docker_mgr.compose_action = fake_compose_action
        self.server.services.service_action = fake_service_action
        self.server.apps.action = fake_app_action
        try:
            container_token = self.client.post(
                "/api/confirm-token",
                data={"action": "docker-container-action:start", "target": "abc123"},
            )
            self.assertEqual(container_token.status_code, 200)
            container = self.client.post(
                "/api/docker/action",
                data={
                    "container_id": "abc123",
                    "action": "start",
                    "confirm_token": container_token.json()["token"],
                },
            )

            compose_token = self.client.post(
                "/api/confirm-token",
                data={"action": "docker-compose-action:up", "target": "demo"},
            )
            self.assertEqual(compose_token.status_code, 200)
            compose = self.client.post(
                "/api/docker/compose/action",
                data={
                    "name": "demo",
                    "action": "up",
                    "confirm_token": compose_token.json()["token"],
                },
            )

            service_token = self.client.post(
                "/api/confirm-token",
                data={"action": "service-action:restart", "target": "ssh.service"},
            )
            self.assertEqual(service_token.status_code, 200)
            service = self.client.post(
                "/api/services/action",
                data={
                    "name": "ssh.service",
                    "action": "restart",
                    "confirm_token": service_token.json()["token"],
                },
            )

            app_token = self.client.post(
                "/api/confirm-token",
                data={"action": "apps-action:update", "target": "demo"},
            )
            self.assertEqual(app_token.status_code, 200)
            app = self.client.post(
                "/api/apps/action",
                data={
                    "app_id": "demo",
                    "action": "update",
                    "confirm_token": app_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "docker-container-action:start", "target": "abc123"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/docker/action",
                data={
                    "container_id": "abc123",
                    "action": "pause",
                    "confirm_token": invalid_token.json()["token"],
                },
            )
        finally:
            self.server.docker_mgr.container_action = original_container_action
            self.server.docker_mgr.compose_action = original_compose_action
            self.server.services.service_action = original_service_action
            self.server.apps.action = original_app_action

        self.assertEqual(container.status_code, 200)
        self.assertEqual(compose.status_code, 200)
        self.assertEqual(service.status_code, 200)
        self.assertEqual(app.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("container", "abc123", "start"),
                ("compose", "demo", "up"),
                ("service", "ssh.service", "restart"),
                ("app", "demo", "update"),
            ],
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("Docker action", invalid.json()["error"])

    def test_compose_env_enabled_boolean_value_is_validated(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_save_compose = self.server.docker_mgr.save_compose

        def fake_save_compose(name, content, env_enabled=False, env_content=""):
            calls.append((name, content, env_enabled, env_content))
            return {"ok": True}

        self.server.docker_mgr.save_compose = fake_save_compose
        try:
            valid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "docker-compose-save", "target": "demo"},
            )
            self.assertEqual(valid_token.status_code, 200)
            valid = self.client.post(
                "/api/docker/compose/save",
                data={
                    "name": "demo",
                    "content": "services: {}\n",
                    "env_enabled": "1",
                    "env_content": "A=B\n",
                    "confirm_token": valid_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "docker-compose-save", "target": "badenv"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/docker/compose/save",
                data={
                    "name": "badenv",
                    "content": "services: {}\n",
                    "env_enabled": "enabled",
                    "confirm_token": invalid_token.json()["token"],
                },
            )

            retry = self.client.post(
                "/api/docker/compose/save",
                data={
                    "name": "badenv",
                    "content": "services: {}\n",
                    "env_enabled": "0",
                    "confirm_token": invalid_token.json()["token"],
                },
            )
        finally:
            self.server.docker_mgr.save_compose = original_save_compose

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("demo", "services: {}\n", True, "A=B\n"),
                ("badenv", "services: {}\n", False, ""),
            ],
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("Env-Wert", invalid.json()["error"])

    def test_host_policy_routes_require_confirm_token_before_host_tools(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        cases = [
            ("/api/services/action", {"name": "ssh.service", "action": "restart"}),
            (
                "/api/monitoring/alerts/add",
                {"metric": "cpu", "threshold": "90", "channel": "log"},
            ),
            (
                "/api/sysmgr/cron/add",
                {"schedule": "0 3 * * *", "command": "/usr/bin/true"},
            ),
            ("/api/sysmgr/hostname", {"name": "runvard-test"}),
            (
                "/api/sysmgr/apparmor/set",
                {"profile": "/usr/sbin/nginx", "mode": "complain"},
            ),
            (
                "/api/sysmgr/unattended/set",
                {"enable": "true", "auto_reboot": "false", "reboot_time": "02:00"},
            ),
            ("/api/sysmgr/tuned/set", {"profile": "balanced"}),
            ("/api/sysmgr/kdump/action", {"action": "enable"}),
            ("/api/sysmgr/sosreport/run", {}),
        ]

        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["ok"], False)
                self.assertIn("confirmation token", response.json()["error"])

    def test_system_choice_values_are_validated_before_host_actions(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_apparmor_set = self.server.system_mgr.apparmor_set
        original_kdump_action = self.server.system_mgr.kdump_action

        def fake_apparmor_set(profile, mode):
            calls.append(("apparmor", profile, mode))
            return {"ok": True}

        def fake_kdump_action(action):
            calls.append(("kdump", action))
            return {"ok": True}

        self.server.system_mgr.apparmor_set = fake_apparmor_set
        self.server.system_mgr.kdump_action = fake_kdump_action
        try:
            apparmor_token = self.client.post(
                "/api/confirm-token",
                data={"action": "sysmgr-apparmor-set", "target": "/usr/sbin/nginx"},
            )
            self.assertEqual(apparmor_token.status_code, 200)
            apparmor = self.client.post(
                "/api/sysmgr/apparmor/set",
                data={
                    "profile": "/usr/sbin/nginx",
                    "mode": "complain",
                    "confirm_token": apparmor_token.json()["token"],
                },
            )

            kdump_token = self.client.post(
                "/api/confirm-token",
                data={"action": "sysmgr-kdump-action:enable", "target": "kdump"},
            )
            self.assertEqual(kdump_token.status_code, 200)
            kdump = self.client.post(
                "/api/sysmgr/kdump/action",
                data={
                    "action": "enable",
                    "confirm_token": kdump_token.json()["token"],
                },
            )

            invalid_apparmor_token = self.client.post(
                "/api/confirm-token",
                data={"action": "sysmgr-apparmor-set", "target": "/usr/sbin/nginx"},
            )
            self.assertEqual(invalid_apparmor_token.status_code, 200)
            invalid_apparmor = self.client.post(
                "/api/sysmgr/apparmor/set",
                data={
                    "profile": "/usr/sbin/nginx",
                    "mode": "audit",
                    "confirm_token": invalid_apparmor_token.json()["token"],
                },
            )

            invalid_kdump_token = self.client.post(
                "/api/confirm-token",
                data={"action": "sysmgr-kdump-action:enable", "target": "kdump"},
            )
            self.assertEqual(invalid_kdump_token.status_code, 200)
            invalid_kdump = self.client.post(
                "/api/sysmgr/kdump/action",
                data={
                    "action": "reload",
                    "confirm_token": invalid_kdump_token.json()["token"],
                },
            )
        finally:
            self.server.system_mgr.apparmor_set = original_apparmor_set
            self.server.system_mgr.kdump_action = original_kdump_action

        self.assertEqual(apparmor.status_code, 200)
        self.assertEqual(kdump.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("apparmor", "/usr/sbin/nginx", "complain"),
                ("kdump", "enable"),
            ],
        )
        self.assertEqual(invalid_apparmor.status_code, 400)
        self.assertEqual(invalid_apparmor.json()["ok"], False)
        self.assertIn("AppArmor mode", invalid_apparmor.json()["error"])
        self.assertEqual(invalid_kdump.status_code, 400)
        self.assertEqual(invalid_kdump.json()["ok"], False)
        self.assertIn("Kdump action", invalid_kdump.json()["error"])

    def test_monitoring_alert_threshold_is_validated_before_save(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_add_alert_rule = self.server.monitoring.add_alert_rule

        def fake_add_alert_rule(metric, threshold, channel):
            calls.append((metric, threshold, channel))
            return {"ok": True}

        self.server.monitoring.add_alert_rule = fake_add_alert_rule
        try:
            valid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "monitoring-alert-add", "target": "cpu"},
            )
            self.assertEqual(valid_token.status_code, 200)
            valid = self.client.post(
                "/api/monitoring/alerts/add",
                data={
                    "metric": "cpu",
                    "threshold": "90.5",
                    "channel": "log",
                    "confirm_token": valid_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={"action": "monitoring-alert-add", "target": "cpu"},
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/monitoring/alerts/add",
                data={
                    "metric": "cpu",
                    "threshold": "100001",
                    "channel": "log",
                    "confirm_token": invalid_token.json()["token"],
                },
            )
        finally:
            self.server.monitoring.add_alert_rule = original_add_alert_rule

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(calls, [("cpu", 90.5, "log")])
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("Alert threshold", invalid.json()["error"])

    def test_unattended_boolean_values_are_validated(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        calls = []
        original_unattended_set = self.server.system_mgr.unattended_set

        def fake_unattended_set(enable, auto_reboot=False, reboot_time="02:00"):
            calls.append((enable, auto_reboot, reboot_time))
            return {"ok": True}

        self.server.system_mgr.unattended_set = fake_unattended_set
        try:
            valid_token = self.client.post(
                "/api/confirm-token",
                data={
                    "action": "sysmgr-unattended-set",
                    "target": "unattended-upgrades",
                },
            )
            self.assertEqual(valid_token.status_code, 200)
            valid = self.client.post(
                "/api/sysmgr/unattended/set",
                data={
                    "enable": "1",
                    "auto_reboot": "false",
                    "reboot_time": "02:00",
                    "confirm_token": valid_token.json()["token"],
                },
            )

            invalid_token = self.client.post(
                "/api/confirm-token",
                data={
                    "action": "sysmgr-unattended-set",
                    "target": "unattended-upgrades",
                },
            )
            self.assertEqual(invalid_token.status_code, 200)
            invalid = self.client.post(
                "/api/sysmgr/unattended/set",
                data={
                    "enable": "enabled",
                    "auto_reboot": "false",
                    "reboot_time": "02:00",
                    "confirm_token": invalid_token.json()["token"],
                },
            )
        finally:
            self.server.system_mgr.unattended_set = original_unattended_set

        self.assertEqual(valid.status_code, 200)
        self.assertEqual(calls, [(True, False, "02:00")])
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.json()["ok"], False)
        self.assertIn("Unattended-Wert", invalid.json()["error"])

    def test_monitoring_filter_validation_is_json_safe(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        response = self.client.get("/api/monitoring/logs", params={"unit": "../bad"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("Ungueltige Unit", response.json()["logs"])

    def test_readonly_cannot_force_expensive_refresh_gets(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)
        token = self.client.post(
            "/api/confirm-token",
            data={"action": "account-add", "target": "viewer"},
        )
        self.assertEqual(token.status_code, 200)
        created = self.client.post(
            "/api/accounts/add",
            data={
                "username": "viewer",
                "password": "secret",
                "role": "readonly",
                "confirm_token": token.json()["token"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.client.cookies.clear()

        viewer = self.client.post(
            "/api/login",
            data={"username": "viewer", "password": "secret", "remember": "0"},
        )
        self.assertEqual(viewer.status_code, 200)

        updates = self.client.get("/api/sysmgr/updates", params={"refresh": "true"})
        self.assertEqual(updates.status_code, 403)
        apps = self.client.get("/api/apps/check-updates", params={"force": "true"})
        self.assertEqual(apps.status_code, 403)

    def test_readonly_cannot_mutate_global_dashboard(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)
        token = self.client.post(
            "/api/confirm-token",
            data={"action": "account-add", "target": "viewer_dashboard"},
        )
        self.assertEqual(token.status_code, 200)
        created = self.client.post(
            "/api/accounts/add",
            data={
                "username": "viewer_dashboard",
                "password": "secret",
                "role": "readonly",
                "confirm_token": token.json()["token"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.client.cookies.clear()

        viewer = self.client.post(
            "/api/login",
            data={
                "username": "viewer_dashboard",
                "password": "secret",
                "remember": "0",
            },
        )
        self.assertEqual(viewer.status_code, 200)

        cases = [
            ("/api/dashboard/add", {"tile_type": "custom", "tile_id": "x"}),
            ("/api/dashboard/remove", {"tile_id": "x"}),
            ("/api/dashboard/order", {"order": "[]"}),
            ("/api/dashboard/toggle-url", {"tile_id": "x", "show": "true"}),
            ("/api/dashboard/update", {"tile_id": "x", "name": "X"}),
        ]

        for path, data in cases:
            with self.subTest(path=path):
                response = self.client.post(path, data=data)
                self.assertEqual(response.status_code, 403)

    def test_dashboard_mutation_validation_errors_are_json(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        bad_type = self.client.post(
            "/api/dashboard/add",
            data={"tile_type": "bad", "tile_id": "demo"},
        )
        self.assertEqual(bad_type.status_code, 400)
        self.assertEqual(bad_type.json()["ok"], False)

        bad_id = self.client.post(
            "/api/dashboard/remove",
            data={"tile_id": "../bad"},
        )
        self.assertEqual(bad_id.status_code, 400)
        self.assertEqual(bad_id.json()["ok"], False)

        old_dash_file = self.server.dashboard.DASH_FILE
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self.server.dashboard.DASH_FILE = os.path.join(tmp, "dashboard.json")
                initial = {
                    "tiles": [
                        {
                            "id": "first",
                            "type": "custom",
                            "order": 0,
                            "show_url": False,
                        },
                        {"id": "second", "type": "custom", "order": 1},
                    ]
                }
                Path(self.server.dashboard.DASH_FILE).write_text(
                    self.server.json.dumps(initial, indent=2),
                    encoding="utf-8",
                )

                bad_order_json = self.client.post(
                    "/api/dashboard/order",
                    data={"order": "{not-json"},
                )
                bad_order_shape = self.client.post(
                    "/api/dashboard/order",
                    data={"order": "{}"},
                )
                add_port = self.client.post(
                    "/api/dashboard/add",
                    data={
                        "tile_type": "compose",
                        "tile_id": "compose:demo",
                        "name": "Demo",
                        "port": "8080",
                    },
                )
                bad_port = self.client.post(
                    "/api/dashboard/add",
                    data={
                        "tile_type": "compose",
                        "tile_id": "compose:bad",
                        "name": "Bad",
                        "port": "70000",
                    },
                )
                show_true = self.client.post(
                    "/api/dashboard/toggle-url",
                    data={"tile_id": "first", "show": "true"},
                )
                bad_show = self.client.post(
                    "/api/dashboard/toggle-url",
                    data={"tile_id": "first", "show": "maybe"},
                )
                dashboard_data = self.server.json.loads(
                    Path(self.server.dashboard.DASH_FILE).read_text(encoding="utf-8")
                )
        finally:
            self.server.dashboard.DASH_FILE = old_dash_file

        self.assertEqual(bad_order_json.status_code, 400)
        self.assertEqual(bad_order_json.json()["ok"], False)
        self.assertIn("Dashboard-Reihenfolge", bad_order_json.json()["error"])
        self.assertEqual(bad_order_shape.status_code, 400)
        self.assertEqual(bad_order_shape.json()["ok"], False)
        self.assertIn("Dashboard-Reihenfolge", bad_order_shape.json()["error"])
        self.assertEqual(add_port.status_code, 200)
        self.assertEqual(bad_port.status_code, 400)
        self.assertEqual(bad_port.json()["ok"], False)
        self.assertIn("Dashboard port", bad_port.json()["error"])
        self.assertEqual(show_true.status_code, 200)
        self.assertEqual(bad_show.status_code, 400)
        self.assertEqual(bad_show.json()["ok"], False)
        self.assertIn("show-Wert", bad_show.json()["error"])
        self.assertEqual(dashboard_data["tiles"][0]["id"], "first")
        self.assertEqual(dashboard_data["tiles"][1]["id"], "second")
        self.assertEqual(dashboard_data["tiles"][2]["id"], "compose:demo")
        self.assertEqual(dashboard_data["tiles"][2]["port"], 8080)
        self.assertTrue(dashboard_data["tiles"][0]["show_url"])

    def test_readonly_cannot_issue_confirm_tokens_or_delete(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)
        setup_token = self.client.post(
            "/api/confirm-token",
            data={"action": "account-add", "target": "viewer_confirm"},
        )
        self.assertEqual(setup_token.status_code, 200)
        created = self.client.post(
            "/api/accounts/add",
            data={
                "username": "viewer_confirm",
                "password": "secret",
                "role": "readonly",
                "confirm_token": setup_token.json()["token"],
            },
        )
        self.assertEqual(created.status_code, 200)
        self.client.cookies.clear()

        viewer = self.client.post(
            "/api/login",
            data={
                "username": "viewer_confirm",
                "password": "secret",
                "remember": "0",
            },
        )
        self.assertEqual(viewer.status_code, 200)

        token = self.client.post(
            "/api/confirm-token",
            data={"action": "docker-volume-remove", "target": "data"},
        )
        self.assertEqual(token.status_code, 403)
        self.assertEqual(token.json()["status"], 403)

        delete = self.client.post(
            "/api/docker/volumes/remove",
            data={"name": "data", "confirm_token": "not-a-real-token"},
        )
        self.assertEqual(delete.status_code, 403)
        self.assertEqual(delete.json()["status"], 403)

    def test_compose_validation_errors_are_not_500s(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        response = self.client.post(
            "/api/docker/compose/save",
            data={
                "name": "badmount",
                "content": "services:\n  app:\n    image: alpine\n    volumes:\n      - /:/host\n",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["ok"], False)

    def test_btop_page_is_served_from_static_file(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        response = self.client.get("/btop")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Missing terminal browser libraries", response.text)

    def test_ftp_status_get_degrades_without_systemctl_stdout(self) -> None:
        login = self.client.post(
            "/api/login",
            data={"username": "admin", "password": "runvard", "remember": "0"},
        )
        self.assertEqual(login.status_code, 200)

        old_run = self.server.shares._run
        try:
            self.server.shares._run = lambda cmd: {
                "ok": False,
                "stderr": "systemctl failed",
            }
            response = self.client.get("/api/shares/ftp")
        finally:
            self.server.shares._run = old_run

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["active"])
        self.assertEqual(response.json()["error"], "systemctl failed")
