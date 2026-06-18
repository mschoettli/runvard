"""
Run long operations in background threads and expose status.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def start_job(name: str, func: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, str]:
    """
    Start a background job.

    Args:
    -----
        name (str):
            Human-readable job name.
        func (Callable[..., Any]):
            Function to run.
        *args (Any):
            Positional function arguments.
        **kwargs (Any):
            Keyword function arguments.

    Returns:
    --------
        dict[str, str]:
            Job identifier response.
    """
    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "id": job_id,
            "name": name,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "result": None,
            "error": None,
        }

    def runner() -> None:
        try:
            result = func(*args, **kwargs)
            with _lock:
                _jobs[job_id].update(
                    {"status": "succeeded", "finished_at": time.time(), "result": result}
                )
        except Exception as exc:  # pragma: no cover - defensive job boundary.
            with _lock:
                _jobs[job_id].update(
                    {"status": "failed", "finished_at": time.time(), "error": str(exc)}
                )

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return {"ok": True, "job_id": job_id}


def get_job(job_id: str) -> dict[str, Any]:
    """
    Return job status.

    Args:
    -----
        job_id (str):
            Job identifier.

    Returns:
    --------
        dict[str, Any]:
            Job state.

    Raises:
    -------
        KeyError:
            Raised when the job does not exist.
    """
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise KeyError("Job not found")
        return dict(job)


def list_jobs() -> list[dict[str, Any]]:
    """
    Return all known jobs.

    Returns:
    --------
        list[dict[str, Any]]:
            Job states ordered by start time.
    """
    with _lock:
        return sorted((dict(job) for job in _jobs.values()), key=lambda item: item["started_at"], reverse=True)
