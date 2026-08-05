#!/usr/bin/env python3
"""Reject unsafe or malformed runvard release tar archives before extraction."""
from pathlib import PurePosixPath
import sys
import tarfile


def validate(path: str) -> str:
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        if not members:
            raise ValueError("release archive is empty")
        roots = set()
        names = set()
        for member in members:
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts or not pure.parts:
                raise ValueError(f"unsafe archive path: {member.name}")
            roots.add(pure.parts[0])
            names.add(str(pure))
            if member.issym() or member.islnk():
                target = PurePosixPath(member.linkname)
                if target.is_absolute() or ".." in target.parts:
                    raise ValueError(f"unsafe archive link: {member.name}")
        if len(roots) != 1:
            raise ValueError("release archive must have exactly one root directory")
        root = next(iter(roots))
        for required in (f"{root}/server.py", f"{root}/scripts/install-full.sh"):
            if required not in names:
                raise ValueError(f"missing required release file: {required}")
        return root


if __name__ == "__main__":
    try:
        print(validate(sys.argv[1]))
    except (IndexError, OSError, tarfile.TarError, ValueError) as exc:
        print(f"Invalid runvard release archive: {exc}", file=sys.stderr)
        raise SystemExit(1)
