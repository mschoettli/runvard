#!/usr/bin/env bash
# Verified runvard release bootstrap. Local trusted checkouts remain supported.
set -euo pipefail

REPOSITORY="mschoettli/runvard"
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
LOCAL_INSTALLER="${SCRIPT_DIR}/scripts/install-full.sh"
VERSION=""
DEVELOPER_BRANCH=""
FORCE_REMOTE=0
FORWARD=()

while [ $# -gt 0 ]; do
  case "$1" in
    --version) [ -n "${2:-}" ] || { echo "--version requires vX.Y.Z" >&2; exit 2; }; VERSION="$2"; shift 2 ;;
    --developer-branch) [ -n "${2:-}" ] || { echo "--developer-branch requires a branch" >&2; exit 2; }; DEVELOPER_BRANCH="$2"; shift 2 ;;
    --verified-release) FORCE_REMOTE=1; shift ;;
    *) FORWARD+=("$1"); shift ;;
  esac
done

if [ "$FORCE_REMOTE" = "0" ] && [ -f "$LOCAL_INSTALLER" ] && [ -f "${SCRIPT_DIR}/server.py" ]; then
  echo "Installing from the explicitly supplied local checkout: ${SCRIPT_DIR}"
  exec bash "$LOCAL_INSTALLER" "${FORWARD[@]}"
fi

[ "$(id -u)" -eq 0 ] || { echo "runvard must be installed with root privileges." >&2; exit 1; }
for command_name in curl tar python3 sha256sum gh; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "$command_name is required for verified release installation." >&2
    exit 1
  }
done

WORK_DIR="$(mktemp -d)"
cleanup() { rm -rf -- "$WORK_DIR"; }
trap cleanup EXIT INT TERM

if [ -n "$DEVELOPER_BRANCH" ]; then
  [[ "$DEVELOPER_BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "Invalid developer branch." >&2; exit 2; }
  echo "WARNING: developer mode bypasses release attestation and must not be used in production." >&2
  ARCHIVE="$WORK_DIR/developer.tar.gz"
  curl -fsSL "https://github.com/${REPOSITORY}/archive/refs/heads/${DEVELOPER_BRANCH}.tar.gz" -o "$ARCHIVE"
  DEV_COMMIT="$(curl -fsSL "https://api.github.com/repos/${REPOSITORY}/commits/${DEVELOPER_BRANCH}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("sha", ""))')"
else
  if [ -z "$VERSION" ]; then
    VERSION="$(curl -fsSL "https://api.github.com/repos/${REPOSITORY}/releases/latest" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tag_name", ""))')"
  fi
  [[ "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || { echo "Unknown or invalid release version: $VERSION" >&2; exit 1; }
  ARCHIVE_NAME="runvard-${VERSION}.tar.gz"
  ARCHIVE="$WORK_DIR/$ARCHIVE_NAME"
  ARCHIVE_URL="https://github.com/${REPOSITORY}/releases/download/${VERSION}/${ARCHIVE_NAME}"
  CHECKSUM="$WORK_DIR/${ARCHIVE_NAME}.sha256"
  curl -fsSL "$ARCHIVE_URL" -o "$ARCHIVE"
  curl -fsSL "${ARCHIVE_URL}.sha256" -o "$CHECKSUM"
  EXPECTED="$(awk 'NR==1 {print $1}' "$CHECKSUM")"
  [[ "$EXPECTED" =~ ^[0-9a-fA-F]{64}$ ]] || { echo "Invalid release checksum." >&2; exit 1; }
  ACTUAL="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
  [ "$ACTUAL" = "$EXPECTED" ] || { echo "Release checksum verification failed." >&2; exit 1; }
  gh attestation verify "$ARCHIVE" --repo "$REPOSITORY" >/dev/null
fi

# Validate every path and link before extraction. This code is part of the trusted bootstrap.
python3 - "$ARCHIVE" >"$WORK_DIR/root" <<'PY'
from pathlib import PurePosixPath
import sys, tarfile
p=sys.argv[1]
with tarfile.open(p, "r:gz") as a:
    roots=set(); names=set()
    for m in a.getmembers():
        n=PurePosixPath(m.name)
        if n.is_absolute() or ".." in n.parts or not n.parts: raise SystemExit("unsafe archive path")
        if (m.issym() or m.islnk()) and (PurePosixPath(m.linkname).is_absolute() or ".." in PurePosixPath(m.linkname).parts): raise SystemExit("unsafe archive link")
        roots.add(n.parts[0]); names.add(str(n))
    if len(roots)!=1: raise SystemExit("invalid archive root")
    root=next(iter(roots))
    for required in (f"{root}/server.py",f"{root}/scripts/install-full.sh"):
        if required not in names: raise SystemExit("missing required release file")
    print(root)
PY
ROOT="$(cat "$WORK_DIR/root")"
tar -xzf "$ARCHIVE" -C "$WORK_DIR" --no-same-owner --no-same-permissions
RUNVARD_DIR="$WORK_DIR/$ROOT"
COMMIT="$(sed -n '1p' "$RUNVARD_DIR/RELEASE_COMMIT" 2>/dev/null || true)"
[ -n "$DEVELOPER_BRANCH" ] && COMMIT="$DEV_COMMIT"
[[ "$COMMIT" =~ ^[0-9a-f]{40}$ ]] || { echo "Release commit metadata is missing or invalid." >&2; exit 1; }
echo "Verified runvard release: $VERSION ($COMMIT)"
RUNVARD_SOURCE_DIR="$RUNVARD_DIR" RUNVARD_SOURCE_COMMIT="$COMMIT" \
  bash "$RUNVARD_DIR/scripts/install-full.sh" "${FORWARD[@]}"
