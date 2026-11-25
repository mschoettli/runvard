#!/usr/bin/env bash
#
# runvard updater
#
set -euo pipefail

if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
  BOLD=$'\033[1m'; RED=$'\033[0;31m'; GREEN=$'\033[0;32m'
  YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; NC=""
fi

INSTALL_DIR="/opt/runvard"
ENV_FILE="${INSTALL_DIR}/data/runvard.env"
VERSION_FILE="${INSTALL_DIR}/data/runvard.version"
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SRC="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"

info() { echo -e "${CYAN}$*${NC}"; }
ok() { echo -e "${GREEN}OK${NC} $*"; }
warn() { echo -e "${YELLOW}WARN${NC} $*"; }
die() { echo -e "${RED}ERROR:${NC} $*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
runvard updater

Usage:
  sudo bash update.sh [options]

Options:
  -h, --help    Show this help

Environment:
  RUNVARD_SOURCE_COMMIT=<sha>  Version commit to write to data/runvard.version
  RUNVARD_SKIP_PIP=1           Skip Python dependency refresh
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (--help)" ;;
  esac
done

[ "$(id -u)" -eq 0 ] || die "Run this updater as root."
[ -f "$SRC/server.py" ] || die "server.py was not found. Run update.sh from a runvard release directory."
[ -f "$SRC/requirements.txt" ] || die "requirements.txt was not found in the source directory."
[ -d "$SRC/modules" ] || die "modules/ was not found in the source directory."
[ -d "$SRC/static" ] || die "static/ was not found in the source directory."
[ -d "$INSTALL_DIR" ] || die "Missing ${INSTALL_DIR}. Run install.sh first."
[ -f "$ENV_FILE" ] || die "Missing ${ENV_FILE}. Run install.sh first."
[ -x "$INSTALL_DIR/venv/bin/python" ] || die "Missing ${INSTALL_DIR}/venv. Run install.sh first."
command -v rsync >/dev/null 2>&1 || die "rsync is required."
command -v systemctl >/dev/null 2>&1 || die "systemctl is required."

# shellcheck source=scripts/install-common.sh
. "$SRC/scripts/install-common.sh"

info "Updating runvard..."

SETTINGS="$SRC/scripts/installer-settings.py"
DATA_DIR="$(python3 "$SETTINGS" get "$ENV_FILE" RUNVARD_DATA_DIR)"
python3 "$SETTINGS" sync "$SRC" "$INSTALL_DIR" "${DATA_DIR:-$INSTALL_DIR/data}"
ok "Program files synced."

SOURCE_COMMIT="${RUNVARD_SOURCE_COMMIT:-}"
if [ -z "$SOURCE_COMMIT" ] && [ -d "$SRC/.git" ] && command -v git >/dev/null 2>&1; then
  SOURCE_COMMIT="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)"
fi
if [ "${RUNVARD_SKIP_PIP:-0}" = "1" ]; then
  warn "Skipping Python dependency refresh because RUNVARD_SKIP_PIP=1."
else
  PIP="$INSTALL_DIR/venv/bin/pip"
  if bundled_wheels_ready "$PIP" "$INSTALL_DIR"; then
    info "Installing Python dependencies from bundled wheels..."
    "$PIP" install -q --no-index --find-links "$INSTALL_DIR/wheels" -r "$INSTALL_DIR/requirements.txt"
    info "Upgrading pip..."
    "$PIP" install -q --upgrade pip
  else
    info "Upgrading pip..."
    "$PIP" install -q --upgrade pip
    info "Installing Python dependencies..."
    "$PIP" install -q -r "$INSTALL_DIR/requirements.txt"
  fi
  "$PIP" install -q libvirt-python || warn "libvirt-python could not be installed."
fi

info "Restarting runvard..."
systemctl restart runvard
sleep 3

PORT="$(python3 "$SETTINGS" get "$ENV_FILE" RUNVARD_PORT)"
if READY="$(probe_local_login "${PORT:-8080}")"; then
  read -r SCHEME HTTP PORT <<< "$READY"
  ok "Update succeeded. runvard responded with HTTP ${HTTP}."
else
  warn "Update finished, but runvard did not respond as expected."
  journalctl -u runvard -n 30 --no-pager || true
  exit 1
fi

if [[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  record_version "$VERSION_FILE" "$SOURCE_COMMIT"
  ok "Version recorded."
else
  warn "No valid source commit found; version file was not updated."
fi
