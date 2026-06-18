"""Dependency-light contract checks for the runvard codebase."""

from __future__ import annotations

import ast
import builtins
import os
import re
import importlib
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _server_post_routes() -> set[str]:
    server_routes = _server_post_route_functions()
    return set(server_routes)


def _server_post_route_functions() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    server = ast.parse((ROOT / "server.py").read_text(encoding="utf-8"))
    route_functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in server.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "post"
                and decorator.args
                and isinstance(decorator.args[0], ast.Constant)
                and isinstance(decorator.args[0].value, str)
            ):
                continue
            route = decorator.args[0].value
            route_functions[route] = node
    return route_functions


def _protected_contract_routes() -> set[str]:
    source = ast.parse(
        (ROOT / "tests" / "test_static_contracts.py").read_text(encoding="utf-8")
    )
    routes: list[str] | None = None
    for node in ast.walk(source):
        if not (
            isinstance(node, ast.FunctionDef)
            and node.name == "test_confirm_protected_routes_are_not_called_directly"
        ):
            continue
        for child in node.body:
            if not isinstance(child, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "protected_routes"
                for target in child.targets
            ):
                continue
            routes = ast.literal_eval(child.value)
            break
    if routes is None:
        raise AssertionError("protected_routes contract list not found")
    return {"/api" + route for route in routes}


class RuntimeConfigTests(unittest.TestCase):
    def test_data_path_uses_runvard_data_dir(self) -> None:
        from modules.runtime import data_dir, data_path

        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("RUNVARD_DATA_DIR")
            os.environ["RUNVARD_DATA_DIR"] = tmp
            try:
                self.assertEqual(data_dir(), tmp)
                self.assertEqual(data_path("users.json"), os.path.join(tmp, "users.json"))
                self.assertEqual(data_path("trash", ".meta.json"), os.path.join(tmp, "trash", ".meta.json"))
                with self.assertRaises(ValueError):
                    data_path("/tmp/escape")
                with self.assertRaises(ValueError):
                    data_path("..", "escape")
            finally:
                if old is None:
                    os.environ.pop("RUNVARD_DATA_DIR", None)
                else:
                    os.environ["RUNVARD_DATA_DIR"] = old

    def test_data_dir_normalizes_relative_override(self) -> None:
        from modules.runtime import data_dir

        old = os.environ.get("RUNVARD_DATA_DIR")
        os.environ["RUNVARD_DATA_DIR"] = "relative-runvard-data"
        try:
            self.assertEqual(
                data_dir(),
                os.path.abspath("relative-runvard-data"),
            )
        finally:
            if old is None:
                os.environ.pop("RUNVARD_DATA_DIR", None)
            else:
                os.environ["RUNVARD_DATA_DIR"] = old

    def test_persistent_module_paths_follow_runtime_data_dir_on_reload(self) -> None:
        modules_to_paths = [
            ("modules.accounts", "STORE", "users.json"),
            ("modules.apps", "APPS_DIR", "apps"),
            ("modules.apps", "UPDATE_CACHE", "apps_updates.json"),
            ("modules.backup", "CONFIG", "backup_jobs.json"),
            ("modules.backup", "HISTORY", "backup_history.json"),
            ("modules.dashboard", "DASH_FILE", "dashboard.json"),
            ("modules.dashboard", "APPS_DIR", "apps"),
            ("modules.dashboard", "COMPOSE_DIR", "compose"),
            ("modules.docker_mgr", "COMPOSE_DIR", "compose"),
            ("modules.files", "TRASH", "trash"),
            ("modules.files", "SHAREDB", "shares.json"),
            ("modules.files", "JOBDB", "file_jobs.json"),
            ("modules.monitoring", "ALERT_CONFIG", "alerts.json"),
            ("modules.monitoring", "ALERT_HISTORY", "alert_history.json"),
            ("modules.security", "CERT_DIR", "certs"),
            ("modules.system_mgr", "RUNVARD_UPDATE_LOG", "runvard-update.log"),
        ]

        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("RUNVARD_DATA_DIR")
            os.environ["RUNVARD_DATA_DIR"] = tmp
            reloaded = []
            try:
                for module_name, attr, suffix in modules_to_paths:
                    module = importlib.import_module(module_name)
                    module = importlib.reload(module)
                    reloaded.append(module)
                    self.assertEqual(
                        getattr(module, attr),
                        os.path.join(tmp, suffix),
                        f"{module_name}.{attr}",
                    )
            finally:
                if old is None:
                    os.environ.pop("RUNVARD_DATA_DIR", None)
                else:
                    os.environ["RUNVARD_DATA_DIR"] = old
                for module in reloaded:
                    importlib.reload(module)


class JobRunnerTests(unittest.TestCase):
    def test_ok_false_results_are_failed_jobs(self) -> None:
        from modules import jobs

        class ImmediateThread:
            def __init__(self, target, daemon=False):
                self.target = target
                self.daemon = daemon

            def start(self):
                self.target()

        old_thread = jobs.threading.Thread
        old_jobs = dict(jobs._jobs)
        try:
            jobs._jobs.clear()
            jobs.threading.Thread = ImmediateThread

            failed_id = jobs.start_job("apt-install", lambda: {"ok": False, "stderr": "apt failed"})["job_id"]
            succeeded_id = jobs.start_job("noop", lambda: {"ok": True})["job_id"]

            failed = jobs.get_job(failed_id)
            succeeded = jobs.get_job(succeeded_id)
        finally:
            jobs.threading.Thread = old_thread
            jobs._jobs.clear()
            jobs._jobs.update(old_jobs)

        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["result"]["stderr"], "apt failed")
        self.assertEqual(succeeded["status"], "succeeded")


class FileManagerTests(unittest.TestCase):
    def test_file_helpers_use_configured_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("RUNVARD_DATA_DIR")
            os.environ["RUNVARD_DATA_DIR"] = tmp
            try:
                import modules.files as files

                files = importlib.reload(files)
                self.assertEqual(files.TRASH, os.path.join(tmp, "trash"))
                self.assertEqual(files.SHAREDB, os.path.join(tmp, "shares.json"))
            finally:
                if old is None:
                    os.environ.pop("RUNVARD_DATA_DIR", None)
                else:
                    os.environ["RUNVARD_DATA_DIR"] = old
                import modules.files as files

                importlib.reload(files)

    def test_mkdir_rejects_traversal_names(self) -> None:
        from modules import files

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                files.mkdir(tmp, "../escape")

    def test_share_links_reject_blocked_paths(self) -> None:
        from modules import files

        with self.assertRaises(PermissionError):
            files.create_share_link("/proc/cpuinfo")

    def test_share_links_only_allow_regular_files(self) -> None:
        from modules import files

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                files.create_share_link(tmp)

    def test_share_tokens_are_validated(self) -> None:
        from modules import files

        self.assertIsNone(files.resolve_share("../bad"))
        with self.assertRaises(ValueError):
            files.delete_share("../bad")

    def test_file_jobs_reject_blocked_paths_before_thread_start(self) -> None:
        from modules import files

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(PermissionError):
                files.start_job("copy", ["/proc/cpuinfo"], tmp)
            with self.assertRaises(PermissionError):
                files.start_job("zip", [tmp], output="/etc/runvard.zip")

    def test_directory_copy_and_zip_validate_symlink_targets(self) -> None:
        from modules import files

        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            dest = Path(tmp) / "dest"
            output = Path(tmp) / "out.zip"
            source.mkdir()
            try:
                os.symlink("/proc/cpuinfo", source / "cpuinfo-link")
            except (AttributeError, NotImplementedError, OSError) as exc:
                self.skipTest(f"symlinks unavailable: {exc}")

            with self.assertRaises(PermissionError):
                files.copy_item(str(source), str(dest))
            with self.assertRaises(PermissionError):
                files.make_zip([str(source)], str(output))

    def test_trash_restore_rejects_protected_original_path(self) -> None:
        from modules import files

        item_id = "restorebad"
        old_meta = files._load_meta
        old_save = files._save_meta
        try:
            files._load_meta = lambda: [
                {"id": item_id, "original": "/etc/runvard.conf", "name": "runvard.conf"}
            ]
            files._save_meta = lambda data: None
            with self.assertRaises(PermissionError):
                files.restore_trash(item_id)
        finally:
            files._load_meta = old_meta
            files._save_meta = old_save

    def test_trash_ids_are_validated_before_path_join(self) -> None:
        from modules import files

        old_meta = files._load_meta
        old_save = files._save_meta
        old_trash = files.TRASH
        try:
            files._load_meta = lambda: [
                {"id": "../escape", "original": "/tmp/escape", "name": "escape"}
            ]
            saved = []
            files._save_meta = lambda data: saved.append(data)
            with self.assertRaises(ValueError):
                files.restore_trash("../escape")

            with tempfile.TemporaryDirectory() as tmp:
                files.TRASH = tmp
                outside = Path(tmp).parent / "runvard-trash-escape-test"
                outside.write_text("keep", encoding="utf-8")
                try:
                    files.empty_trash()
                    self.assertTrue(outside.exists())
                finally:
                    outside.unlink(missing_ok=True)
        finally:
            files._load_meta = old_meta
            files._save_meta = old_save
            files.TRASH = old_trash

    def test_file_metadata_corrupt_json_is_quarantined(self) -> None:
        from modules import files

        old_trashmeta = files.TRASHMETA
        old_jobdb = files.JOBDB
        old_sharedb = files.SHAREDB
        try:
            with tempfile.TemporaryDirectory() as tmp:
                files.TRASHMETA = os.path.join(tmp, ".meta.json")
                files.JOBDB = os.path.join(tmp, "file_jobs.json")
                files.SHAREDB = os.path.join(tmp, "shares.json")
                Path(files.TRASHMETA).write_text("{trash", encoding="utf-8")
                Path(files.JOBDB).write_text("[jobs", encoding="utf-8")
                Path(files.SHAREDB).write_text("{shares", encoding="utf-8")

                trash = files._load_meta()
                jobs = files._load_jobs()
                shares = files._load_shares()
                trash_quarantine = list(Path(tmp).glob(".meta.json.corrupt-*"))
                jobs_quarantine = list(Path(tmp).glob("file_jobs.json.corrupt-*"))
                shares_quarantine = list(Path(tmp).glob("shares.json.corrupt-*"))
                trash_content = trash_quarantine[0].read_text(encoding="utf-8")
                jobs_content = jobs_quarantine[0].read_text(encoding="utf-8")
                shares_content = shares_quarantine[0].read_text(encoding="utf-8")
        finally:
            files.TRASHMETA = old_trashmeta
            files.JOBDB = old_jobdb
            files.SHAREDB = old_sharedb

        self.assertEqual(trash, [])
        self.assertEqual(jobs, [])
        self.assertEqual(shares, {})
        self.assertEqual(len(trash_quarantine), 1)
        self.assertEqual(len(jobs_quarantine), 1)
        self.assertEqual(len(shares_quarantine), 1)
        self.assertEqual(trash_content, "{trash")
        self.assertEqual(jobs_content, "[jobs")
        self.assertEqual(shares_content, "{shares")

    def test_remote_mounts_validate_targets_before_mount_command(self) -> None:
        from modules import files

        calls = []
        old_run = files.subprocess.run
        try:
            def fake_run(cmd, capture_output=True, text=True, timeout=30):
                calls.append(cmd)
                raise AssertionError("mount command should not run")

            files.subprocess.run = fake_run

            with self.assertRaises(PermissionError):
                files.mount_smb("nas.local", "media", "/var/lib/docker/runvard")
            with self.assertRaises(ValueError):
                files.mount_smb("nas.local/bad", "media", "/mnt/media")
            with self.assertRaises(ValueError):
                files.mount_smb("nas.local", "media", "/mnt/media", "guest,rw")
            with self.assertRaises(ValueError):
                files.mount_nfs("nas.local", "/srv/media", "/mnt/media", "rw;reboot")
            with self.assertRaises(ValueError):
                files.mount_nfs("nas.local", "srv/media", "/mnt/media")
            with self.assertRaises(ValueError):
                files.mount_nfs("nas.local", "/srv/../etc", "/mnt/media")
        finally:
            files.subprocess.run = old_run

        self.assertEqual(calls, [])

    def test_nfs_mount_preserves_remote_export_path(self) -> None:
        from modules import files

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        calls = []
        old_run = files.subprocess.run
        try:
            def fake_run(cmd, capture_output=True, text=True, timeout=30):
                calls.append(cmd)
                return Result()

            files.subprocess.run = fake_run

            with tempfile.TemporaryDirectory() as tmp:
                mountpoint = os.path.join(tmp, "mnt")
                result = files.mount_nfs("nas.local", "/srv/media", mountpoint)
        finally:
            files.subprocess.run = old_run

        self.assertTrue(result["ok"])
        self.assertEqual(calls[0][3], "nas.local:/srv/media")

    def test_remote_mounts_report_host_tool_failures(self) -> None:
        from modules import files

        old_run = files.subprocess.run
        try:
            with tempfile.TemporaryDirectory() as tmp:
                smb_mount = os.path.join(tmp, "smb")
                nfs_mount = os.path.join(tmp, "nfs")

                def missing_mount(cmd, *args, **kwargs):
                    raise FileNotFoundError(cmd[0])

                files.subprocess.run = missing_mount
                missing = files.mount_smb("nas.local", "media", smb_mount)

                def timed_out(cmd, *args, **kwargs):
                    raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

                files.subprocess.run = timed_out
                timeout = files.mount_nfs("nas.local", "/srv/media", nfs_mount)
        finally:
            files.subprocess.run = old_run

        self.assertFalse(missing["ok"])
        self.assertEqual(missing["returncode"], 127)
        self.assertIn("mount nicht gefunden", missing["stderr"])
        self.assertFalse(timeout["ok"])
        self.assertEqual(timeout["returncode"], 124)
        self.assertIn("timed out", timeout["stderr"])


