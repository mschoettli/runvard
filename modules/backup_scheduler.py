"""Run recurring rsync backups while the panel is online."""
import logging
import threading
import time


_INTERVALS = {"hourly": 3600, "daily": 86400, "weekly": 604800}
_LOG = logging.getLogger(__name__)


def is_due(job, now):
    """Legacy jobs used their creation timestamp as their integer ID."""
    interval = _INTERVALS.get(job.get("schedule"))
    if interval is None or not job.get("enabled", True):
        return False
    anchor = job.get("last_run")
    if anchor is None:
        anchor = job.get("created_at", job.get("id"))
    return isinstance(anchor, (int, float)) and now - anchor >= interval


class BackupScheduler:
    def __init__(self, list_jobs, run_scheduled_job):
        self._list_jobs = list_jobs
        self._run_scheduled_job = run_scheduled_job
        self._worker = None

    def start(self):
        if self._worker is not None:
            return
        self._stop = threading.Event()
        self._worker = threading.Thread(
            target=self._poll, args=(self._stop,), name="runvard-backups", daemon=True,
        )
        self._worker.start()

    def stop(self):
        if self._worker is not None:
            self._stop.set()
            self._worker.join(timeout=2)
            self._worker = None

    def _poll(self, stop):
        while not stop.is_set():
            try:
                self._scan(stop)
            except Exception:
                _LOG.exception("Could not read scheduled backups")
            stop.wait(60)

    def _scan(self, stop):
        for job in self._list_jobs():
            if stop.is_set():
                return
            now = time.time()
            try:
                if is_due(job, now):
                    # The store rechecks under its lock before claiming the run.
                    self._run_scheduled_job(job["id"], now)
            except Exception:
                _LOG.exception("Could not run scheduled backup %s", job.get("id"))
