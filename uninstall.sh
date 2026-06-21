#!/usr/bin/env bash
#
# runvard uninstaller
# Usage: sudo bash uninstall.sh
#
set -euo pipefail

if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[0;31m'; GREEN=$'\033[0;32m'
  YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; PURPLE=$'\033[0;35m'; NC=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; PURPLE=""; NC=""
fi

step()  { echo -e "\n${PURPLE}${BOLD}> $*${NC}"; }
info()  { echo -e "  ${DIM}$*${NC}"; }
ok()    { echo -e "  ${GREEN}OK${NC} $*"; }
warn()  { echo -e "  ${YELLOW}WARN${NC} $*"; }
die()   { echo -e "\n${RED}ERROR:${NC} $*" >&2; exit 1; }
trap 'die "Something went wrong at line $LINENO."' ERR

INSTALL_DIR="/opt/runvard"
SERVICE_FILE="/etc/systemd/system/runvard.service"
SERVICE="runvard"

PURGE=0
ASSUME_YES=0

usage() {
  cat <<'USAGE'
runvard uninstaller

Usage:
  sudo bash uninstall.sh [options]

Options:
  --purge      Delete data and configuration without creating a backup.
               By default, the data directory is backed up first.
  -y, --yes    Run without confirmation prompts.
  -h, --help   Show this help.

Intentionally not removed:
  - Packages installed through apt, such as Docker, Samba, libvirt, or ZFS.
    They may be used by other services.
  - System changes made from inside runvard, such as shares, users,
    sudo policy, SSH keys, or cron jobs.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --purge) PURGE=1; shift ;;
    -y|--yes) ASSUME_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (--help)" ;;
  esac
done

echo -e "\n${CYAN}${BOLD}runvard uninstaller${NC}\n"

[ "$(id -u)" -eq 0 ] || die "Run this uninstaller as root: sudo bash uninstall.sh"

if [ ! -e "$INSTALL_DIR" ] && [ ! -e "$SERVICE_FILE" ]; then
  ok "runvard is not installed. Nothing to do."
  exit 0
fi

echo "  The following will be removed:"
echo -e "    Service : ${BOLD}${SERVICE}${NC} will be stopped, disabled, and removed"
echo -e "    Folder  : ${BOLD}${INSTALL_DIR}${NC}"
if [ "$PURGE" = "1" ]; then
  echo -e "    Data    : ${RED}${BOLD}deleted without backup${NC}"
else
  echo -e "    Data    : ${GREEN}backed up first${NC} ${DIM}(accounts, secrets, certificates, app data)${NC}"
fi
echo

if [ "$ASSUME_YES" != "1" ]; then
  read -r -p "  Continue with uninstall? (y/N) " ans </dev/tty || ans=""
  case "$ans" in y|Y|yes|YES) ;; *) die "Aborted. Nothing was changed." ;; esac
fi

step "[1/3] Stop and remove service"
if command -v systemctl >/dev/null 2>&1; then
  systemctl stop "$SERVICE" 2>/dev/null || true
  systemctl disable "$SERVICE" 2>/dev/null || true
fi

if [ -f "$SERVICE_FILE" ]; then
  rm -f "$SERVICE_FILE"
  command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload 2>/dev/null || true
  ok "Service stopped and unit removed."
else
  info "No systemd unit was found. Skipping."
fi

BACKUP=""
step "[2/3] Data"
if [ "$PURGE" = "1" ]; then
  warn "--purge selected. Data will not be backed up."
elif [ -d "$INSTALL_DIR/data" ]; then
  BACKUP_DIR="${BACKUP_DIR:-/root}"
  [ -w "$BACKUP_DIR" ] || BACKUP_DIR="/tmp"
  BACKUP="${BACKUP_DIR}/runvard-data-backup-$(date +%Y%m%d-%H%M%S).tar.gz"
  if tar -czf "$BACKUP" -C "$INSTALL_DIR" data 2>/dev/null; then
    ok "Data backed up: ${BOLD}${BACKUP}${NC}"
  else
    BACKUP=""
    warn "Backup failed. Continuing; data will be removed with the installation folder."
  fi
else
  info "No data directory found. Nothing to back up."
fi

step "[3/3] Remove program files"
if [ -e "$INSTALL_DIR" ]; then
  rm -rf "$INSTALL_DIR"
  ok "Removed ${INSTALL_DIR}."
else
  info "${INSTALL_DIR} did not exist."
fi

echo
echo -e "${GREEN}${BOLD}runvard was uninstalled.${NC}"
echo
if [ -n "$BACKUP" ]; then
  echo -e "  Data backup: ${BOLD}${BACKUP}${NC}"
  echo -e "  Restore after reinstall:"
  echo -e "  ${DIM}tar -xzf ${BACKUP} -C /opt/runvard && systemctl restart runvard${NC}"
  echo
fi
info "Not removed: apt packages such as Docker, Samba, libvirt, or ZFS."
info "Not removed: system changes made from inside runvard."
echo