class HostCommandValidationTests(unittest.TestCase):
    def test_service_names_must_be_systemd_units(self) -> None:
        from modules import services, validators

        self.assertEqual(validators.require_service("ssh.service"), "ssh.service")
        with self.assertRaises(ValueError):
            services.service_action("ssh;reboot", "restart")

    def test_service_reads_report_host_tool_failures(self) -> None:
        from modules import services

        old_run = services._run
        try:
            def fake_run(cmd, timeout=30):
                return {"ok": False, "stdout": "", "stderr": f"{cmd[0]} failed"}

            services._run = fake_run
            listing = services.list_services()
            status = services.service_status("ssh.service")
            logs = services.service_logs("ssh.service", 10)
        finally:
            services._run = old_run

        self.assertFalse(listing["ok"])
        self.assertEqual(listing["services"], [])
        self.assertIn("systemctl failed", listing["stderr"])
        self.assertFalse(status["ok"])
        self.assertIn("systemctl failed", status["stderr"])
        self.assertFalse(logs["ok"])
        self.assertIn("journalctl failed", logs["stderr"])

    def test_backup_rejects_blocked_local_source(self) -> None:
        from modules import backup

        with self.assertRaises(PermissionError):
            backup.add_job("badbackup", "/proc", "/tmp", "manual")

    def test_backup_rejects_unsafe_rsync_paths(self) -> None:
        from modules import backup

        with self.assertRaises(ValueError):
            backup.add_job("badbackup", "-e:evil", "/tmp", "manual")
        with self.assertRaises(ValueError):
            backup.add_job("badbackup", "host:path\n--delete", "/tmp", "manual")
        with self.assertRaises(ValueError):
            backup.add_job("badbackup", "relative/path", "/tmp", "manual")
        with self.assertRaises(ValueError):
            backup.add_job("badbackup", "/tmp", "relative/path", "manual")

    def test_backup_remote_paths_use_central_rsync_validator(self) -> None:
        backup = (ROOT / "modules" / "backup.py").read_text(encoding="utf-8")
        validators = (ROOT / "modules" / "validators.py").read_text(encoding="utf-8")

        self.assertIn("def require_rsync_remote(", validators)
        self.assertIn('validators.require_rsync_remote(path, "backup source")', backup)
        self.assertIn('validators.require_rsync_remote(path, "backup destination")', backup)
        self.assertIn("Backup source must be absolute", backup)
        self.assertIn("Backup destination must be absolute", backup)

    def test_backup_run_records_host_tool_failures(self) -> None:
        from modules import backup

        old_config = backup.CONFIG
        old_history = backup.HISTORY
        old_run = backup.subprocess.run
        try:
            with tempfile.TemporaryDirectory() as tmp:
                backup.CONFIG = os.path.join(tmp, "jobs.json")
                backup.HISTORY = os.path.join(tmp, "history.json")
                backup._save(backup.CONFIG, [
                    {
                        "id": 1,
                        "name": "home",
                        "source": "/tmp/source",
                        "dest": "/tmp/dest",
                        "schedule": "manual",
                        "direction": "push",
                        "last_run": None,
                    }
                ])

                def missing_rsync(cmd, *args, **kwargs):
                    raise FileNotFoundError(cmd[0])

                backup.subprocess.run = missing_rsync
                missing = backup.run_job(1)

                def timed_out(cmd, *args, **kwargs):
                    raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

                backup.subprocess.run = timed_out
                timeout = backup.run_job(1)

                history = backup.get_history()
                jobs = backup.list_jobs()
        finally:
            backup.CONFIG = old_config
            backup.HISTORY = old_history
            backup.subprocess.run = old_run

        self.assertFalse(missing["ok"])
        self.assertIn("rsync nicht gefunden", missing["error"])
        self.assertFalse(timeout["ok"])
        self.assertIn("Zeitlimit", timeout["error"])
        self.assertEqual(len(history), 2)
        self.assertFalse(history[0]["success"])
        self.assertFalse(history[1]["success"])
        self.assertIsNotNone(jobs[0]["last_run"])

    def test_backup_corrupt_json_is_quarantined(self) -> None:
        from modules import backup

        old_config = backup.CONFIG
        old_history = backup.HISTORY
        try:
            with tempfile.TemporaryDirectory() as tmp:
                backup.CONFIG = os.path.join(tmp, "jobs.json")
                backup.HISTORY = os.path.join(tmp, "history.json")
                Path(backup.CONFIG).write_text("{not-json", encoding="utf-8")
                Path(backup.HISTORY).write_text("[not-json", encoding="utf-8")

                jobs = backup.list_jobs()
                history = backup.get_history()
                job_quarantine = list(Path(tmp).glob("jobs.json.corrupt-*"))
                history_quarantine = list(Path(tmp).glob("history.json.corrupt-*"))
                job_content = job_quarantine[0].read_text(encoding="utf-8")
                history_content = history_quarantine[0].read_text(encoding="utf-8")
        finally:
            backup.CONFIG = old_config
            backup.HISTORY = old_history

        self.assertEqual(jobs, [])
        self.assertEqual(history, [])
        self.assertEqual(len(job_quarantine), 1)
        self.assertEqual(len(history_quarantine), 1)
        self.assertEqual(job_content, "{not-json")
        self.assertEqual(history_content, "[not-json")

    def test_shares_validate_names_and_clients(self) -> None:
        from modules import shares

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                shares.add_samba_share("../bad", tmp)
            with self.assertRaises(ValueError):
                shares.add_nfs_export(tmp, clients="*; reboot")
            with self.assertRaises(ValueError):
                shares.add_nfs_export(tmp, clients="*", options="rw)\n/ *(rw")

    def test_nfs_exports_validate_clients_and_options_centrally(self) -> None:
        shares = (ROOT / "modules" / "shares.py").read_text(encoding="utf-8")
        validators = (ROOT / "modules" / "validators.py").read_text(encoding="utf-8")
        server = (ROOT / "server.py").read_text(encoding="utf-8")

        self.assertIn("def require_nfs_clients(", validators)
        self.assertIn("validators.require_nfs_clients(clients)", shares)
        self.assertIn('validators.require_mount_options(options, "NFS options")', shares)
        self.assertNotIn("_CLIENTS_RE", shares)
        self.assertIn('options: str = Form("rw,sync,no_subtree_check")', server)
        self.assertIn("shares.add_nfs_export(path, clients, options)", server)

    def test_share_activation_failures_are_reported(self) -> None:
        from modules import shares

        old_smb = shares.SMB_CONF
        old_nfs = shares.NFS_EXPORTS
        old_run = shares._run
        try:
            with tempfile.TemporaryDirectory() as tmp:
                shares.SMB_CONF = str(Path(tmp) / "smb.conf")
                shares.NFS_EXPORTS = str(Path(tmp) / "exports")

                def fake_run(cmd):
                    return {"ok": False, "stdout": "", "stderr": "service failed"}

                shares._run = fake_run
                samba = shares.add_samba_share("media", tmp)
                nfs = shares.add_nfs_export(tmp)

            self.assertFalse(samba["ok"])
            self.assertIn("service failed", samba["stderr"])
            self.assertFalse(nfs["ok"])
            self.assertIn("service failed", nfs["stderr"])
        finally:
            shares.SMB_CONF = old_smb
            shares.NFS_EXPORTS = old_nfs
            shares._run = old_run

    def test_ftp_status_degrades_when_systemctl_fails(self) -> None:
        from modules import shares

        old_run = shares._run
        try:
            shares._run = lambda cmd: {"ok": False, "stderr": "systemctl failed"}
            status = shares.ftp_status()
        finally:
            shares._run = old_run

        self.assertFalse(status["active"])
        self.assertEqual(status["error"], "systemctl failed")

    def test_compose_project_names_cannot_escape_data_dir(self) -> None:
        from modules import docker_mgr

        with self.assertRaises(ValueError):
            docker_mgr.get_compose("../escape")

    def test_compose_actions_report_subprocess_failures(self) -> None:
        from modules import docker_mgr

        old_compose_dir = docker_mgr.COMPOSE_DIR
        old_run = docker_mgr.subprocess.run
        try:
            with tempfile.TemporaryDirectory() as tmp:
                docker_mgr.COMPOSE_DIR = tmp
                project = Path(tmp) / "demo"
                project.mkdir()
                (project / "docker-compose.yml").write_text("services: {}\n")

                def missing_docker(cmd, *args, **kwargs):
                    raise FileNotFoundError(cmd[0])

                docker_mgr.subprocess.run = missing_docker
                missing = docker_mgr.compose_action("demo", "up")

                def timed_out(cmd, *args, **kwargs):
                    raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

                docker_mgr.subprocess.run = timed_out
                timeout = docker_mgr.compose_action("demo", "down")

                def failed_down(cmd, *args, **kwargs):
                    return subprocess.CompletedProcess(cmd, 1, "", "down failed")

                docker_mgr.subprocess.run = failed_down
                remove = docker_mgr.remove_compose_project("demo")
                still_exists = project.exists()
        finally:
            docker_mgr.COMPOSE_DIR = old_compose_dir
            docker_mgr.subprocess.run = old_run

        self.assertFalse(missing["ok"])
        self.assertIn("docker nicht gefunden", missing["output"])
        self.assertFalse(timeout["ok"])
        self.assertIn("timed out", timeout["output"])
        self.assertFalse(remove["ok"])
        self.assertIn("down failed", remove["output"])
        self.assertTrue(still_exists)

    def test_docker_lists_degrade_when_daemon_is_unavailable(self) -> None:
        from modules import docker_mgr

        old_client = docker_mgr._client
        old_has_docker = docker_mgr.HAS_DOCKER
        try:
            docker_mgr._client = None
            docker_mgr.HAS_DOCKER = False

            containers = docker_mgr.list_containers()
            images = docker_mgr.list_images()
            volumes = docker_mgr.list_volumes()

            self.assertFalse(containers["ok"])
            self.assertEqual(containers["containers"], [])
            self.assertFalse(images["ok"])
            self.assertEqual(images["images"], [])
            self.assertFalse(volumes["ok"])
            self.assertEqual(volumes["volumes"], [])
            self.assertFalse(docker_mgr.pull_image("alpine").get("ok"))
        finally:
            docker_mgr._client = old_client
            docker_mgr.HAS_DOCKER = old_has_docker

    def test_docker_lists_report_sdk_iteration_failures(self) -> None:
        from modules import docker_mgr

        class Containers:
            def list(self, all=False):
                raise RuntimeError("container list failed")

        class Images:
            def list(self):
                raise RuntimeError("image list failed")

        class Volumes:
            def list(self):
                raise RuntimeError("volume list failed")

        class Client:
            containers = Containers()
            images = Images()
            volumes = Volumes()

        old_get_client = docker_mgr._get_client
        try:
            docker_mgr._get_client = lambda: Client()
            containers = docker_mgr.list_containers()
            images = docker_mgr.list_images()
            volumes = docker_mgr.list_volumes()
        finally:
            docker_mgr._get_client = old_get_client

        self.assertFalse(containers["ok"])
        self.assertEqual(containers["containers"], [])
        self.assertIn("container list failed", containers["stderr"])
        self.assertFalse(images["ok"])
        self.assertEqual(images["images"], [])
        self.assertIn("image list failed", images["stderr"])
        self.assertFalse(volumes["ok"])
        self.assertEqual(volumes["volumes"], [])
        self.assertIn("volume list failed", volumes["stderr"])

    def test_docker_rejects_sensitive_host_mounts_before_daemon(self) -> None:
        from modules import docker_mgr

        result = docker_mgr.create_container(
            "alpine", name="demo", volumes="/var/run/docker.sock:/sock"
        )
        self.assertFalse(result["ok"])

    def test_docker_container_refs_are_validated_before_sdk_calls(self) -> None:
        from modules import docker_mgr

        old_get_client = docker_mgr._get_client
        try:
            docker_mgr._get_client = lambda: (_ for _ in ()).throw(
                AssertionError("docker client should not be touched")
            )

            self.assertFalse(
                docker_mgr.container_action("../bad", "start")["ok"]
            )
            self.assertFalse(docker_mgr.container_logs("../bad")["ok"])
            with self.assertRaises(ValueError):
                docker_mgr.container_stats("../bad")
        finally:
            docker_mgr._get_client = old_get_client

    def test_docker_mutation_refs_are_validated_before_sdk_calls(self) -> None:
        from modules import docker_mgr

        old_get_client = docker_mgr._get_client
        try:
            docker_mgr._get_client = lambda: (_ for _ in ()).throw(
                AssertionError("docker client should not be touched")
            )

            self.assertFalse(docker_mgr.create_container("../bad")["ok"])
            self.assertFalse(docker_mgr.update_container("../bad", cpus="1")["ok"])
            self.assertFalse(docker_mgr.pull_image("../bad")["ok"])
            self.assertFalse(docker_mgr.remove_image("../bad")["ok"])
            self.assertFalse(docker_mgr.remove_volume("../bad")["ok"])
        finally:
            docker_mgr._get_client = old_get_client

    def test_docker_sdk_mutation_errors_are_json_results(self) -> None:
        from modules import docker_mgr

        class Images:
            def get(self, image):
                return object()

            def pull(self, image):
                raise RuntimeError("pull failed")

            def remove(self, image, force=False):
                raise RuntimeError("remove image failed")

        class Containers:
            def get(self, container_id):
                raise RuntimeError("container missing")

            def run(self, *args, **kwargs):
                raise RuntimeError("run failed")

        class Volumes:
            def get(self, name):
                raise RuntimeError("volume missing")

        class Client:
            images = Images()
            containers = Containers()
            volumes = Volumes()

        old_get_client = docker_mgr._get_client
        try:
            docker_mgr._get_client = lambda: Client()
            created = docker_mgr.create_container("alpine", name="demo")
            updated = docker_mgr.update_container("abc123", cpus="1")
            pulled = docker_mgr.pull_image("alpine:latest")
            removed_image = docker_mgr.remove_image("sha256:abc")
            removed_volume = docker_mgr.remove_volume("data")
        finally:
            docker_mgr._get_client = old_get_client

        for result in (created, updated, pulled, removed_image, removed_volume):
            self.assertFalse(result["ok"])
            self.assertIn("stderr", result)

    def test_docker_stats_degrades_when_sdk_lookup_fails(self) -> None:
        from modules import docker_mgr

        class Containers:
            def get(self, container_id):
                raise RuntimeError("not found")

        class Client:
            containers = Containers()

        old_get_client = docker_mgr._get_client
        try:
            docker_mgr._get_client = lambda: Client()
            stats = docker_mgr.container_stats("missing")
        finally:
            docker_mgr._get_client = old_get_client

        self.assertFalse(stats["ok"])
        self.assertEqual(stats["cpu_percent"], 0)
        self.assertIn("not found", stats["stderr"])

    def test_sensitive_path_checks_canonicalize_symlinked_roots(self) -> None:
        from modules import validators

        self.assertTrue(
            validators.is_under(
                "/var/run/docker.sock",
                validators.SENSITIVE_HOST_PATHS,
            )
        )
        with self.assertRaises(PermissionError):
            validators.guard_host_mount("/var/run/docker.sock")

    def test_compose_validation_blocks_sensitive_host_binds(self) -> None:
        from modules import compose_utils

        validate_compose_content = compose_utils.validate_compose_content

        validate_compose_content(
            "services:\n  app:\n    image: alpine\n    volumes:\n      - ./data:/data\n"
        )
        validate_compose_content(
            "services:\n  app:\n    image: alpine\n    volumes:\n      - /etc/localtime:/etc/localtime:ro\n"
        )
        with self.assertRaises(PermissionError):
            validate_compose_content(
                "services:\n  app:\n    image: alpine\n    volumes:\n      - /:/host\n"
            )
        with self.assertRaises(PermissionError):
            validate_compose_content(
                "services:\n  app:\n    image: alpine\n    volumes:\n      - /etc/localtime:/etc/localtime\n"
            )
        with self.assertRaises(PermissionError):
            validate_compose_content(
                "services:\n  app:\n    image: alpine\n    volumes:\n      - /var/run/docker.sock:/var/run/docker.sock\n"
            )

        old_yaml = compose_utils.yaml
        try:
            compose_utils.yaml = None
            with self.assertRaises(PermissionError):
                validate_compose_content(
                    "services:\n"
                    "  app:\n"
                    "    image: alpine\n"
                    "    volumes:\n"
                    "      - type: bind\n"
                    "        source: /\n"
                    "        target: /host\n"
                )
        finally:
            compose_utils.yaml = old_yaml

    def test_compose_fallback_validation_blocks_long_syntax_bad_ports(self) -> None:
        from modules import compose_utils

        old_yaml = compose_utils.yaml
        try:
            compose_utils.yaml = None
            with self.assertRaises(ValueError):
                compose_utils.validate_compose_content(
                    "services:\n"
                    "  app:\n"
                    "    image: alpine\n"
                    "    ports:\n"
                    "      - target: 80\n"
                    "        published: 70000\n"
                )
        finally:
            compose_utils.yaml = old_yaml

    def test_catalog_templates_validate_with_declared_privileges(self) -> None:
        from modules import apps

        for app in apps.CATALOG:
            with self.subTest(app=app["id"]):
                apps.validate_compose_content(
                    apps.build_compose(app),
                    allow_docker_socket=bool(app.get("allow_docker_socket")),
                )

    def test_app_dirs_require_slug_ids(self) -> None:
        from modules import apps

        with self.assertRaises(ValueError):
            apps.is_installed("../escape")

    def test_app_actions_report_subprocess_exceptions(self) -> None:
        from modules import apps

        old_apps_dir = apps.APPS_DIR
        old_run = apps.subprocess.run
        try:
            with tempfile.TemporaryDirectory() as tmp:
                apps.APPS_DIR = tmp
                os.makedirs(os.path.join(tmp, "demo"))

                def missing_docker(cmd, *args, **kwargs):
                    raise FileNotFoundError(cmd[0])

                apps.subprocess.run = missing_docker
                missing = apps.action("demo", "start")

                def timed_out(cmd, *args, **kwargs):
                    raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

                apps.subprocess.run = timed_out
                timeout = apps.action("demo", "stop")
        finally:
            apps.APPS_DIR = old_apps_dir
            apps.subprocess.run = old_run

        self.assertFalse(missing["ok"])
        self.assertIn("docker nicht gefunden", missing["output"])
        self.assertFalse(timeout["ok"])
        self.assertIn("timed out", timeout["output"])

    def test_app_install_jobs_report_subprocess_exceptions(self) -> None:
        from modules import apps

        class ImmediateThread:
            def __init__(self, target, daemon=False):
                self.target = target
                self.daemon = daemon

            def start(self):
                self.target()

        old_apps_dir = apps.APPS_DIR
        old_run = apps.subprocess.run
        old_thread = apps.threading.Thread
        old_running = apps._running
        old_jobs = dict(apps._install_jobs)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                apps.APPS_DIR = tmp
                apps.threading.Thread = ImmediateThread
                apps._running = lambda app_id: True
                app_id = apps.CATALOG[0]["id"]
                content = apps.build_compose(apps.CATALOG[0])

                def missing_docker(cmd, *args, **kwargs):
                    raise FileNotFoundError(cmd[0])

                apps.subprocess.run = missing_docker
                missing_job = apps.install(app_id, content)["job_id"]
                missing = apps.install_status(missing_job)

                def timed_out_on_start(cmd, *args, **kwargs):
                    if cmd == ["docker", "compose", "up", "-d"]:
                        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))
                    return subprocess.CompletedProcess(cmd, 0, "", "")

                apps.subprocess.run = timed_out_on_start
                timeout_job = apps.install(app_id, content)["job_id"]
                timeout = apps.install_status(timeout_job)
        finally:
            apps.APPS_DIR = old_apps_dir
            apps.subprocess.run = old_run
            apps.threading.Thread = old_thread
            apps._running = old_running
            apps._install_jobs.clear()
            apps._install_jobs.update(old_jobs)

        self.assertEqual(missing["status"], "error")
        self.assertFalse(missing["ok"])
        self.assertIn("docker nicht gefunden", missing["output"])
        self.assertNotEqual(missing_job, timeout_job)
        self.assertEqual(timeout["status"], "error")
        self.assertFalse(timeout["ok"])
        self.assertIn("timed out", timeout["output"])

    def test_network_names_are_validated_before_commands(self) -> None:
        from modules import network

        with self.assertRaises(ValueError):
            network.create_bridge("../br0", [])
        with self.assertRaises(ValueError):
            network.delete_link("eth0;reboot")
        with self.assertRaises(ValueError):
            network.delete_link("this-interface-name-is-too-long")

    def test_network_interface_listing_reports_psutil_failures(self) -> None:
        from modules import network

        old_psutil = network.psutil
        try:
            network.psutil = None
            missing = network.list_interfaces()

            class BadPsutil:
                @staticmethod
                def net_if_addrs():
                    raise RuntimeError("net_if_addrs failed")

            network.psutil = BadPsutil()
            failed = network.list_interfaces()
        finally:
            network.psutil = old_psutil

        self.assertFalse(missing["ok"])
        self.assertEqual(missing["interfaces"], [])
        self.assertIn("psutil", missing["stderr"])
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["interfaces"], [])
        self.assertIn("net_if_addrs failed", failed["stderr"])

    def test_network_multi_step_actions_stop_on_first_failure(self) -> None:
        from modules import network

        calls = []
        old_run = network._run
        try:
            def fake_run(cmd, timeout=30):
                calls.append(cmd)
                if cmd[:4] == ["ip", "link", "set", "eth0"]:
                    return {"ok": False, "stdout": "", "stderr": "link failed"}
                if cmd[:6] == ["ip", "link", "add", "link", "eth0", "name"]:
                    return {"ok": False, "stdout": "", "stderr": "vlan failed"}
                return {"ok": True, "stdout": "", "stderr": ""}

            network._run = fake_run
            bridge = network.create_bridge("br0", ["eth0"])
            vlan = network.create_vlan("eth0", 10, "eth0.10")
        finally:
            network._run = old_run

        self.assertFalse(bridge["ok"])
        self.assertEqual(
            calls[:3],
            [
                ["ip", "link", "add", "name", "br0", "type", "bridge"],
                ["ip", "link", "set", "br0", "up"],
                ["ip", "link", "set", "eth0", "master", "br0"],
            ],
        )
        self.assertFalse(vlan["ok"])
        self.assertNotIn(["ip", "link", "set", "eth0.10", "up"], calls)

    def test_network_bond_does_not_persist_after_runtime_failure(self) -> None:
        from modules import network

        calls = []
        old_run = network._run
        try:
            def fake_run(cmd, timeout=30):
                calls.append(cmd)
                if cmd[:4] == ["ip", "link", "set", "eth0"]:
                    return {"ok": False, "stdout": "", "stderr": "member failed"}
                return {"ok": True, "stdout": "", "stderr": ""}

            network._run = fake_run
            result = network.create_bond("bond0", ["eth0"], "active-backup")
        finally:
            network._run = old_run

        self.assertFalse(result["ok"])
        self.assertEqual(result["steps"][-1]["stderr"], "member failed")
        self.assertNotIn(["ip", "link", "set", "bond0", "up"], calls)

    def test_network_ip_configuration_validates_values_before_commands(self) -> None:
        from modules import network

        calls = []
        old_run = network._run
        try:
            def fake_run(cmd, timeout=30):
                calls.append(cmd)
                return {"ok": True, "stdout": "", "stderr": ""}

            network._run = fake_run
            self.assertFalse(
                network.configure_ip(
                    "eth0",
                    mode="static",
                    ip="192.168.1.10",
                    netmask="24",
                    gateway="192.168.1.1",
                    dns="1.1.1.1\niface bad inet dhcp",
                    persist=True,
                )["ok"]
            )
            self.assertEqual(calls, [])
            self.assertFalse(
                network.configure_ip("eth0", mode="static", ip="999.1.1.1")["ok"]
            )
            self.assertEqual(calls, [])
        finally:
            network._run = old_run

    def test_network_ip_configuration_stops_on_runtime_failures(self) -> None:
        from modules import network

        old_run = network._run
        scenarios = [
            (
                ["ip", "addr", "flush", "dev", "eth0"],
                lambda: network.configure_ip(
                    "eth0", mode="static", ip="192.168.1.10"
                ),
                ["ip", "addr", "add", "192.168.1.10/24", "dev", "eth0"],
            ),
            (
                ["ip", "link", "set", "eth0", "up"],
                lambda: network.configure_ip(
                    "eth0",
                    mode="static",
                    ip="192.168.1.10",
                    gateway="192.168.1.1",
                ),
                ["ip", "route", "replace", "default", "via", "192.168.1.1"],
            ),
            (
                ["ip", "route", "replace", "default", "via", "192.168.1.1"],
                lambda: network.configure_ip(
                    "eth0",
                    mode="static",
                    ip="192.168.1.10",
                    gateway="192.168.1.1",
                ),
                ["ip", "route", "add", "default", "via", "192.168.1.1"],
            ),
            (
                ["ip", "addr", "flush", "dev", "eth0"],
                lambda: network.configure_ip("eth0", mode="dhcp"),
                ["dhclient", "-1", "eth0"],
            ),
            (
                ["ip", "addr", "flush", "dev", "eth0"],
                lambda: network.set_static_ip(
                    "eth0", "192.168.1.10", "24", "192.168.1.1"
                ),
                ["ip", "addr", "add", "192.168.1.10/24", "dev", "eth0"],
            ),
            (
                ["ip", "addr", "add", "192.168.1.10/24", "dev", "eth0"],
                lambda: network.set_static_ip(
                    "eth0", "192.168.1.10", "24", "192.168.1.1"
                ),
                ["ip", "route", "add", "default", "via", "192.168.1.1"],
            ),
        ]

        try:
            for failing_cmd, action, forbidden_cmd in scenarios:
                with self.subTest(failing_cmd=failing_cmd):
                    calls = []

                    def fake_run(cmd, timeout=30):
                        calls.append(cmd)
                        if cmd == failing_cmd:
                            return {"ok": False, "stdout": "", "stderr": "ip failed"}
                        return {"ok": True, "stdout": "", "stderr": ""}

                    network._run = fake_run
                    result = action()

                    self.assertFalse(result["ok"])
                    self.assertEqual(result["stderr"], "ip failed")
                    self.assertNotIn(forbidden_cmd, calls)
        finally:
            network._run = old_run

    def test_network_firewall_rejects_invalid_values_before_commands(self) -> None:
        from modules import network

        calls = []
        old_run = network._run
        try:
            def fake_run(cmd, timeout=30):
                calls.append(cmd)
                return {"ok": True, "stdout": "", "stderr": ""}

            network._run = fake_run
            self.assertFalse(network.firewall_add_rule(80, proto="icmp")["ok"])
            self.assertFalse(network.firewall_add_rule(80, action="permit")["ok"])
            self.assertFalse(network.firewall_add_rule(70000)["ok"])
            self.assertFalse(network.firewall_remove_rule(0)["ok"])
            self.assertEqual(calls, [])
        finally:
            network._run = old_run

    def test_account_usernames_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("RUNVARD_DATA_DIR")
            os.environ["RUNVARD_DATA_DIR"] = tmp
            try:
                import modules.accounts as accounts

                accounts = importlib.reload(accounts)
                self.assertFalse(accounts.add_user("../bad", "secret")["ok"])
                self.assertTrue(accounts.add_user("admin2", "secret")["ok"])
                self.assertEqual(accounts.verify("admin2", "secret"), "readonly")
            finally:
                if old is None:
                    os.environ.pop("RUNVARD_DATA_DIR", None)
                else:
                    os.environ["RUNVARD_DATA_DIR"] = old
                import modules.accounts as accounts

                importlib.reload(accounts)

    def test_accounts_corrupt_json_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("RUNVARD_DATA_DIR")
            os.environ["RUNVARD_DATA_DIR"] = tmp
            try:
                import modules.accounts as accounts

                accounts = importlib.reload(accounts)
                Path(accounts.STORE).write_text("{not-json", encoding="utf-8")
                data = accounts._load()
                quarantined = list(Path(tmp).glob("users.json.corrupt-*"))
                quarantined_content = quarantined[0].read_text(encoding="utf-8")
            finally:
                if old is None:
                    os.environ.pop("RUNVARD_DATA_DIR", None)
                else:
                    os.environ["RUNVARD_DATA_DIR"] = old
                import modules.accounts as accounts

                importlib.reload(accounts)

        self.assertEqual(data, {})
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined_content, "{not-json")

    def test_storage_rejects_unsafe_devices_and_mountpoints(self) -> None:
        from modules import storage, validators

        with self.assertRaises(ValueError):
            storage.create_partition_table("sda;reboot", "gpt")
        with self.assertRaises(ValueError):
            validators.require_device("/dev/../sda")
        with self.assertRaises(PermissionError):
            storage.unmount_device("/")
        with self.assertRaises(PermissionError):
            validators.guard_mountpoint("/proc/runvard")
        self.assertEqual(validators.require_device("/dev/vg0/data"), "/dev/vg0/data")
        self.assertEqual(
            validators.require_device("/dev/mapper/vg0-data"),
            "/dev/mapper/vg0-data",
        )
        self.assertEqual(
            storage._base_device_name("/dev/mapper/vg0-root"),
            "mapper/vg0-root",
        )
        self.assertFalse(storage.create_swapfile("/etc/runvard.swap", 64)["ok"])
        self.assertFalse(storage.lv_create("vg0", "data", "bad;size")["ok"])

    def test_storage_device_listing_degrades_on_bad_lsblk_json(self) -> None:
        from modules import storage

        old_run = storage._run
        try:
            def fake_run(cmd, *args, **kwargs):
                if cmd and cmd[0] == "lsblk":
                    return {"ok": True, "stdout": "not-json", "stderr": ""}
                return {"ok": True, "stdout": "", "stderr": ""}

            storage._run = fake_run
            result = storage.list_block_devices()
        finally:
            storage._run = old_run

        self.assertEqual(result["devices"], [])
        self.assertEqual(result["error"], "lsblk parse error")

    def test_storage_partition_table_reports_refresh_failure(self) -> None:
        from modules import storage

        calls = []
        old_run = storage._run
        old_root_device = storage._root_device
        try:
            def fake_run(cmd, *args, **kwargs):
                calls.append(cmd)
                if cmd and cmd[0] == "partprobe":
                    return {"ok": False, "stdout": "", "stderr": "probe failed"}
                return {"ok": True, "stdout": "", "stderr": ""}

            storage._run = fake_run
            storage._root_device = lambda: "sda"
            result = storage.create_partition_table("/dev/sdb", "gpt")
        finally:
            storage._run = old_run
            storage._root_device = old_root_device

        self.assertFalse(result["ok"])
        self.assertIn("could not be refreshed", result["stderr"])
        self.assertIn(["partprobe", "/dev/sdb"], calls)

    def test_storage_wipefs_failures_stop_destructive_followups(self) -> None:
        from modules import storage

        calls = []
        old_run = storage._run
        old_guard = storage._guard
        old_exists = storage.os.path.exists
        try:
            def fake_run(cmd, *args, **kwargs):
                calls.append(cmd)
                if cmd and cmd[0] == "wipefs":
                    return {"ok": False, "stdout": "", "stderr": "wipe failed"}
                return {"ok": True, "stdout": "", "stderr": ""}

            storage._run = fake_run
            storage._guard = lambda device: None
            storage.os.path.exists = (
                lambda path: True if path == "/dev/sdb1" else old_exists(path)
            )

            table = storage.create_partition_table("/dev/sdb", "gpt")
            formatted = storage.format_partition("/dev/sdb1", "ext4")
        finally:
            storage._run = old_run
            storage._guard = old_guard
            storage.os.path.exists = old_exists

        self.assertFalse(table["ok"])
        self.assertFalse(formatted["ok"])
        self.assertNotIn(["parted", "-s", "/dev/sdb", "mklabel", "gpt"], calls)
        self.assertFalse(any(cmd and str(cmd[0]).startswith("mkfs.") for cmd in calls))

    def test_storage_mount_persist_requires_uuid(self) -> None:
        from modules import storage

        calls = []
        old_run = storage._run
        old_guard = storage._guard
        old_exists = storage.os.path.exists
        old_open = builtins.open
        try:
            def fake_run(cmd, *args, **kwargs):
                calls.append(cmd)
                if cmd and cmd[0] == "blkid":
                    return {"ok": False, "stdout": "", "stderr": "no uuid"}
                return {"ok": True, "stdout": "", "stderr": ""}

            def fake_open(path, *args, **kwargs):
                if path == "/etc/fstab":
                    raise AssertionError("fstab must not be touched without UUID")
                return old_open(path, *args, **kwargs)

            storage._run = fake_run
            storage._guard = lambda device: None
            storage.os.path.exists = (
                lambda path: True if path == "/dev/sdb1" else old_exists(path)
            )
            builtins.open = fake_open

            with tempfile.TemporaryDirectory() as tmp:
                result = storage.mount_device("/dev/sdb1", tmp, persist=True)
        finally:
            storage._run = old_run
            storage._guard = old_guard
            storage.os.path.exists = old_exists
            builtins.open = old_open

        self.assertFalse(result["ok"])
        self.assertIn("Persistenz fehlgeschlagen", result["stderr"])
        self.assertGreaterEqual(len(calls), 2)
        self.assertEqual(calls[0][0:2], ["mount", "/dev/sdb1"])
        self.assertTrue(any(cmd and cmd[0] == "blkid" for cmd in calls))

    def test_storage_swap_actions_support_device_targets(self) -> None:
        from modules import storage

        calls = []
        old_run = storage._run
        try:
            storage._run = lambda cmd, *args, **kwargs: calls.append(cmd) or {
                "ok": True,
                "stdout": "",
                "stderr": "",
            }
            result = storage.swap_action("/dev/mapper/vg0-swap", "off")
        finally:
            storage._run = old_run

        self.assertTrue(result["ok"])
        self.assertEqual(calls, [["swapoff", "/dev/mapper/vg0-swap"]])
        self.assertFalse(storage.swap_action("/dev/../bad", "off")["ok"])
        self.assertFalse(storage.swap_action("/proc/swaps", "off")["ok"])

    def test_storage_swap_persist_errors_are_reported(self) -> None:
        from modules import storage

        old_run = storage._run
        old_open = builtins.open
        try:
            def fake_run(cmd, *args, **kwargs):
                if cmd and cmd[0] == "fallocate":
                    Path(cmd[-1]).write_bytes(b"\0")
                return {"ok": True, "stdout": "", "stderr": ""}

            def fake_open(path, mode="r", *args, **kwargs):
                if path == "/etc/fstab" and "a" in mode:
                    raise OSError("readonly fstab")
                return old_open(path, mode, *args, **kwargs)

            storage._run = fake_run
            builtins.open = fake_open
            with tempfile.TemporaryDirectory() as tmp:
                result = storage.create_swapfile(
                    str(Path(tmp) / "swapfile"), 1, persist=True
                )
        finally:
            storage._run = old_run
            builtins.open = old_open

        self.assertFalse(result["ok"])
        self.assertIn("Persistenz fehlgeschlagen", result["stderr"])

    def test_storage_lvm_stops_when_pvcreate_fails(self) -> None:
        from modules import storage

        calls = []
        old_run = storage._run
        try:
            def fake_run(cmd, *args, **kwargs):
                calls.append(cmd)
                if cmd and cmd[0] == "pvcreate":
                    return {"ok": False, "stdout": "", "stderr": "pv failed"}
                return {"ok": True, "stdout": "", "stderr": ""}

            storage._run = fake_run
            result = storage.vg_create("vgdata", ["/dev/sdb"])
        finally:
            storage._run = old_run

        self.assertFalse(result["ok"])
        self.assertEqual(result["device"], "/dev/sdb")
        self.assertIn(["pvcreate", "-y", "/dev/sdb"], calls)
        self.assertNotIn(["vgcreate", "vgdata", "/dev/sdb"], calls)

    def test_storage_blocks_root_lvm_and_zfs_destroy_before_commands(self) -> None:
        from modules import storage

        old_run = storage._run
        old_root_device = storage._root_device
        old_root_zpool = storage._root_zpool
        calls = []
        try:
            storage._run = lambda cmd, *args, **kwargs: calls.append(cmd) or {
                "ok": True,
                "stdout": "",
                "stderr": "",
            }
            storage._root_device = lambda: "mapper/vg0-root"
            storage._root_zpool = lambda: "rpool"

            with self.assertRaises(PermissionError):
                storage.lv_remove("/dev/mapper/vg0-root")
            with self.assertRaises(PermissionError):
                storage.zpool_destroy("rpool")
        finally:
            storage._run = old_run
            storage._root_device = old_root_device
            storage._root_zpool = old_root_zpool

        self.assertEqual(calls, [])

    def test_storage_zfs_btrfs_listing_errors_are_reported(self) -> None:
        from modules import storage

        old_run = storage._run
        try:
            def fake_run(cmd, *args, **kwargs):
                if cmd in (["zpool", "version"], ["btrfs", "version"]):
                    return {"ok": True, "stdout": "", "stderr": ""}
                return {"ok": False, "stdout": "", "stderr": f"{cmd[0]} failed"}

            storage._run = fake_run
            pools = storage.zfs_pools()
            datasets = storage.zfs_datasets()
            btrfs = storage.btrfs_filesystems()
        finally:
            storage._run = old_run

        self.assertTrue(pools["available"])
        self.assertFalse(pools["ok"])
        self.assertEqual(pools["pools"], [])
        self.assertIn("zpool failed", pools["error"])
        self.assertTrue(datasets["available"])
        self.assertFalse(datasets["ok"])
        self.assertEqual(datasets["datasets"], [])
        self.assertIn("zfs failed", datasets["error"])
        self.assertTrue(btrfs["available"])
        self.assertFalse(btrfs["ok"])
        self.assertEqual(btrfs["filesystems"], [])
        self.assertIn("btrfs failed", btrfs["error"])

    def test_storage_lvm_and_iscsi_listing_errors_are_reported(self) -> None:
        from modules import storage

        old_run = storage._run
        try:
            def fake_run(cmd, *args, **kwargs):
                if cmd == ["vgs", "--version"]:
                    return {"ok": True, "stdout": "", "stderr": ""}
                if cmd == ["iscsiadm", "--version"]:
                    return {"ok": True, "stdout": "", "stderr": ""}
                if cmd and cmd[0] in {"pvs", "vgs", "lvs", "iscsiadm"}:
                    return {"ok": False, "stdout": "", "stderr": f"{cmd[0]} failed"}
                return {"ok": True, "stdout": "", "stderr": ""}

            storage._run = fake_run
            lvm = storage.lvm_overview()
            iscsi = storage.iscsi_sessions()
        finally:
            storage._run = old_run

        self.assertTrue(lvm["available"])
        self.assertFalse(lvm["ok"])
        self.assertEqual(lvm["pvs"], [])
        self.assertEqual(lvm["vgs"], [])
        self.assertEqual(lvm["lvs"], [])
        self.assertIn("pvs failed", lvm["error"])
        self.assertIn("vgs failed", lvm["error"])
        self.assertIn("lvs failed", lvm["error"])
        self.assertTrue(iscsi["available"])
        self.assertFalse(iscsi["ok"])
        self.assertEqual(iscsi["sessions"], [])
        self.assertIn("iscsiadm failed", iscsi["stderr"])

    def test_security_validates_os_names_before_commands(self) -> None:
        from modules import security

        with self.assertRaises(ValueError):
            security.add_user("bad;name")
        with self.assertRaises(ValueError):
            security.add_group("../bad")
        with self.assertRaises(ValueError):
            security.generate_self_signed("../cert")

    def test_security_smb_user_listing_reports_pdbedit_failures(self) -> None:
        from modules import security

        old_run = security._run
        try:
            security._run = lambda cmd, timeout=30: {
                "ok": False,
                "stdout": "",
                "stderr": "pdbedit failed",
            }
            result = security.list_smb_users()
        finally:
            security._run = old_run

        self.assertFalse(result["ok"])
        self.assertEqual(result["users"], [])
        self.assertIn("pdbedit failed", result["stderr"])

    def test_security_certificate_listing_reports_openssl_failures(self) -> None:
        from modules import security

        old_cert_dir = security.CERT_DIR
        old_run = security._run
        try:
            with tempfile.TemporaryDirectory() as tmp:
                security.CERT_DIR = tmp
                Path(tmp, "bad.crt").write_text("not a cert", encoding="utf-8")
                security._run = lambda cmd, timeout=30: {
                    "ok": False,
                    "stdout": "",
                    "stderr": "openssl failed",
                }
                result = security.list_certificates()
        finally:
            security.CERT_DIR = old_cert_dir
            security._run = old_run

        self.assertFalse(result["ok"])
        self.assertEqual(result["certificates"][0]["file"], "bad.crt")
        self.assertIn("openssl failed", result["certificates"][0]["error"])
        self.assertEqual(result["errors"][0]["file"], "bad.crt")

    def test_security_rejects_line_oriented_password_injection(self) -> None:
        from modules import security

        calls = []
        old_run = security.subprocess.run
        try:
            def fake_run(cmd, *args, **kwargs):
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")

            security.subprocess.run = fake_run
            linux_password = security.set_password("root", "secret\nbad:pw")
            smb_password = security.set_smb_password("root", "secret\rbad")
            empty_password = security.set_password("root", "")
        finally:
            security.subprocess.run = old_run

        self.assertFalse(linux_password["ok"])
        self.assertFalse(smb_password["ok"])
        self.assertFalse(empty_password["ok"])
        self.assertEqual(calls, [])

    def test_security_validates_ssh_public_keys_centrally(self) -> None:
        from modules import security

        self.assertFalse(security.add_ssh_key("root", "ssh-ed25519 AAAA\nbad")["ok"])
        self.assertFalse(security.add_ssh_key("root", "ssh-ed25519 not-base64")["ok"])

        source = (ROOT / "modules" / "security.py").read_text(encoding="utf-8")
        validators = (ROOT / "modules" / "validators.py").read_text(encoding="utf-8")
        self.assertIn("require_ssh_public_key", validators)
        self.assertIn("validators.require_ssh_public_key(key)", source)
        self.assertIn("require_password_value", validators)
        self.assertIn("validators.require_password_value(password)", source)

    def test_security_password_aging_rejects_invalid_day_values(self) -> None:
        from modules import security

        self.assertFalse(security.set_password_aging("root", max_days="-2")["ok"])
        self.assertFalse(security.set_password_aging("root", min_days="abc")["ok"])
        self.assertFalse(security.set_password_aging("root", warn_days="100000")["ok"])

    def test_system_manager_rejects_unsafe_control_inputs(self) -> None:
        from modules import system_mgr

        self.assertFalse(system_mgr.add_cron_job("* * *", "echo ok")["ok"])
        self.assertFalse(system_mgr.add_cron_job("* * * * *", "echo ok\nreboot")["ok"])
        self.assertFalse(system_mgr.set_hostname("bad..host")["ok"])
        self.assertFalse(system_mgr.power_action("reboot", -1)["ok"])
        self.assertFalse(system_mgr.apparmor_set("../bad", "complain")["ok"])
        self.assertFalse(system_mgr.apparmor_set("-bad", "complain")["ok"])
        self.assertFalse(system_mgr.apparmor_set("/usr//bin/foo", "complain")["ok"])

    def test_system_manager_cron_add_stops_on_read_errors(self) -> None:
        from modules import system_mgr

        old_run = system_mgr._run
        old_subprocess_run = system_mgr.subprocess.run
        try:
            calls = []

            def denied_run(cmd, timeout=60):
                calls.append(cmd)
                return {"ok": False, "stdout": "", "stderr": "permission denied"}

            system_mgr._run = denied_run
            system_mgr.subprocess.run = lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("crontab write should not run")
            )
            denied = system_mgr.add_cron_job("* * * * *", "echo ok")

            writes = []

            def no_crontab_run(cmd, timeout=60):
                return {"ok": False, "stdout": "", "stderr": "no crontab for root"}

            def write_crontab(cmd, *args, **kwargs):
                writes.append(kwargs.get("input", ""))
                return subprocess.CompletedProcess(cmd, 0, "", "")

            system_mgr._run = no_crontab_run
            system_mgr.subprocess.run = write_crontab
            created = system_mgr.add_cron_job("* * * * *", "echo ok")
        finally:
            system_mgr._run = old_run
            system_mgr.subprocess.run = old_subprocess_run

        self.assertFalse(denied["ok"])
        self.assertIn("permission denied", denied["stderr"])
        self.assertEqual(calls, [["crontab", "-l", "-u", "root"]])
        self.assertTrue(created["ok"])
        self.assertIn("* * * * * echo ok", writes[0])

    def test_system_manager_uses_central_apparmor_validator(self) -> None:
        source = (ROOT / "modules" / "system_mgr.py").read_text(encoding="utf-8")
        validators = (ROOT / "modules" / "validators.py").read_text(encoding="utf-8")

        self.assertIn("require_apparmor_profile", validators)
        self.assertIn("validators.require_apparmor_profile(profile)", source)
        self.assertNotIn('re.match(r"^[A-Za-z0-9._/-]+$"', source)

    def test_system_stats_degrade_when_psutil_denies_optional_metrics(self) -> None:
        try:
            from modules import system
        except ModuleNotFoundError as exc:
            if exc.name == "psutil":
                self.skipTest("psutil is not installed")
            raise

        old_swap = system.psutil.swap_memory
        old_boot = system.psutil.boot_time
        try:
            system.psutil.swap_memory = lambda: (_ for _ in ()).throw(
                PermissionError("denied")
            )
            system.psutil.boot_time = lambda: (_ for _ in ()).throw(
                PermissionError("denied")
            )

            stats = system.get_stats()
            info = system.get_system_info()
        finally:
            system.psutil.swap_memory = old_swap
            system.psutil.boot_time = old_boot

        self.assertEqual(stats["swap"]["total"], 0)
        self.assertIn("uptime_seconds", info)

    def test_update_check_does_not_refresh_package_lists_by_default(self) -> None:
        from modules import system_mgr

        calls = []
        old_run = system_mgr._run
        try:
            def fake_run(cmd, timeout=60):
                calls.append(cmd)
                return {"ok": True, "stdout": "", "stderr": ""}

            system_mgr._run = fake_run
            system_mgr.check_updates()
            self.assertNotIn(["apt-get", "update", "-qq"], calls)
            system_mgr.check_updates(refresh=True)
            self.assertIn(["apt-get", "update", "-qq"], calls)
        finally:
            system_mgr._run = old_run

    def test_system_manager_apt_queries_report_tool_failures(self) -> None:
        from modules import system_mgr

        old_run = system_mgr._run
        try:
            def fake_run(cmd, timeout=60):
                return {"ok": False, "stdout": "", "stderr": "apt failed"}

            system_mgr._run = fake_run
            updates = system_mgr.check_updates()
            refresh = system_mgr.check_updates(refresh=True)
            upgradable = system_mgr.list_upgradable()
            search = system_mgr.pkg_search("curl")
            install = system_mgr.pkg_install("curl")
            remove = system_mgr.pkg_remove("curl")
            bad_install = system_mgr.pkg_install("-bad")
            bad_remove = system_mgr.pkg_remove("bad/pkg")
        finally:
            system_mgr._run = old_run

        self.assertFalse(updates["ok"])
        self.assertEqual(updates["updates"], 0)
        self.assertIn("apt failed", updates["error"])
        self.assertFalse(refresh["ok"])
        self.assertEqual(refresh["updates"], 0)
        self.assertIn("apt failed", refresh["error"])
        self.assertFalse(upgradable["ok"])
        self.assertEqual(upgradable["packages"], [])
        self.assertIn("apt failed", upgradable["error"])
        self.assertFalse(search["ok"])
        self.assertEqual(search["packages"], [])
        self.assertIn("apt failed", search["error"])
        self.assertFalse(install["ok"])
        self.assertIn("apt failed", install["stderr"])
        self.assertFalse(remove["ok"])
        self.assertIn("apt failed", remove["stderr"])
        self.assertFalse(bad_install["ok"])
        self.assertIn("Ungueltiger Paketname", bad_install["stderr"])
        self.assertFalse(bad_remove["ok"])
        self.assertIn("Ungueltiger Paketname", bad_remove["stderr"])

    def test_system_manager_gpu_info_tolerates_non_numeric_fields(self) -> None:
        from modules import system_mgr

        old_run = system_mgr._run
        try:
            system_mgr._run = lambda cmd, timeout=60: {
                "ok": True,
                "stdout": "RTX Test, N/A, bad, 8192, \n",
                "stderr": "",
            }
            info = system_mgr.gpu_info()
        finally:
            system_mgr._run = old_run

        self.assertTrue(info["available"])
        self.assertEqual(info["gpus"][0]["name"], "RTX Test")
        self.assertEqual(info["gpus"][0]["util"], 0)
        self.assertEqual(info["gpus"][0]["mem_used"], 0)
        self.assertEqual(info["gpus"][0]["mem_total"], 8192)
        self.assertEqual(info["gpus"][0]["temp"], 0)

    def test_runvard_update_start_reports_systemd_run_failures(self) -> None:
        from modules import system_mgr

        old_run = system_mgr.subprocess.run
        old_log = system_mgr.RUNVARD_UPDATE_LOG
        scripts = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                system_mgr.RUNVARD_UPDATE_LOG = os.path.join(tmp, "runvard-update.log")

                def missing_systemd(cmd, *args, **kwargs):
                    scripts.append(cmd[-1])
                    raise FileNotFoundError(cmd[0])

                system_mgr.subprocess.run = missing_systemd
                missing = system_mgr.start_runvard_update()

                def timed_out(cmd, *args, **kwargs):
                    scripts.append(cmd[-1])
                    raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 0))

                system_mgr.subprocess.run = timed_out
                timeout = system_mgr.start_runvard_update()

                def failed(cmd, *args, **kwargs):
                    scripts.append(cmd[-1])
                    return subprocess.CompletedProcess(cmd, 1, "", "systemd denied")

                system_mgr.subprocess.run = failed
                nonzero = system_mgr.start_runvard_update()

                self.assertTrue(all(not os.path.exists(path) for path in scripts))
        finally:
            system_mgr.subprocess.run = old_run
            system_mgr.RUNVARD_UPDATE_LOG = old_log

        self.assertFalse(missing["ok"])
        self.assertIn("systemd-run", missing["error"])
        self.assertFalse(timeout["ok"])
        self.assertIn("timed out", timeout["error"])
        self.assertFalse(nonzero["ok"])
        self.assertEqual(nonzero["error"], "systemd denied")

    def test_monitoring_validates_filters_and_alert_rules(self) -> None:
        from modules import monitoring

        self.assertIn("Ungueltige Unit", monitoring.get_logs("syslog", unit="../bad")["logs"])
        self.assertIn("Ungueltige Prioritaet", monitoring.get_logs("syslog", priority="bad")["logs"])
        self.assertIn("Ungueltiger Suchfilter", monitoring.get_logs("syslog", grep="x\nx")["logs"])
        self.assertFalse(monitoring.add_alert_rule("bad", 90, "webhook")["ok"])
        self.assertFalse(monitoring.add_alert_rule("cpu", 90, "shell")["ok"])

    def test_monitoring_corrupt_json_is_quarantined(self) -> None:
        from modules import monitoring

        old_config = monitoring.ALERT_CONFIG
        old_history = monitoring.ALERT_HISTORY
        try:
            with tempfile.TemporaryDirectory() as tmp:
                monitoring.ALERT_CONFIG = os.path.join(tmp, "alerts.json")
                monitoring.ALERT_HISTORY = os.path.join(tmp, "alert_history.json")
                Path(monitoring.ALERT_CONFIG).write_text("{not-json", encoding="utf-8")
                Path(monitoring.ALERT_HISTORY).write_text("[not-json", encoding="utf-8")

                config = monitoring.list_alert_rules()
                history = monitoring.get_alert_history()
                config_quarantine = list(Path(tmp).glob("alerts.json.corrupt-*"))
                history_quarantine = list(Path(tmp).glob("alert_history.json.corrupt-*"))
                config_content = config_quarantine[0].read_text(encoding="utf-8")
                history_content = history_quarantine[0].read_text(encoding="utf-8")
        finally:
            monitoring.ALERT_CONFIG = old_config
            monitoring.ALERT_HISTORY = old_history

        self.assertEqual(config["rules"], [])
        self.assertEqual(history, [])
        self.assertEqual(len(config_quarantine), 1)
        self.assertEqual(len(history_quarantine), 1)
        self.assertEqual(config_content, "{not-json")
        self.assertEqual(history_content, "[not-json")

    def test_monitoring_logs_report_host_tool_failures(self) -> None:
        from modules import monitoring

        old_run = monitoring.subprocess.run
        try:
            def fake_run(cmd, *args, **kwargs):
                return subprocess.CompletedProcess(cmd, 1, "", f"{cmd[0]} failed")

            monitoring.subprocess.run = fake_run
            journal = monitoring.get_logs("syslog")
            kernel = monitoring.get_logs("kernel")
        finally:
            monitoring.subprocess.run = old_run

        self.assertFalse(journal["ok"])
        self.assertIn("journalctl failed", journal["logs"])
        self.assertFalse(kernel["ok"])
        self.assertIn("dmesg failed", kernel["logs"])

    def test_metrics_and_process_queries_are_bounded(self) -> None:
        try:
            from modules import metrics, system
        except ModuleNotFoundError:
            metrics_src = (ROOT / "modules" / "metrics.py").read_text(encoding="utf-8")
            system_src = (ROOT / "modules" / "system.py").read_text(encoding="utf-8")
            self.assertIn("minutes = max(1, min(minutes, 240))", metrics_src)
            self.assertIn("limit = max(1, min(limit, 100))", system_src)
            return

        old_process_iter = system.psutil.process_iter
        try:
            class Proc:
                def __init__(self, pid):
                    self.info = {
                        "pid": pid,
                        "name": f"proc-{pid}",
                        "cpu_percent": pid,
                        "memory_info": type("Mem", (), {"rss": pid})(),
                        "status": "running",
                        "username": "user",
                    }

            system.psutil.process_iter = lambda *args, **kwargs: [
                Proc(i) for i in range(150)
            ]
            processes = system.get_processes("bad", limit=100000)

            def bad_process_iter(*args, **kwargs):
                raise OSError("process query failed")

            system.psutil.process_iter = bad_process_iter
            failed = system.get_processes()
        finally:
            system.psutil.process_iter = old_process_iter

        self.assertIsInstance(metrics.get_history(-999)["points"], list)
        self.assertTrue(processes["ok"])
        self.assertLessEqual(len(processes["processes"]), 100)
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["processes"], [])
        self.assertIn("process query failed", failed["stderr"])

    def test_system_detail_metrics_report_psutil_failures(self) -> None:
        try:
            from modules import system
        except ModuleNotFoundError:
            system_src = (ROOT / "modules" / "system.py").read_text(encoding="utf-8")
            self.assertIn('"disk_io": {}', system_src)
            self.assertIn('"interfaces": []', system_src)
            self.assertIn("socket.AF_INET", system_src)
            self.assertNotIn(".family.name", system_src)
            return

        old_disk_io = system.psutil.disk_io_counters
        old_net_addrs = system.psutil.net_if_addrs
        try:
            system.psutil.disk_io_counters = (
                lambda *args, **kwargs: (_ for _ in ()).throw(
                    OSError("disk io failed")
                )
            )
            disk = system.get_disk_io()

            system.psutil.net_if_addrs = (
                lambda: (_ for _ in ()).throw(OSError("net detail failed"))
            )
            net = system.get_net_detail()
        finally:
            system.psutil.disk_io_counters = old_disk_io
            system.psutil.net_if_addrs = old_net_addrs

        self.assertFalse(disk["ok"])
        self.assertEqual(disk["disk_io"], {})
        self.assertIn("disk io failed", disk["stderr"])
        self.assertFalse(net["ok"])
        self.assertEqual(net["interfaces"], [])
        self.assertIn("net detail failed", net["stderr"])

    def test_vm_inputs_are_validated_before_host_tools(self) -> None:
        from modules import vms

        with self.assertRaises(ValueError):
            vms.create_vm("../bad", 1024, 1, 10, "", "default")
        with self.assertRaises(ValueError):
            vms.create_vm("demo", 128, 1, 10, "", "default")
        with self.assertRaises(ValueError):
            vms.create_vm("demo", 1024, 1, 10, "../escape.iso", "default")
        with self.assertRaises(ValueError):
            vms.vm_action("demo;reboot", "start")
        self.assertFalse(vms.attach_disk("demo", "/etc/passwd", "vdb")["ok"])
        self.assertFalse(vms.pool_create("pool", "dir", "/")["ok"])

    def test_vm_libvirt_runtime_errors_are_json_results(self) -> None:
        from modules import vms

        class Conn:
            def lookupByName(self, name):
                raise RuntimeError("domain missing")

        old_connect = vms._connect
        try:
            vms._connect = lambda: Conn()
            action = vms.vm_action("demo", "start")
            snapshot = vms.create_snapshot("demo", "before")
            snapshot_action = vms.snapshot_action("demo", "before", "revert")
            cdrom = vms.change_cdrom("demo", "")
        finally:
            vms._connect = old_connect

        for result in (action, snapshot, snapshot_action, cdrom):
            self.assertFalse(result["ok"])
            self.assertIn("domain missing", result["stderr"])

    def test_vm_create_reports_missing_virt_install(self) -> None:
        from modules import vms

        old_run = vms.subprocess.run
        try:
            def fake_run(cmd, *args, **kwargs):
                raise FileNotFoundError(cmd[0])

            vms.subprocess.run = fake_run
            result = vms.create_vm("demo", 1024, 1, 10, "", "default")
        finally:
            vms.subprocess.run = old_run

        self.assertFalse(result["ok"])
        self.assertIn("virt-install nicht installiert", result["output"])

    def test_vm_pool_create_stops_on_build_or_autostart_failure(self) -> None:
        from modules import vms

        old_virsh = vms._virsh
        try:
            calls = []

            def build_fails(args, timeout=120):
                calls.append(args)
                if args[:1] == ["pool-build"]:
                    return {"ok": False, "stdout": "", "stderr": "build failed"}
                return {"ok": True, "stdout": "", "stderr": ""}

            vms._virsh = build_fails
            build_result = vms.pool_create("pool", "dir", "/tmp")
            self.assertFalse(build_result["ok"])
            self.assertIn("build failed", build_result["stderr"])
            self.assertNotIn(["pool-start", "pool"], calls)

            calls = []

            def autostart_fails(args, timeout=120):
                calls.append(args)
                if args[:1] == ["pool-autostart"]:
                    return {"ok": False, "stdout": "", "stderr": "autostart failed"}
                return {"ok": True, "stdout": "", "stderr": ""}

            vms._virsh = autostart_fails
            autostart_result = vms.pool_create("pool", "dir", "/tmp")
            self.assertFalse(autostart_result["ok"])
            self.assertIn("autostart failed", autostart_result["stderr"])
            self.assertIn(["pool-start", "pool"], calls)
        finally:
            vms._virsh = old_virsh

    def test_vm_list_reads_report_virsh_failures(self) -> None:
        from modules import vms

        old_virsh = vms._virsh
        old_available = vms.available
        old_connect = vms._connect
        try:
            def fake_virsh(args, timeout=120):
                return {"ok": False, "stdout": "", "stderr": f"{args[0]} failed"}

            class BadConn:
                def listAllDomains(self):
                    raise RuntimeError("domain list failed")

            vms._virsh = fake_virsh
            vms.available = lambda: True
            vms._connect = lambda: BadConn()
            domains = vms.list_vms()
            networks = vms.list_networks()
            pools = vms.list_pools()
            volumes = vms.pool_volumes("default")
            bad_pool = vms.pool_volumes("../bad")
        finally:
            vms._virsh = old_virsh
            vms.available = old_available
            vms._connect = old_connect

        self.assertFalse(domains["ok"])
        self.assertEqual(domains["vms"], [])
        self.assertIn("domain list failed", domains["stderr"])
        self.assertFalse(networks["ok"])
        self.assertEqual(networks["networks"], [])
        self.assertIn("net-list failed", networks["error"])
        self.assertFalse(pools["ok"])
        self.assertEqual(pools["pools"], [])
        self.assertIn("pool-list failed", pools["error"])
        self.assertFalse(volumes["ok"])
        self.assertEqual(volumes["volumes"], [])
        self.assertIn("vol-list failed", volumes["error"])
        self.assertFalse(bad_pool["ok"])
        self.assertEqual(bad_pool["volumes"], [])

    def test_vm_pool_delete_stops_on_destroy_failure(self) -> None:
        from modules import vms

        old_virsh = vms._virsh
        try:
            calls = []

            def destroy_fails(args, timeout=120):
                calls.append(args)
                if args[:1] == ["pool-destroy"]:
                    return {"ok": False, "stdout": "", "stderr": "permission denied"}
                return {"ok": True, "stdout": "", "stderr": ""}

            vms._virsh = destroy_fails
            failed = vms.pool_action("pool", "delete")
            failed_calls = list(calls)

            calls = []

            def already_inactive(args, timeout=120):
                calls.append(args)
                if args[:1] == ["pool-destroy"]:
                    return {"ok": False, "stdout": "", "stderr": "pool is not active"}
                return {"ok": True, "stdout": "", "stderr": ""}

            vms._virsh = already_inactive
            inactive = vms.pool_action("pool", "delete")
        finally:
            vms._virsh = old_virsh

        self.assertFalse(failed["ok"])
        self.assertIn("permission denied", failed["stderr"])
        self.assertEqual(failed_calls, [["pool-destroy", "pool"]])
        self.assertTrue(inactive["ok"])
        self.assertEqual(calls, [["pool-destroy", "pool"], ["pool-undefine", "pool"]])

    def test_vm_unknown_actions_still_raise_value_error(self) -> None:
        from modules import vms

        class Conn:
            def lookupByName(self, name):
                return object()

        old_connect = vms._connect
        try:
            vms._connect = lambda: Conn()
            with self.assertRaises(ValueError):
                vms.vm_action("demo", "bad")
            with self.assertRaises(ValueError):
                vms.snapshot_action("demo", "before", "bad")
        finally:
            vms._connect = old_connect

    def test_dashboard_tiles_validate_ids_types_and_ports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("RUNVARD_DATA_DIR")
            os.environ["RUNVARD_DATA_DIR"] = tmp
            try:
                import modules.dashboard as dashboard

                dashboard = importlib.reload(dashboard)
                self.assertTrue(
                    dashboard.add_tile("compose", "compose:demo", "Demo", port=8080)["ok"]
                )
                with self.assertRaises(ValueError):
                    dashboard.add_tile("bad", "demo")
                with self.assertRaises(ValueError):
                    dashboard.add_tile("custom", "../bad")
                with self.assertRaises(ValueError):
                    dashboard.add_tile("custom", "shortcut", port=70000)
                with self.assertRaises(ValueError):
                    dashboard.save_order({"bad": "shape"})
                with self.assertRaises(ValueError):
                    dashboard.update_tile("compose:demo", host="bad host name")
            finally:
                if old is None:
                    os.environ.pop("RUNVARD_DATA_DIR", None)
                else:
                    os.environ["RUNVARD_DATA_DIR"] = old
                import modules.dashboard as dashboard

                importlib.reload(dashboard)

    def test_dashboard_corrupt_json_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = os.environ.get("RUNVARD_DATA_DIR")
            os.environ["RUNVARD_DATA_DIR"] = tmp
            try:
                import modules.dashboard as dashboard

                dashboard = importlib.reload(dashboard)
                Path(dashboard.DASH_FILE).write_text("{not-json", encoding="utf-8")
                data = dashboard._load()
                quarantined = list(Path(tmp).glob("dashboard.json.corrupt-*"))
                quarantined_content = quarantined[0].read_text(encoding="utf-8")
            finally:
                if old is None:
                    os.environ.pop("RUNVARD_DATA_DIR", None)
                else:
                    os.environ["RUNVARD_DATA_DIR"] = old
                import modules.dashboard as dashboard

                importlib.reload(dashboard)

        self.assertEqual(data, {"tiles": []})
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined_content, "{not-json")


