#!/usr/bin/env bash
#
# runvard installer bootstrap.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/mschoettli/runvard/main/install.sh | sudo bash
#
set -euo pipefail

ARCHIVE_URL="${RUNVARD_ARCHIVE_URL:-https://github.com/mschoettli/runvard/archive/refs/heads/main.tar.gz}"
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
LOCAL_INSTALLER="${SCRIPT_DIR}/scripts/install-full.sh"

if [ -f "$LOCAL_INSTALLER" ] && [ -f "${SCRIPT_DIR}/server.py" ]; then
  exec bash "$LOCAL_INSTALLER" "$@"
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "runvard must be installed with root privileges." >&2
  echo "Run: curl -fsSL https://raw.githubusercontent.com/mschoettli/runvard/main/install.sh | sudo bash" >&2
  exit 1
fi

command -v curl >/dev/null 2>&1 || {
  echo "curl is required for the one-command installer." >&2
  exit 1
}

command -v tar >/dev/null 2>&1 || {
  echo "tar is required for the one-command installer." >&2
  exit 1
}

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "Downloading runvard..."
curl -fsSL "$ARCHIVE_URL" | tar -xz -C "$WORK_DIR"
REMOTE_COMMIT="$(
  curl -fsSL https://api.github.com/repos/mschoettli/runvard/commits/main 2>/dev/null \
    | sed -n 's/.*"sha": "\([0-9a-f]\{40\}\)".*/\1/p' \
    | head -n 1
)"

RUNVARD_DIR="$(find "$WORK_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
REMOTE_INSTALLER="${RUNVARD_DIR}/scripts/install-full.sh"

if [ -z "$RUNVARD_DIR" ] || [ ! -f "$REMOTE_INSTALLER" ] || [ ! -f "${RUNVARD_DIR}/server.py" ]; then
  echo "Downloaded archive is not a valid runvard release." >&2
  exit 1
fi

RUNVARD_SOURCE_DIR="$RUNVARD_DIR" RUNVARD_SOURCE_COMMIT="$REMOTE_COMMIT" bash "$REMOTE_INSTALLER" "$@"
