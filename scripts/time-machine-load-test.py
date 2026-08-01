#!/usr/bin/env python3
"""Bounded concurrent I/O smoke/load test for a mounted Time Machine share."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import time


MARKER = ".runvard-time-machine-load-test"


def client_count(value: str) -> int:
    parsed = int(value)
    if parsed < 1 or parsed > 20:
        raise argparse.ArgumentTypeError("clients must be between 1 and 20")
    return parsed


def size_mib(value: str) -> int:
    parsed = int(value)
    if parsed < 1 or parsed > 4096:
        raise argparse.ArgumentTypeError("size must be between 1 and 4096 MiB")
    return parsed


def _exercise(workdir: Path, client: int, byte_count: int) -> dict[str, float | int]:
    path = workdir / f"client-{client:02d}.bin"
    digest = hashlib.sha256()
    remaining = byte_count
    block = secrets.token_bytes(min(1024 * 1024, byte_count))
    started = time.monotonic()
    with path.open("xb") as handle:
        while remaining:
            chunk = block[:min(len(block), remaining)]
            handle.write(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        handle.flush()
        os.fsync(handle.fileno())
    write_seconds = time.monotonic() - started
    expected = digest.hexdigest()
    digest = hashlib.sha256()
    started = time.monotonic()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    read_seconds = time.monotonic() - started
    if digest.hexdigest() != expected:
        raise RuntimeError(f"checksum mismatch for client {client}")
    return {
        "client": client, "bytes": byte_count,
        "write_seconds": round(write_seconds, 3),
        "read_seconds": round(read_seconds, 3),
    }


def _safe_cleanup(workdir: Path, mount: Path) -> None:
    resolved = workdir.resolve()
    root = mount.resolve()
    if (
        resolved.parent != root
        or not resolved.name.startswith(".runvard-tm-load-")
        or not (resolved / MARKER).is_file()
    ):
        raise RuntimeError("refusing to clean an unverified load-test directory")
    shutil.rmtree(resolved)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run bounded concurrent write/read checks on a mounted SMB target.",
    )
    parser.add_argument("--mount", required=True, help="Mounted SMB share path")
    parser.add_argument("--clients", type=client_count, default=10)
    parser.add_argument("--size-mib", type=size_mib, default=64)
    parser.add_argument("--keep", action="store_true", help="Keep generated files")
    parser.add_argument(
        "--allow-non-mount", action="store_true",
        help="Allow a normal directory for lab validation",
    )
    args = parser.parse_args()
    mount = Path(args.mount).resolve()
    if not mount.is_dir():
        parser.error("mount path must be an existing directory")
    if not args.allow_non_mount and not os.path.ismount(mount):
        parser.error("path is not a mount point (use --allow-non-mount only in a lab)")
    workdir = mount / f".runvard-tm-load-{secrets.token_hex(8)}"
    workdir.mkdir(mode=0o700)
    (workdir / MARKER).write_text("created-by=runvard\n", encoding="utf-8")
    started = time.monotonic()
    results = []
    failed = None
    try:
        with ThreadPoolExecutor(max_workers=args.clients) as executor:
            futures = [
                executor.submit(_exercise, workdir, client, args.size_mib * 1024**2)
                for client in range(1, args.clients + 1)
            ]
            for future in as_completed(futures):
                results.append(future.result())
    except Exception as exc:
        failed = str(exc)
    finally:
        if not args.keep:
            _safe_cleanup(workdir, mount)
    report = {
        "ok": failed is None, "clients": args.clients,
        "size_mib_per_client": args.size_mib,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "results": sorted(results, key=lambda row: row["client"]),
    }
    if failed:
        report["error"] = failed
    elif args.keep:
        report["workdir"] = str(workdir)
    print(json.dumps(report, indent=2))
    return 0 if failed is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