class StaticApiContractTests(unittest.TestCase):
    def test_btop_html_is_not_embedded_in_server_module(self) -> None:
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        btop = ROOT / "static" / "btop.html"

        self.assertTrue(btop.exists())
        self.assertIn("static\", \"btop.html", server)
        self.assertNotIn("const THEME={", server)

    def test_websocket_terminal_loop_is_shared(self) -> None:
        server = (ROOT / "server.py").read_text(encoding="utf-8")

        self.assertIn("async def _require_ws_admin", server)
        self.assertIn("async def _run_terminal_websocket", server)
        self.assertEqual(server.count("terminal.pty_to_ws"), 1)

    def test_websocket_host_params_use_central_validators(self) -> None:
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        validators = (ROOT / "modules" / "validators.py").read_text(encoding="utf-8")
        vms = (ROOT / "modules" / "vms.py").read_text(encoding="utf-8")

        self.assertIn("def require_vm_name(", validators)
        self.assertIn("validators.require_slug(websocket.query_params.get(\"id\", \"\")", server)
        self.assertIn("validators.require_vm_name(websocket.query_params.get(\"name\", \"\"))", server)
        self.assertIn("validators.require_vm_name(name)", vms)
        self.assertNotIn("import re as _re\n    cid =", server)
        self.assertNotIn("import re as _re\n    name =", server)

    def test_file_path_lists_use_central_parser(self) -> None:
        server = (ROOT / "server.py").read_text(encoding="utf-8")

        self.assertIn("def _parse_file_paths_form", server)
        self.assertIn("selected_paths = _parse_file_paths_form(paths)", server)
        self.assertIn("files.make_zip(selected_paths, output)", server)
        self.assertIn("selected_paths = _parse_file_paths_form(paths)", server)
        self.assertIn("files.start_job(action, selected_paths, dst_dir, output)", server)
        self.assertNotIn("paths.split(\"|\")", server)

    def test_routes_validate_form_values_before_confirm_tokens(self) -> None:
        server_text = (ROOT / "server.py").read_text(encoding="utf-8")
        server = ast.parse(server_text)
        offenders: list[str] = []

        for node in server.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            confirm_index = None
            for index, statement in enumerate(node.body):
                text = ast.get_source_segment(server_text, statement) or ""
                if "_confirm_action(" in text and confirm_index is None:
                    confirm_index = index
                    continue
                if confirm_index is not None and index > confirm_index:
                    if "validators." in text or "_parse_" in text:
                        offenders.append(f"{node.name}: {text.splitlines()[0]}")

        self.assertEqual(offenders, [])

    def test_frontend_api_wrapper_reads_json_error_payloads(self) -> None:
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn("data.error||data.detail", frontend)
        self.assertIn("JSON.parse(txt)", frontend)

    def test_frontend_has_browser_dependency_fallbacks(self) -> None:
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        btop = (ROOT / "static" / "btop.html").read_text(encoding="utf-8")

        self.assertIn("function showDependencyWarning", frontend)
        self.assertIn("if(typeof Chart==='undefined')return null", frontend)
        self.assertIn("typeof Terminal==='undefined'||typeof FitAddon==='undefined'", frontend)
        self.assertIn("typeof marked==='undefined'", frontend)
        self.assertIn("Missing terminal browser libraries", btop)

    def test_frontend_translation_allows_empty_strings(self) -> None:
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn("hasOwnProperty.call(I18N[lang],key)", frontend)
        self.assertIn("daySuffix:''", frontend)

    def test_frontend_uses_runtime_data_dir_config(self) -> None:
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('@app.get("/api/config")', server)
        self.assertIn("const runvardDataPath=", frontend)
        self.assertIn("api('/config')", frontend)
        self.assertIn("runvardDataPath('apps',id)", frontend)

    def test_destructive_actions_use_confirm_tokens(self) -> None:
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn('@app.post("/api/confirm-token")', server)
        self.assertIn("security_tokens.require_confirm_token", server)
        self.assertIn("confirm_token: str = Form(\"\")", server)
        self.assertIn("const confirmedPost=", frontend)
        self.assertIn("'auth-toggle'", frontend)
        self.assertIn("'account-add'", frontend)
        self.assertIn("'account-password'", frontend)
        self.assertIn("'account-role'", frontend)
        self.assertIn("'account-delete'", frontend)
        self.assertIn("'files-write'", frontend)
        self.assertIn("'files-rename'", frontend)
        self.assertIn("'files-mkdir'", frontend)
        self.assertIn("'files-upload'", frontend)
        self.assertIn("'files-job:'+action", frontend)
        self.assertIn("'files-unzip'", frontend)
        self.assertIn("'files-share-link'", frontend)
        self.assertIn("'files-trash-restore'", frontend)
        self.assertIn("'files-trash-empty'", frontend)
        self.assertIn("'storage-format'", frontend)
        self.assertIn("'storage-partition-table'", frontend)
        self.assertIn("'storage-partition'", frontend)
        self.assertIn("'storage-mount'", frontend)
        self.assertIn("'storage-swap-create'", frontend)
        self.assertIn("'storage-swap-action:off'", frontend)
        self.assertIn("'storage-raid-create'", frontend)
        self.assertIn("'storage-vg-create'", frontend)
        self.assertIn("'storage-lv-create'", frontend)
        self.assertIn("'storage-lv-extend'", frontend)
        self.assertIn("'power:'+action", frontend)
        self.assertIn("'storage-luks-open'", frontend)
        self.assertIn("'storage-luks-close'", frontend)
        self.assertIn("'storage-fs-grow'", frontend)
        self.assertIn("'storage-zfs-create'", frontend)
        self.assertIn("'storage-zfs-destroy'", frontend)
        self.assertIn("'storage-zfs-scrub'", frontend)
        self.assertIn("'storage-btrfs-create'", frontend)
        self.assertIn("'storage-btrfs-scrub'", frontend)
        self.assertIn("'storage-iscsi-discover'", frontend)
        self.assertIn("'storage-iscsi-login'", frontend)
        self.assertIn("'storage-iscsi-logout'", frontend)
        self.assertIn("'docker-container-action:'+action", frontend)
        self.assertIn("'docker-container-create'", frontend)
        self.assertIn("'docker-container-update'", frontend)
        self.assertIn("'docker-image-pull'", frontend)
        self.assertIn("'storage-lv-remove'", frontend)
        self.assertIn("'docker-volume-remove'", frontend)
        self.assertIn("'docker-compose-save'", frontend)
        self.assertIn("'docker-compose-action:'+action", frontend)
        self.assertIn("'docker-compose-remove'", frontend)
        self.assertIn("'vm-action:'+action", frontend)
        self.assertIn("'vm-create'", frontend)
        self.assertIn("'vm-clone'", frontend)
        self.assertIn("'vm-cdrom'", frontend)
        self.assertIn("'vm-disk-attach'", frontend)
        self.assertIn("'vm-disk-detach'", frontend)
        self.assertIn("'vm-nic-attach'", frontend)
        self.assertIn("'vm-nic-detach'", frontend)
        self.assertIn("'vm-pool-create'", frontend)
        self.assertIn("'vm-volume-create'", frontend)
        self.assertIn("'vm-snapshot-create'", frontend)
        self.assertIn("'vm-snapshot-action:'+action", frontend)
        self.assertIn("'vm-delete'", frontend)
        self.assertIn("'vm-snapshot-delete'", frontend)
        self.assertIn("'vm-volume-delete'", frontend)
        self.assertIn("'vm-pool-delete'", frontend)
        self.assertIn("'backup-add'", frontend)
        self.assertIn("'backup-run'", frontend)
        self.assertIn("'share-samba-add'", frontend)
        self.assertIn("'share-nfs-add'", frontend)
        self.assertIn("'files-samba-share'", frontend)
        self.assertIn("'files-mount-smb'", frontend)
        self.assertIn("'files-mount-nfs'", frontend)
        self.assertIn("'security-user-add'", frontend)
        self.assertIn("'security-user-password'", frontend)
        self.assertIn("'security-smb-password'", frontend)
        self.assertIn("'security-ssh-key-add'", frontend)
        self.assertIn("'security-ssh-key-remove'", frontend)
        self.assertIn("'security-sudo'", frontend)
        self.assertIn("'security-aging'", frontend)
        self.assertIn("'security-expire'", frontend)
        self.assertIn("'security-group-add'", frontend)
        self.assertIn("'security-group-delete'", frontend)
        self.assertIn("'security-group-add-member'", frontend)
        self.assertIn("'security-group-remove-member'", frontend)
        self.assertIn("'security-cert-generate'", frontend)
        self.assertIn("'network-bond-create'", frontend)
        self.assertIn("'network-firewall-add'", frontend)
        self.assertIn("'network-firewall-remove'", frontend)
        self.assertIn("'network-configure-ip'", frontend)
        self.assertIn("'network-bridge-create'", frontend)
        self.assertIn("'network-vlan-create'", frontend)
        self.assertIn("'network-link-delete'", frontend)
        self.assertIn("'sysmgr-sosreport-run'", frontend)
        self.assertIn("'sysmgr-updates-apply'", frontend)
        self.assertIn("'sysmgr-runvard-update'", frontend)
        self.assertIn("'sysmgr-package-'+action", frontend)
        self.assertIn("'apps-install'", frontend)
        self.assertIn("'apps-action:'+action", frontend)
        self.assertIn("'service-action:'+action", frontend)
        self.assertIn("'sysmgr-cron-add'", frontend)
        self.assertIn("'sysmgr-apparmor-set'", frontend)
        self.assertIn("'sysmgr-unattended-set'", frontend)
        self.assertIn("'sysmgr-tuned-set'", frontend)
        self.assertIn("'sysmgr-kdump-action:'+action", frontend)

    def test_confirm_protected_routes_are_not_called_directly(self) -> None:
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        protected_routes = [
            "/auth/toggle",
            "/accounts/add",
            "/accounts/password",
            "/accounts/role",
            "/accounts/delete",
            "/files/write",
            "/files/rename",
            "/files/copy",
            "/files/move",
            "/files/mkdir",
            "/files/delete",
            "/files/upload",
            "/files/zip",
            "/files/unzip",
            "/files/job",
            "/files/trash/restore",
            "/files/trash/empty",
            "/files/share",
            "/files/shares/delete",
            "/storage/partition-table",
            "/storage/partition",
            "/storage/format",
            "/storage/mount",
            "/storage/unmount",
            "/storage/swap/create",
            "/storage/swap/action",
            "/storage/raid/create",
            "/storage/lvm/vg-create",
            "/storage/lvm/lv-create",
            "/storage/lvm/lv-extend",
            "/storage/luks/format",
            "/storage/luks/open",
            "/storage/luks/close",
            "/storage/lvm/lv-remove",
            "/storage/fs-grow",
            "/storage/zfs/create",
            "/storage/zfs/destroy",
            "/storage/zfs/scrub",
            "/storage/btrfs/create",
            "/storage/btrfs/scrub",
            "/storage/iscsi/discover",
            "/storage/iscsi/login",
            "/storage/iscsi/logout",
            "/docker/action",
            "/docker/create",
            "/docker/update",
            "/docker/images/pull",
            "/docker/images/remove",
            "/docker/volumes/remove",
            "/docker/compose/save",
            "/docker/compose/action",
            "/docker/compose/remove",
            "/vms/action",
            "/vms/disk/attach",
            "/vms/disk/detach",
            "/vms/nic/attach",
            "/vms/nic/detach",
            "/vms/pool/create",
            "/vms/pool/action",
            "/vms/pool/vol-create",
            "/vms/pool/vol-delete",
            "/vms/clone",
            "/vms/cdrom",
            "/vms/create",
            "/vms/snapshot",
            "/vms/snapshot/action",
            "/backup/add",
            "/backup/run",
            "/shares/samba/add",
            "/shares/nfs/add",
            "/files/samba-share",
            "/files/mount-smb",
            "/files/mount-nfs",
            "/security/users/add",
            "/security/users/password",
            "/security/users/smb-password",
            "/security/users/ssh-keys/add",
            "/security/users/ssh-keys/remove",
            "/security/users/sudo",
            "/security/users/aging",
            "/security/users/expire",
            "/security/groups/add",
            "/security/groups/delete",
            "/security/groups/add-member",
            "/security/groups/remove-member",
            "/security/certs/generate",
            "/sysmgr/power",
            "/network/bond/create",
            "/network/bond/delete",
            "/network/firewall/add",
            "/network/firewall/remove",
            "/network/configure-ip",
            "/network/bridge/create",
            "/network/vlan/create",
            "/network/link/delete",
            "/monitoring/alerts/add",
            "/sysmgr/updates/apply",
            "/sysmgr/runvard-update/apply",
            "/sysmgr/hostname",
            "/sysmgr/packages/install",
            "/sysmgr/packages/remove",
            "/apps/install",
            "/apps/action",
            "/services/action",
            "/sysmgr/cron/add",
            "/sysmgr/apparmor/set",
            "/sysmgr/unattended/set",
            "/sysmgr/tuned/set",
            "/sysmgr/kdump/action",
            "/sysmgr/sosreport/run",
        ]

        offenders: list[str] = []
        for lineno, line in enumerate(frontend.splitlines(), 1):
            for route in protected_routes:
                pattern = rf"\bpost\(\s*['\"]{re.escape(route)}['\"]"
                if re.search(pattern, line) and "confirmedPost" not in line:
                    offenders.append(f"{lineno}: {route}")

        self.assertEqual(offenders, [])
        self.assertNotIn("dockerAct('${x.id}','remove')", frontend)
        self.assertIn("action==='delete'?confirmedPost('/vms/snapshot/action'", frontend)
        self.assertIn("confirmedPost('/vms/action',{name,action:'delete'}", frontend)
        self.assertIn(
            "confirmedPost('/vms/pool/action',{name,action:'delete'}",
            frontend,
        )
        self.assertNotIn("post('/apps/action',{app_id:id,action:'down'}", frontend)
        self.assertNotIn("post('/apps/action',{app_id:id,action}", frontend)
        self.assertIn(
            "confirmedPost('/apps/action',{app_id:id,action},'apps-action:'+action,id)",
            frontend,
        )

    def test_all_post_routes_are_classified_by_mutation_policy(self) -> None:
        allowed_without_confirm = {
            "/api/login",
            "/api/logout",
            "/api/confirm-token",
            "/api/dashboard/add",
            "/api/dashboard/remove",
            "/api/dashboard/order",
            "/api/dashboard/toggle-url",
            "/api/dashboard/update",
        }
        classified = _protected_contract_routes() | allowed_without_confirm

        self.assertEqual(_server_post_routes() - classified, set())

    def test_dashboard_mutations_are_admin_only_without_confirmation(self) -> None:
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        routes = [
            "/api/dashboard/add",
            "/api/dashboard/remove",
            "/api/dashboard/order",
            "/api/dashboard/toggle-url",
            "/api/dashboard/update",
        ]

        for route in routes:
            with self.subTest(route=route):
                marker = f'@app.post("{route}")'
                self.assertIn(marker, server)
                start = server.index(marker)
                next_route = server.find("\n@app.", start + len(marker))
                block = server[start:] if next_route == -1 else server[start:next_route]
                self.assertIn("Depends(require_admin)", block)
                self.assertNotIn("confirm_token", block)

    def test_frontend_dashboard_mutations_are_role_gated(self) -> None:
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn("const authState={user:null,role:'admin',login_enabled:false}", frontend)
        self.assertIn("const isAdmin=()=>authState.role==='admin'", frontend)
        self.assertIn("const dashRequireAdmin=()=>", frontend)
        self.assertIn("id=\"dash-add-btn\"", frontend)
        self.assertIn("if(isAdmin()&&typeof Sortable!=='undefined')", frontend)

        for fn in [
            "window.dashAppAction=async(id,action)=>",
            "window.dashCompose=async id=>",
            "window.dashToggleUrl=async(id,show)=>",
            "window.dashEditHost=id=>",
            "window.dashRemove=(id,name)=>",
            "window.dashEditCustom=id=>",
            "window.dashAddForm=()=>",
            "window.appAddToDashboard=async id=>",
        ]:
            with self.subTest(fn=fn):
                start = frontend.index(fn)
                block = frontend[start : start + 180]
                self.assertIn("dashRequireAdmin()", block)

    def test_frontend_has_no_shadowed_top_level_functions(self) -> None:
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        definitions: dict[str, list[int]] = {}
        patterns = [
            r"^async function\s+([A-Za-z_$][\w$]*)\s*\(",
            r"^function\s+([A-Za-z_$][\w$]*)\s*\(",
            r"^window\.([A-Za-z_$][\w$]*)\s*=",
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, frontend, re.M):
                line = frontend.count("\n", 0, match.start()) + 1
                definitions.setdefault(match.group(1), []).append(line)

        duplicates = {
            name: lines for name, lines in definitions.items() if len(lines) > 1
        }
        self.assertEqual(duplicates, {})

    def test_frontend_inline_handlers_reference_existing_functions(self) -> None:
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        definitions: set[str] = set()
        definition_patterns = [
            r"^\s*async function\s+([A-Za-z_$][\w$]*)\s*\(",
            r"^\s*function\s+([A-Za-z_$][\w$]*)\s*\(",
            r"^\s*window\.([A-Za-z_$][\w$]*)\s*=",
            r"^\s*const\s+([A-Za-z_$][\w$]*)\s*=",
            r"^\s*let\s+([A-Za-z_$][\w$]*)\s*=",
        ]
        for pattern in definition_patterns:
            definitions.update(re.findall(pattern, frontend, re.M))

        ignored_calls = {
            "Boolean",
            "Date",
            "JSON",
            "Math",
            "Number",
            "String",
            "catch",
            "clear",
            "decodeURIComponent",
            "encodeURIComponent",
            "for",
            "if",
            "parseFloat",
            "parseInt",
            "preventDefault",
            "return",
            "setInterval",
            "setTimeout",
            "stopPropagation",
            "switch",
            "while",
        }
        missing: dict[str, list[int]] = {}
        handler_pattern = re.compile(
            r"on(?:click|change|input|keydown|contextmenu|submit)="
            r"(?P<quote>['\"])(?P<body>.*?)(?P=quote)"
        )
        for match in handler_pattern.finditer(frontend):
            line = frontend.count("\n", 0, match.start()) + 1
            for call in re.finditer(r"\b([A-Za-z_$][\w$]*)\s*\(", match.group("body")):
                name = call.group(1)
                if name not in definitions and name not in ignored_calls:
                    missing.setdefault(name, []).append(line)

        self.assertEqual(missing, {})

    def test_network_interface_family_detection_is_platform_safe(self) -> None:
        network = (ROOT / "modules" / "network.py").read_text(encoding="utf-8")

        self.assertIn("import socket", network)
        self.assertIn("a.family == socket.AF_INET", network)
        self.assertIn('getattr(socket, "AF_PACKET", None)', network)
        self.assertIn('getattr(socket, "AF_LINK", None)', network)
        self.assertNotIn(".family.name", network)

    def test_service_logs_validate_line_count(self) -> None:
        services = (ROOT / "modules" / "services.py").read_text(encoding="utf-8")
        validators = (ROOT / "modules" / "validators.py").read_text(encoding="utf-8")
        server = (ROOT / "server.py").read_text(encoding="utf-8")

        self.assertIn("def require_int_range(", validators)
        self.assertIn('validators.require_int_range(lines, 1, 5000, "lines")', services)
        self.assertIn("def services_logs(name: str, lines: int = 100", server)

    def test_docker_client_is_closed_and_socket_guarded(self) -> None:
        docker_mgr = (ROOT / "modules" / "docker_mgr.py").read_text(encoding="utf-8")
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        runtime_tests = (ROOT / "tests" / "test_app_runtime.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('not os.environ.get("DOCKER_HOST")', docker_mgr)
        self.assertIn('not os.path.exists("/var/run/docker.sock")', docker_mgr)
        self.assertIn("def close_client():", docker_mgr)
        self.assertIn('getattr(client, "close", None)', docker_mgr)
        self.assertIn("close()", docker_mgr)
        self.assertIn("lifespan=_lifespan", server)
        self.assertIn("docker_mgr.close_client()", server)
        self.assertIn("cls.server.docker_mgr.close_client()", runtime_tests)

    def test_confirm_protected_routes_have_backend_checks(self) -> None:
        route_functions = _server_post_route_functions()
        for route in sorted(_protected_contract_routes()):
            with self.subTest(route=route):
                self.assertIn(route, route_functions)
                route_fn = route_functions[route]
                self.assertIn(
                    "confirm_token",
                    {arg.arg for arg in route_fn.args.args},
                )
                self.assertTrue(
                    any(
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "_confirm_action"
                        for node in ast.walk(route_fn)
                    )
                )

    def test_file_explorer_starts_on_readable_path(self) -> None:
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        server = (ROOT / "server.py").read_text(encoding="utf-8")

        self.assertIn("let currentPath='/home'", frontend)
        self.assertIn('def files_list(path: str = "/home"', server)
        self.assertNotIn("path:'/root'", frontend)

    def test_web_terminal_uses_readable_default_directory(self) -> None:
        terminal = (ROOT / "modules" / "terminal.py").read_text(encoding="utf-8")

        self.assertIn('return "/home" if os.path.isdir("/home") else "/"', terminal)
        self.assertIn("def default_shell():", terminal)
        self.assertIn("self.cwd = cwd if cwd and os.path.isdir(cwd) else default_cwd()", terminal)
        self.assertNotIn('cwd="/root"', terminal)

    def test_literal_frontend_api_calls_have_backend_routes(self) -> None:
        server = (ROOT / "server.py").read_text(encoding="utf-8")
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        routes = {
            match.group(1)
            for match in re.finditer(r'@app\.(?:get|post|put|delete)\("([^"]+)"', server)
            if match.group(1).startswith("/api/")
        }
        calls = set()
        for match in re.finditer(r"api\(\s*['\"]([^'\"]+)", frontend):
            path = match.group(1)
            if path.startswith("/"):
                calls.add(("/api" + path).split("?", 1)[0])
        for match in re.finditer(r"['\"](/api/[^'\"?`$]+)", frontend):
            calls.add(match.group(1).split("?", 1)[0])

        missing = sorted(call for call in calls if call not in routes)
        self.assertEqual(missing, [])

    def test_literal_frontend_post_fields_match_backend_forms(self) -> None:
        frontend = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
        server_routes = _server_post_route_functions()
        ignored_args = {"request", "user"}
        route_fields = {
            route.removeprefix("/api"): {
                arg.arg for arg in function.args.args if arg.arg not in ignored_args
            }
            for route, function in server_routes.items()
        }
        call_pattern = re.compile(
            r"(?:confirmedPost|post)\(\s*['\"]([^'\"]+)['\"]\s*,\s*\{([^{}]*)\}",
            re.S,
        )
        key_pattern = re.compile(
            r"(?:^|,)\s*([A-Za-z_$][\w$]*|['\"][^'\"]+['\"])\s*(?=[:,])"
        )
        offenders: list[str] = []

        for match in call_pattern.finditer(frontend):
            route = match.group(1)
            fields = route_fields.get(route)
            if fields is None:
                continue
            body = match.group(2)
            keys = {key.strip("'\"") for key in key_pattern.findall(body)}
            extra = sorted(keys - fields)
            if extra:
                line = frontend.count("\n", 0, match.start()) + 1
                offenders.append(f"{line}: {route} unexpected fields {extra}")

        self.assertEqual(offenders, [])

    def test_persistent_data_paths_go_through_runtime_module(self) -> None:
        offenders: list[str] = []
        allowed = {
            "modules/runtime.py",
            "modules/apps.py",  # module docstring documents the on-disk layout
            "modules/system_mgr.py",  # updater shell script checks legacy env paths
        }
        for path in (ROOT / "modules").glob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel in allowed:
                continue
            text = path.read_text(encoding="utf-8")
            if "/opt/runvard/data" in text:
                offenders.append(rel)

        self.assertEqual(offenders, [])

    def test_target_host_verifier_has_safe_default_and_contract_checks(self) -> None:
        script = ROOT / "scripts" / "verify-target-host.sh"
        text = script.read_text(encoding="utf-8")

        self.assertTrue(script.exists())
        self.assertIn('RUNVARD_DESTRUCTIVE="${RUNVARD_DESTRUCTIVE:-0}"', text)
        self.assertIn('RUNVARD_API_ONLY="${RUNVARD_API_ONLY:-0}"', text)
        self.assertIn("API-only verifier mode enabled", text)
        self.assertIn("service checks skipped in API-only mode", text)
        self.assertIn("host integration checks skipped in API-only mode", text)
        self.assertIn("destructive checks skipped in API-only mode", text)
        self.assertIn("preflight_fail_count", text)
        self.assertIn("missing command before verifier setup", text)
        self.assertIn("finish()", text)
        self.assertIn("missing command: $1", text)
        self.assertIn('if [ "$fail_count" -gt 0 ]; then', text)
        self.assertIn("required_tools=\"mktemp rm curl grep sed python3\"", text)
        self.assertIn("required_tools=\"mktemp rm curl grep sed systemctl journalctl python3\"", text)
        self.assertIn("login page returned HTTP 000", text)
        self.assertIn("is not reachable", text)
        self.assertIn('storage/format', text)
        self.assertIn('confirm-token', text)
        self.assertIn('--data-urlencode "username=${RUNVARD_USER}"', text)
        self.assertIn('--data-urlencode "password=${RUNVARD_PASS}"', text)
        self.assertIn(': > "$body"', text)
        self.assertIn("curl -sS -o \"$body\"", text)
        self.assertIn("expect_json()", text)
        self.assertIn("json_has_key()", text)
        self.assertIn("expect_json_key()", text)
        self.assertIn("json_key_equals()", text)
        self.assertIn("expect_invalid_mutation()", text)
        self.assertIn("python3 -m json.tool", text)
        self.assertIn('expect_json "login API"', text)
        self.assertIn('api_get auth/status', text)
        self.assertIn("verifier account has admin role", text)
        self.assertIn("verifier account must have admin role", text)
        self.assertIn('expect_json "GET /api/${route}"', text)
        self.assertIn('mismatched confirmation token', text)
        self.assertIn("system/stats system/info system/disks system/temps", text)
        self.assertIn("shares/samba shares/nfs shares/ftp", text)
        self.assertIn("backup/jobs dashboard apps/catalog sysmgr/updates", text)
        self.assertIn('5*) fail "GET /api/${route} returned HTTP $code"', text)
        self.assertIn('docker/available docker/compose', text)
        self.assertIn('storage/devices storage/swap storage/raid storage/luks', text)
        self.assertIn("Structured Discovery Contracts", text)
        for spec in (
            "docker/containers:containers",
            "docker/images:images",
            "docker/volumes:volumes",
            "system/processes:processes",
            "system/disk-io:disk_io",
            "system/net-detail:interfaces",
            "services/list:services",
            "network/interfaces:interfaces",
            "security/smb-users:users",
            "security/certs:certificates",
            "vms/list:vms",
        ):
            self.assertIn(spec, text)
        self.assertIn("Parameterized Read API Validation", text)
        self.assertIn('services/logs?name=../bad', text)
        self.assertIn("service logs invalid unit", text)
        self.assertIn('services/logs?name=${RUNVARD_TEST_SERVICE}&lines=999999', text)
        self.assertIn("service logs invalid line count", text)
        self.assertIn('files/download?path=/proc/cpuinfo', text)
        self.assertIn("file download rejects blocked path", text)
        self.assertIn('files/preview?path=/', text)
        self.assertIn("file preview rejects directory path", text)
        self.assertIn("Mutating API Input Validation", text)
        self.assertIn("backup rejects unsafe rsync source", text)
        self.assertIn('source=-e:evil', text)
        self.assertIn("NFS rejects unsafe export options", text)
        self.assertIn("--data-urlencode \"options=rw)", text)
        self.assertIn("file upload rejects invalid filename", text)
        self.assertIn('filename=..', text)
        self.assertIn('file upload validation token issue', text)
        self.assertIn("file share-link rejects directory path", text)
        self.assertIn("file share-link directory validation token issue", text)
        self.assertIn("file job rejects empty path-list entries", text)
        self.assertIn('paths=/tmp/a||/tmp/b', text)
        self.assertIn("account creation rejects invalid role", text)
        self.assertIn("storage format rejects invalid filesystem type", text)
        self.assertIn("VM volume creation rejects invalid disk format", text)
        self.assertIn("file job rejects invalid action", text)
        self.assertIn("package install rejects invalid package name", text)
        self.assertIn("package remove rejects invalid package name", text)
        self.assertIn("name=-bad", text)
        self.assertIn("name=bad/pkg", text)
        self.assertIn("AppArmor rejects invalid mode", text)
        self.assertIn("kdump rejects invalid action", text)
        self.assertIn('RUNVARD_DESTRUCTIVE" = "1"', text)

    def test_local_verifier_checks_shell_script_syntax(self) -> None:
        verifier = (ROOT / "scripts" / "verify-local.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("== required tools ==", verifier)
        self.assertIn('SCRIPT_DIR="${0%/*}"', verifier)
        self.assertNotIn("dirname --", verifier)
        for tool in ("mktemp", "rm", "python3", "bash", "git", "node"):
            self.assertIn(tool, verifier)
        self.assertIn('command -v "$tool"', verifier)
        self.assertIn("missing command: $tool", verifier)
        self.assertIn("missing_tools", verifier)
        self.assertIn("== shell syntax ==", verifier)
        for script in (
            "install.sh",
            "uninstall.sh",
            "update.sh",
            "scripts/install-full.sh",
            "scripts/verify-local.sh",
            "scripts/verify-target-host.sh",
            "scripts/verify-api-only.sh",
        ):
            self.assertIn(script, verifier)
        self.assertIn('bash -n "$script"', verifier)

    def test_local_verifier_checks_required_artifacts_and_markdown_links(self) -> None:
        verifier = (ROOT / "scripts" / "verify-local.sh").read_text(
            encoding="utf-8"
        )
        docs = (ROOT / "DEVELOPMENT.md").read_text(encoding="utf-8")

        self.assertIn("== required artifacts ==", verifier)
        for artifact in (
            "DEVELOPMENT.md",
            "modules/runtime.py",
            "static/btop.html",
            "scripts/verify-target-host.sh",
            "scripts/verify-api-only.sh",
            "tests/test_app_runtime.py",
        ):
            self.assertIn(artifact, verifier)
        self.assertIn("== markdown links ==", verifier)
        self.assertIn("root.glob(\"*.md\")", verifier)
        self.assertIn("re.match", verifier)
        self.assertIn("test -s README.md INSTALLATION.md DEVELOPMENT.md", docs)
        self.assertIn("scripts/verify-api-only.sh", docs)
        self.assertIn("bash -n install.sh uninstall.sh update.sh", docs)
        self.assertIn("checks local Markdown links", docs)

    def test_local_verifier_keeps_python_bytecode_out_of_workspace(self) -> None:
        verifier = (ROOT / "scripts" / "verify-local.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("mktemp -d", verifier)
        self.assertIn("preflight_missing", verifier)
        self.assertIn("missing command before verifier setup", verifier)
        self.assertLess(
            verifier.index("missing command before verifier setup"),
            verifier.index('VERIFY_TMP="$(mktemp -d'),
        )
        self.assertIn("PYTHONPYCACHEPREFIX", verifier)
        self.assertIn('trap \'rm -rf "$VERIFY_TMP"\'', verifier)
        self.assertIn("== generated python artifacts ==", verifier)
        self.assertIn("__pycache__", verifier)
        self.assertIn('path.suffix in {".pyc", ".pyo"}', verifier)
        docs = (ROOT / "DEVELOPMENT.md").read_text(encoding="utf-8")
        self.assertIn("isolated `PYTHONPYCACHEPREFIX`", docs)
        self.assertIn("`__pycache__`, `.pyc`, or `.pyo`", docs)

    def test_api_only_verifier_wrapper_runs_isolated_local_http_contract(self) -> None:
        wrapper_path = ROOT / "scripts" / "verify-api-only.sh"
        wrapper = wrapper_path.read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertTrue(wrapper_path.exists())
        self.assertTrue(os.access(wrapper_path, os.X_OK))
        self.assertIn('SCRIPT_DIR="${0%/*}"', wrapper)
        self.assertNotIn("dirname --", wrapper)
        self.assertIn('for tool in curl mktemp rm sleep "$PYTHON_BIN"; do', wrapper)
        self.assertIn('command -v "$tool"', wrapper)
        self.assertIn('VERIFY_TMP="$(mktemp -d', wrapper)
        self.assertIn('RUNVARD_DATA_DIR="${RUNVARD_DATA_DIR:-${VERIFY_TMP}/data}"', wrapper)
        self.assertIn('PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-${VERIFY_TMP}/pycache}"', wrapper)
        self.assertIn('"$PYTHON_BIN" -m uvicorn server:app', wrapper)
        self.assertIn('RUNVARD_PORT="${RUNVARD_PORT:-8876}"', wrapper)
        self.assertIn('curl -fsS "${RUNVARD_URL}/login"', wrapper)
        self.assertIn("for ((attempt = 1; attempt <= 80; attempt++)); do", wrapper)
        self.assertNotIn("seq 1 80", wrapper)
        self.assertIn("RUNVARD_API_ONLY=1", wrapper)
        self.assertIn("scripts/verify-target-host.sh", wrapper)
        self.assertIn("kill \"$SERVER_PID\"", wrapper)
        self.assertIn("local API-only verifier", readme)

    def test_target_host_verification_docs_cover_api_boundary_checks(self) -> None:
        docs = "\n".join(
            (
                (ROOT / "DEVELOPMENT.md").read_text(encoding="utf-8"),
                (ROOT / "INSTALLATION.md").read_text(encoding="utf-8"),
            )
        )

        self.assertIn("HTTP 400 before host tools run", docs)
        for phrase in (
            "unsafe backup rsync sources",
            "unsafe NFS export options",
            "invalid upload filenames",
            "directory share-link attempts",
            "empty file job path-list entries",
            "account roles",
            "invalid package names",
            "filesystem types",
            "VM volume formats",
            "file job actions",
            "AppArmor modes",
            "kdump actions",
        ):
            self.assertIn(phrase, docs)
        self.assertIn("RUNVARD_API_ONLY=1", docs)
        self.assertIn("admin account", docs)
        self.assertIn("/api/auth/status", docs)
        self.assertIn("readonly account", docs)
        self.assertIn("mutation-boundary checks require admin privileges", docs)
        self.assertIn("does not prove service", docs)
        self.assertIn("does not replace the normal verifier", docs)
        self.assertIn("scripts/verify-api-only.sh", docs)
        self.assertIn("starts uvicorn on `127.0.0.1:8876`", docs)

    def test_installer_quotes_env_credentials(self) -> None:
        installer = (ROOT / "scripts" / "install-full.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("quote_env_value()", installer)
        self.assertIn('RUNVARD_USER=$(quote_env_value "$ADMIN_USER")', installer)
        self.assertIn('RUNVARD_PASS=$(quote_env_value "$ADMIN_PASS")', installer)
        self.assertIn("RUNVARD_PORT=${PORT}", installer)

    def test_installer_service_uses_runtime_port_from_env_file(self) -> None:
        installer = (ROOT / "scripts" / "install-full.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("Environment=RUNVARD_PORT=8080", installer)
        self.assertIn("EnvironmentFile=-${ENV_FILE}", installer)
        self.assertIn("--port \\${RUNVARD_PORT}", installer)
        self.assertNotIn("--port ${PORT} --workers 1", installer)

    def test_installer_does_not_copy_local_development_artifacts(self) -> None:
        installer = (ROOT / "scripts" / "install-full.sh").read_text(
            encoding="utf-8"
        )

        for pattern in (
            "--exclude '.git'",
            "--exclude '.DS_Store'",
            "--exclude '.venv'",
            "--exclude '__pycache__'",
            "--exclude '.pytest_cache'",
            "--exclude '*.pyc'",
            "--exclude '*.pyo'",
        ):
            self.assertIn(pattern, installer)

    def test_installer_and_updater_make_verifiers_executable(self) -> None:
        installer = (ROOT / "scripts" / "install-full.sh").read_text(
            encoding="utf-8"
        )
        updater = (ROOT / "update.sh").read_text(encoding="utf-8")

        for text, install_var in (
            (installer, "$INSTALL_DIR"),
            (updater, "$INSTALL"),
        ):
            self.assertIn("chmod +x", text)
            self.assertIn(f'{install_var}/scripts/verify-local.sh', text)
            self.assertIn(f'{install_var}/scripts/verify-target-host.sh', text)
            self.assertIn(f'{install_var}/scripts/verify-api-only.sh', text)

    def test_update_syncs_full_release_and_refreshes_service_template(self) -> None:
        updater = (ROOT / "update.sh").read_text(encoding="utf-8")

        self.assertIn("rsync -a --delete", updater)
        self.assertIn('"$SRC"/ "$INSTALL"/', updater)
        self.assertIn("SERVICE_FILE=\"/etc/systemd/system/runvard.service\"", updater)
        self.assertIn('cp -f "$INSTALL/runvard.service" "$SERVICE_FILE"', updater)
        self.assertIn("systemctl daemon-reload", updater)
        self.assertNotIn('cp -f "$SRC/static/index.html"', updater)
        for pattern in (
            "--exclude 'data'",
            "--exclude 'venv'",
            "--exclude '.venv'",
            "--exclude '__pycache__'",
            "--exclude '.pytest_cache'",
            "--exclude '*.pyc'",
            "--exclude '*.bak*'",
        ):
            self.assertIn(pattern, updater)

    def test_update_backs_up_current_static_and_service_files(self) -> None:
        updater = (ROOT / "update.sh").read_text(encoding="utf-8")

        self.assertIn("server.py requirements.txt runvard.service", updater)
        self.assertIn("static/index.html static/login.html static/btop.html", updater)
        self.assertIn('cp -f "$INSTALL/$f" "$INSTALL/$f.bak.$TS"', updater)

    def test_installer_env_quote_helper_roundtrips_shell_values(self) -> None:
        script = r'''
quote_env_value() {
  local value="${1-}" out="" i ch
  for ((i=0; i<${#value}; i++)); do
    ch="${value:i:1}"
    case "$ch" in
      "\\"|"\""|"\$"|\`) out="${out}\\${ch}" ;;
      *) out="${out}${ch}" ;;
    esac
  done
  printf '"%s"' "$out"
}
value=$'pa ss\'"$`word'
quoted="$(quote_env_value "$value")"
eval "roundtrip=$quoted"
[ "$roundtrip" = "$value" ]
'''
        subprocess.run(["bash", "-c", script], check=True)

    def test_service_template_uses_runtime_port_from_env_file(self) -> None:
        service = (ROOT / "runvard.service").read_text(encoding="utf-8")

        self.assertIn("Environment=RUNVARD_PORT=8080", service)
        self.assertIn("EnvironmentFile=-/opt/runvard/data/runvard.env", service)
        self.assertIn("--port ${RUNVARD_PORT}", service)


if __name__ == "__main__":
    unittest.main()
