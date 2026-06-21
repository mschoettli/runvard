#!/usr/bin/env bash
#
# runvard full installer
# Usage: sudo bash install.sh [options]
#
set -euo pipefail

# ------------------------------ UI ------------------------------------
if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[0;31m'; GREEN=$'\033[0;32m'
  YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; PURPLE=$'\033[0;35m'; NC=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; PURPLE=""; NC=""
fi

LANG_CODE="${RUNVARD_LANG:-}"
ASSUME_YES="${RUNVARD_YES:-0}"
TOTAL_STEPS=6
CURRENT_STEP=0
SPIN_LOG="/tmp/runvard_install.log"

t() {
  local key="$1"
  case "${LANG_CODE}:${key}" in
    de:usage) cat <<'EOF'
runvard - Installations-Assistent

Verwendung:
  sudo bash install.sh [Optionen]

Optionen:
  --lang <en|de>    Sprache des Installers
  --port <n>        Web-Port (1-65535, Standard 8080)
  --user <name>     Admin-Benutzername (Standard admin)
  -y, --yes         Ohne Rueckfragen installieren
  -h, --help        Diese Hilfe anzeigen

Das Passwort wird interaktiv abgefragt oder ueber RUNVARD_PASS gesetzt.
Leer bedeutet: ein sicheres Zufallspasswort wird erzeugt.

Beispiele:
  sudo bash install.sh --lang de --port 9090
  sudo RUNVARD_PASS='secret' bash install.sh --yes --port 8443
EOF
      ;;
    *:usage) cat <<'EOF'
runvard installer

Usage:
  sudo bash install.sh [options]

Options:
  --lang <en|de>    Installer language
  --port <n>        Web port (1-65535, default 8080)
  --user <name>     Admin username (default admin)
  -y, --yes         Install without prompts
  -h, --help        Show this help

The password is requested interactively or read from RUNVARD_PASS.
An empty password means a secure random password is generated.

Examples:
  sudo bash install.sh --lang de --port 9090
  sudo RUNVARD_PASS='secret' bash install.sh --yes --port 8443
EOF
      ;;
    de:root_required) echo "Bitte mit root-Rechten starten: sudo bash install.sh" ;;
    *:root_required) echo "Run this installer as root: sudo bash install.sh" ;;
    de:apt_required) echo "Dieses Script benoetigt Debian/Ubuntu mit apt-get." ;;
    *:apt_required) echo "This script requires Debian/Ubuntu with apt-get." ;;
    de:unknown_option) echo "Unbekannte Option" ;;
    *:unknown_option) echo "Unknown option" ;;
    de:missing_value) echo "Option benoetigt einen Wert" ;;
    *:missing_value) echo "Option requires a value" ;;
    de:invalid_lang) echo "Ungueltige Sprache. Erlaubt: en, de." ;;
    *:invalid_lang) echo "Invalid language. Allowed values: en, de." ;;
    de:choose_lang) echo "Sprache waehlen: [1] English  [2] Deutsch" ;;
    *:choose_lang) echo "Choose language: [1] English  [2] Deutsch" ;;
    de:config) echo "Konfiguration" ;;
    *:config) echo "Configuration" ;;
    de:admin_user) echo "Admin-Benutzername" ;;
    *:admin_user) echo "Admin username" ;;
    de:admin_pass) echo "Admin-Passwort (leer = automatisch erzeugen)" ;;
    *:admin_pass) echo "Admin password (empty = generate one)" ;;
    de:admin_pass_repeat) echo "Passwort wiederholen" ;;
    *:admin_pass_repeat) echo "Repeat password" ;;
    de:password_mismatch) echo "Die Passwoerter stimmen nicht ueberein. Bitte erneut versuchen." ;;
    *:password_mismatch) echo "Passwords do not match. Please try again." ;;
    de:web_port) echo "Web-Port" ;;
    *:web_port) echo "Web port" ;;
    de:invalid_port) echo "Bitte eine gueltige Portnummer (1-65535) angeben." ;;
    *:invalid_port) echo "Enter a valid port number (1-65535)." ;;
    de:full_install) echo "Alle Komponenten werden installiert: Docker, Speicher, Freigaben, KVM, ZFS/Btrfs/iSCSI und Wartungstools." ;;
    *:full_install) echo "All components will be installed: Docker, storage, shares, KVM, ZFS/Btrfs/iSCSI, and maintenance tools." ;;
    de:summary) echo "Zusammenfassung" ;;
    *:summary) echo "Summary" ;;
    de:user) echo "Benutzer" ;;
    *:user) echo "User" ;;
    de:password) echo "Passwort" ;;
    *:password) echo "Password" ;;
    de:generated) echo "wird automatisch erzeugt" ;;
    *:generated) echo "will be generated" ;;
    de:hidden) echo "verborgen" ;;
    *:hidden) echo "hidden" ;;
    de:scope) echo "Umfang" ;;
    *:scope) echo "Scope" ;;
    de:complete) echo "Vollstaendig" ;;
    *:complete) echo "Complete" ;;
    de:target) echo "Ziel" ;;
    *:target) echo "Target" ;;
    de:start_install) echo "Installation jetzt starten? (y/n)" ;;
    *:start_install) echo "Start installation now? (y/n)" ;;
    de:aborted) echo "Abgebrochen. Es wurde nichts veraendert." ;;
    *:aborted) echo "Aborted. Nothing was changed." ;;
    de:packages_phase) echo "Systempakete installieren" ;;
    *:packages_phase) echo "Install system packages" ;;
    de:update_apt) echo "Paketquellen aktualisieren" ;;
    *:update_apt) echo "Update package sources" ;;
    de:apt_warn) echo "apt-get update returned warnings; continuing." ;;
    *:apt_warn) echo "apt-get update returned warnings; continuing." ;;
    de:packages_ok) echo "Pakete installiert." ;;
    *:packages_ok) echo "Packages installed." ;;
    de:packages_failed) echo "Diese Pakete konnten nicht installiert werden" ;;
    *:packages_failed) echo "These packages could not be installed" ;;
    de:compose_ok) echo "Docker Compose v2 ist verfuegbar." ;;
    *:compose_ok) echo "Docker Compose v2 is available." ;;
    de:compose_install) echo "Docker Compose v2 installieren" ;;
    *:compose_install) echo "Install Docker Compose v2" ;;
    de:compose_warn) echo "Docker Compose v2 konnte nicht installiert werden; App-Funktionen koennen eingeschraenkt sein." ;;
    *:compose_warn) echo "Docker Compose v2 could not be installed; app features may be limited." ;;
    de:copy_phase) echo "runvard nach /opt/runvard synchronisieren" ;;
    *:copy_phase) echo "Sync runvard to /opt/runvard" ;;
    de:source_missing) echo "server.py nicht gefunden. Installer aus einem runvard-Release starten." ;;
    *:source_missing) echo "server.py was not found. Run the installer from a runvard release." ;;
    de:files_ok) echo "Programmdateien synchronisiert." ;;
    *:files_ok) echo "Program files synced." ;;
    de:version_ok) echo "Version gespeichert." ;;
    *:version_ok) echo "Version recorded." ;;
    de:venv_phase) echo "Python-Umgebung einrichten" ;;
    *:venv_phase) echo "Set up Python environment" ;;
    de:venv_rebuild) echo "Bestehende Python-Umgebung ist ungueltig und wird neu erstellt." ;;
    *:venv_rebuild) echo "Existing Python environment is invalid and will be recreated." ;;
    de:venv_create) echo "Virtuelle Umgebung anlegen" ;;
    *:venv_create) echo "Create virtual environment" ;;
    de:pip_upgrade) echo "pip aktualisieren" ;;
    *:pip_upgrade) echo "Upgrade pip" ;;
    de:pip_install_offline) echo "Python-Pakete aus bundled wheels installieren" ;;
    *:pip_install_offline) echo "Install Python packages from bundled wheels" ;;
    de:pip_install_online) echo "Python-Pakete installieren" ;;
    *:pip_install_online) echo "Install Python packages" ;;
    de:libvirt_install) echo "libvirt-Python-Anbindung installieren" ;;
    *:libvirt_install) echo "Install libvirt Python bindings" ;;
    de:venv_ok) echo "Python-Umgebung bereit." ;;
    *:venv_ok) echo "Python environment ready." ;;
    de:config_phase) echo "Zugangsdaten und Konfiguration schreiben" ;;
    *:config_phase) echo "Write credentials and configuration" ;;
    de:config_ok) echo "Konfiguration gespeichert" ;;
    *:config_ok) echo "Configuration saved" ;;
    de:service_phase) echo "systemd-Dienst einrichten und starten" ;;
    *:service_phase) echo "Set up and start systemd service" ;;
    de:service_ok) echo "Dienst gestartet." ;;
    *:service_ok) echo "Service started." ;;
    de:health_phase) echo "Funktionspruefung" ;;
    *:health_phase) echo "Health check" ;;
    de:wait_service) echo "Warte auf den Dienst" ;;
    *:wait_service) echo "Waiting for the service" ;;
    de:health_ok) echo "runvard antwortet." ;;
    *:health_ok) echo "runvard is responding." ;;
    de:health_fail) echo "Dienst antwortet nicht. Pruefe: journalctl -u runvard -e" ;;
    *:health_fail) echo "Service is not responding. Check: journalctl -u runvard -e" ;;
    de:done) echo "runvard ist installiert." ;;
    *:done) echo "runvard is installed." ;;
    de:url) echo "URL" ;;
    *:url) echo "URL" ;;
    de:status) echo "Status" ;;
    *:status) echo "Status" ;;
    de:logs) echo "Logs" ;;
    *:logs) echo "Logs" ;;
    de:restart) echo "Neustart" ;;
    *:restart) echo "Restart" ;;
    de:stop) echo "Stoppen" ;;
    *:stop) echo "Stop" ;;
    de:error_trap) echo "Etwas ist schiefgelaufen" ;;
    *:error_trap) echo "Something went wrong" ;;
    *) echo "$key" ;;
  esac
}

step()  { echo -e "\n${PURPLE}${BOLD}> $*${NC}"; }
info()  { echo -e "  ${DIM}$*${NC}"; }
ok()    { echo -e "  ${GREEN}OK${NC} $*"; }
warn()  { echo -e "  ${YELLOW}WARN${NC} $*"; }
die()   { echo -e "\n${RED}ERROR:${NC} $*" >&2; exit 1; }
phase() { CURRENT_STEP=$((CURRENT_STEP+1)); step "[$CURRENT_STEP/$TOTAL_STEPS] $*"; }

progress_bar() {
  local cur=$1 total=$2 label="${3:-}" width=24 filled empty bar
  [ "$total" -gt 0 ] || total=1
  filled=$(( cur * width / total ))
  empty=$(( width - filled ))
  bar="$(printf '%*s' "$filled" '' | tr ' ' '#')$(printf '%*s' "$empty" '' | tr ' ' '.')"
  printf '\r  %s[%s]%s %2d/%-2d  %-28.28s' "$CYAN" "$bar" "$NC" "$cur" "$total" "$label"
}

spinner() {
  local pid=$1 text="$2" frames='|/-\' i=0
  if [ ! -t 1 ]; then info "$text"; return 0; fi
  while kill -0 "$pid" 2>/dev/null; do
    i=$(( (i + 1) % 4 ))
    printf '\r  %s%s%s %s' "$CYAN" "${frames:$i:1}" "$NC" "$text"
    sleep 0.2
  done
  printf '\r%*s\r' 80 ''
}

run_spin() {
  local text="$1"; shift
  ( "$@" ) >"$SPIN_LOG" 2>&1 &
  local pid=$!
  spinner "$pid" "$text"
  if wait "$pid"; then
    return 0
  fi
  echo
  tail -n 12 "$SPIN_LOG" 2>/dev/null | sed 's/^/      /'
  return 1
}

trap 'die "$(t error_trap) (line $LINENO). journalctl -u runvard -e"' ERR

# --------------------------- options ----------------------------------
usage() { t usage; }
need_value() { [ -n "${2:-}" ] || die "$(t missing_value): $1"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --lang) need_value "$1" "${2:-}"; LANG_CODE="$2"; RUNVARD_LANG="$2"; shift 2 ;;
    --port) need_value "$1" "${2:-}"; RUNVARD_PORT="$2"; shift 2 ;;
    --user) need_value "$1" "${2:-}"; RUNVARD_USER="$2"; shift 2 ;;
    -y|--yes) ASSUME_YES=1; RUNVARD_YES=1; shift ;;
    -h|--help) [ "$LANG_CODE" = "de" ] || LANG_CODE="en"; usage; exit 0 ;;
    *) die "$(t unknown_option): $1 (--help)" ;;
  esac
done

case "$LANG_CODE" in
  en|de) ;;
  "")
    if [ "$ASSUME_YES" != "1" ] && [ -t 0 ] && [ -t 1 ]; then
      echo -e "${CYAN}${BOLD}runvard installer${NC}"
      echo "  $(t choose_lang)"
      read -r -p "  > " lang_choice </dev/tty || lang_choice=""
      case "$lang_choice" in
        2|de|DE|Deutsch|deutsch) LANG_CODE="de" ;;
        *) LANG_CODE="en" ;;
      esac
    else
      LANG_CODE="en"
    fi
    ;;
  *) LANG_CODE="en" ;;
esac

# ------------------------- helpers ------------------------------------
ask() {
  local prompt="$1" def="$2" ans=""
  if [ "$ASSUME_YES" = "1" ]; then
    echo "$def"
    return
  fi
  read -r -p "  ${prompt} ${DIM}[${def}]${NC} " ans </dev/tty || ans=""
  echo "${ans:-$def}"
}

read_password() {
  local prompt="$1" pass=""
  read -r -s -p "  ${prompt}: " pass </dev/tty || pass=""
  echo >&2
  printf '%s' "$pass"
}

random_password() {
  openssl rand -base64 18 2>/dev/null | tr -d '/+=' | cut -c1-18 || true
}

enable_extra_repos() {
  local os_id="" codename=""
  os_id="$(. /etc/os-release 2>/dev/null && echo "${ID:-}")"
  codename="$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME:-}")"
  [ -n "$codename" ] || return 1

  case "$os_id" in
    debian)
      if [ -f /etc/apt/sources.list ]; then
        sed -i -E 's/^(deb .* main)( contrib)?( non-free)?( non-free-firmware)?/\1 contrib non-free non-free-firmware/' /etc/apt/sources.list
      fi
      for f in /etc/apt/sources.list.d/*.sources; do
        [ -f "$f" ] || continue
        sed -i -E 's/^Components: .*/Components: main contrib non-free non-free-firmware/' "$f"
      done
      for f in /etc/apt/sources.list.d/*.list; do
        [ -f "$f" ] || continue
        sed -i -E 's/^(deb .* main)( contrib)?( non-free)?( non-free-firmware)?/\1 contrib non-free non-free-firmware/' "$f"
      done
      ;;
    ubuntu)
      if [ -f /etc/apt/sources.list ]; then
        sed -i -E 's/^(deb .* main)( restricted)?( universe)?( multiverse)?/\1 restricted universe multiverse/' /etc/apt/sources.list
      fi
      for f in /etc/apt/sources.list.d/*.sources; do
        [ -f "$f" ] || continue
        sed -i -E 's/^Components: .*/Components: main restricted universe multiverse/' "$f"
      done
      for f in /etc/apt/sources.list.d/*.list; do
        [ -f "$f" ] || continue
        sed -i -E 's/^(deb .* main)( restricted)?( universe)?( multiverse)?/\1 restricted universe multiverse/' "$f"
      done
      ;;
    *) return 1 ;;
  esac
}

install_apt_packages() {
  local packages=("$@") failed=() retry_failed=() total=${#packages[@]} idx=0 p
  for p in "${packages[@]}"; do
    idx=$((idx+1))
    progress_bar "$idx" "$total" "$p"
    apt-get install -y -qq "$p" >>"$SPIN_LOG" 2>&1 || failed+=("$p")
  done
  echo
  if [ "${#failed[@]}" -eq 0 ]; then
    ok "$(t packages_ok)"
    return 0
  fi

  warn "$(t packages_failed): ${failed[*]}"
  enable_extra_repos || true
  run_spin "$(t update_apt)" apt-get update -qq || true
  for p in "${failed[@]}"; do
    apt-get install -y -qq "$p" >>"$SPIN_LOG" 2>&1 || retry_failed+=("$p")
  done
  [ "${#retry_failed[@]}" -eq 0 ] || die "$(t packages_failed): ${retry_failed[*]}"
  ok "$(t packages_ok)"
}

install_compose_plugin() {
  systemctl enable --now docker >/dev/null 2>&1 || true
  if docker compose version >/dev/null 2>&1; then
    ok "$(t compose_ok)"
    return 0
  fi

  if run_spin "$(t compose_install)" apt-get install -y -qq docker-compose-plugin \
      && docker compose version >/dev/null 2>&1; then
    ok "$(t compose_ok)"
    return 0
  fi

  if run_spin "$(t compose_install)" bash -c '
      set -e
      arch=$(uname -m)
      case "$arch" in
        x86_64) a=x86_64 ;;
        aarch64|arm64) a=aarch64 ;;
        armv7l) a=armv7 ;;
        *) a=$arch ;;
      esac
      mkdir -p /usr/local/lib/docker/cli-plugins
      curl -fSL --max-time 180 \
        "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${a}" \
        -o /usr/local/lib/docker/cli-plugins/docker-compose
      chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
      docker compose version >/dev/null 2>&1
    '; then
    ok "$(t compose_ok)"
  else
    warn "$(t compose_warn)"
  fi
}

sync_program_files() {
  rsync -a --delete \
    --exclude 'data' \
    --exclude 'venv' \
    --exclude '.venv' \
    --exclude '.git' \
    --exclude '.pytest_cache' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude '*.bak*' \
    --exclude 'tests' \
    "$SRC"/ "$INSTALL_DIR"/
}

# ---------------------------- preflight --------------------------------
[ "$(id -u)" -eq 0 ] || die "$(t root_required)"
command -v apt-get >/dev/null 2>&1 || die "$(t apt_required)"

INSTALL_DIR="/opt/runvard"
SERVICE_FILE="/etc/systemd/system/runvard.service"
ENV_FILE="${INSTALL_DIR}/data/runvard.env"
VERSION_FILE="${INSTALL_DIR}/data/runvard.version"
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
SRC="${RUNVARD_SOURCE_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
[ -f "${SRC}/server.py" ] || die "$(t source_missing)"

if [ -f "$ENV_FILE" ]; then
  # shellcheck source=/dev/null
  . "$ENV_FILE"
fi

clear 2>/dev/null || true
echo -e "${CYAN}${BOLD}"
cat <<'BANNER'
  ____  _   _ _   ___     ___    ____  ____
 |  _ \| | | | \ | \ \   / / \  |  _ \|  _ \
 | |_) | | | |  \| |\ \ / / _ \ | |_) | | | |
 |  _ <| |_| | |\  | \ V / ___ \|  _ <| |_| |
 |_| \_\\___/|_| \_|  \_/_/   \_\_| \_\____/
BANNER
echo -e "${NC}  ${DIM}runvard installer${NC}\n"

# -------------------------- configuration ------------------------------
step "$(t config)"
ADMIN_USER="$(ask "$(t admin_user)" "${RUNVARD_USER:-admin}")"

ADMIN_PASS="${RUNVARD_PASS:-}"
GEN_PASS=0
if [ "$ASSUME_YES" != "1" ] && [ -z "$ADMIN_PASS" ]; then
  while :; do
    p1="$(read_password "$(t admin_pass)")"
    if [ -z "$p1" ]; then
      GEN_PASS=1
      break
    fi
    p2="$(read_password "$(t admin_pass_repeat)")"
    if [ "$p1" = "$p2" ]; then
      ADMIN_PASS="$p1"
      break
    fi
    warn "$(t password_mismatch)"
  done
fi
if [ -z "$ADMIN_PASS" ]; then
  GEN_PASS=1
  ADMIN_PASS="$(random_password)"
  [ -n "$ADMIN_PASS" ] || ADMIN_PASS="$(head -c 18 /dev/urandom | base64 | tr -d '/+=' | cut -c1-18)"
fi

while :; do
  PORT="$(ask "$(t web_port)" "${RUNVARD_PORT:-8080}")"
  if [[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ]; then
    break
  fi
  warn "$(t invalid_port)"
  [ "$ASSUME_YES" = "1" ] && die "$(t invalid_port)"
done

info "$(t full_install)"
echo
echo -e "  ${BOLD}$(t summary)${NC}"
echo -e "  ${DIM}------------------------------${NC}"
echo -e "  $(t user)     : ${BOLD}${ADMIN_USER}${NC}"
echo -e "  $(t password) : ${BOLD}$( [ "$GEN_PASS" = "1" ] && t generated || t hidden )${NC}"
echo -e "  Port     : ${BOLD}${PORT}${NC}"
echo -e "  $(t scope)    : ${BOLD}$(t complete)${NC}"
echo -e "  $(t target)   : ${BOLD}${INSTALL_DIR}${NC}"
echo
if [ "$ASSUME_YES" != "1" ]; then
  confirm="$(ask "$(t start_install)" "y")"
  case "$confirm" in y|Y|j|J|yes|Yes|ja|Ja) ;; *) die "$(t aborted)" ;; esac
fi

# --------------------------- 1 packages --------------------------------
phase "$(t packages_phase)"
export DEBIAN_FRONTEND=noninteractive

PKGS=(
  python3 python3-pip python3-venv python3-dev gcc pkg-config
  rsync curl ca-certificates git openssl sudo cron
  btop htop smartmontools mdadm parted lvm2 cryptsetup dosfstools
  e2fsprogs xfsprogs btrfs-progs zfsutils-linux
  samba nfs-kernel-server nfs-common cifs-utils
  docker.io
  qemu-kvm libvirt-daemon-system libvirt-clients libvirt-dev virtinst
  open-iscsi
  ufw iptables iproute2 isc-dhcp-client
  unattended-upgrades tuned kdump-tools sosreport vsftpd
  apparmor-utils power-profiles-daemon
)

run_spin "$(t update_apt)" apt-get update -qq || warn "$(t apt_warn)"
install_apt_packages "${PKGS[@]}"
install_compose_plugin

# ----------------------------- 2 files ---------------------------------
phase "$(t copy_phase)"
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/data"
if [ "$SRC" != "$INSTALL_DIR" ]; then
  sync_program_files
  ok "$(t files_ok)"
else
  info "Source already matches target; skipping file sync."
fi

SOURCE_COMMIT="${RUNVARD_SOURCE_COMMIT:-}"
if [ -z "$SOURCE_COMMIT" ] && [ -d "$SRC/.git" ] && command -v git >/dev/null 2>&1; then
  SOURCE_COMMIT="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)"
fi
if [[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  printf '%s\n' "$SOURCE_COMMIT" > "$VERSION_FILE"
  ok "$(t version_ok)"
fi

# ----------------------------- 3 python --------------------------------
phase "$(t venv_phase)"
PIP="$INSTALL_DIR/venv/bin/pip"
if [ -d "$INSTALL_DIR/venv" ] && ! "$PIP" --version >/dev/null 2>&1; then
  warn "$(t venv_rebuild)"
  rm -rf "$INSTALL_DIR/venv"
fi
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
  run_spin "$(t venv_create)" python3 -m venv "$INSTALL_DIR/venv" \
    || die "Could not create Python virtual environment."
fi

if [ -d "$INSTALL_DIR/wheels" ] && [ -n "$(ls -A "$INSTALL_DIR/wheels" 2>/dev/null)" ]; then
  run_spin "$(t pip_install_offline)" \
    "$PIP" install -q --no-index --find-links "$INSTALL_DIR/wheels" -r "$INSTALL_DIR/requirements.txt" \
    || die "Python package installation from bundled wheels failed."
  run_spin "$(t pip_upgrade)" "$PIP" install -q --upgrade pip \
    || die "Could not upgrade pip."
else
  run_spin "$(t pip_upgrade)" "$PIP" install -q --upgrade pip \
    || die "Could not upgrade pip."
  run_spin "$(t pip_install_online)" "$PIP" install -q -r "$INSTALL_DIR/requirements.txt" \
    || die "Python package installation failed."
fi
run_spin "$(t libvirt_install)" "$PIP" install -q libvirt-python || warn "libvirt-python could not be installed."
ok "$(t venv_ok)"

# ----------------------------- 4 config --------------------------------
phase "$(t config_phase)"
umask 077
cat > "$ENV_FILE" <<EOF
# Generated by install.sh - central runvard configuration
RUNVARD_USER=${ADMIN_USER}
RUNVARD_PASS=${ADMIN_PASS}
RUNVARD_PORT=${PORT}
RUNVARD_LANG=${LANG_CODE}
EOF
chmod 600 "$ENV_FILE"
ok "$(t config_ok): ${DIM}${ENV_FILE}${NC}"

# ----------------------------- 5 service -------------------------------
phase "$(t service_phase)"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=runvard Server Panel
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${INSTALL_DIR}/venv/bin/uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 1
# Optional TLS/HTTPS:
# ExecStart=${INSTALL_DIR}/venv/bin/uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 1 --ssl-keyfile ${INSTALL_DIR}/data/certs/<CN>.key --ssl-certfile ${INSTALL_DIR}/data/certs/<CN>.crt
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable runvard >/dev/null 2>&1 || true
systemctl restart runvard
ok "$(t service_ok)"

# ----------------------------- 6 health --------------------------------
phase "$(t health_phase)"
info "$(t wait_service) ..."
HTTP="000"
for _ in $(seq 1 20); do
  HTTP="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:${PORT}/login" || true)"
  case "$HTTP" in
    200|302) break ;;
  esac
  sleep 1
done
case "$HTTP" in
  200|302) ok "$(t health_ok) HTTP ${HTTP}" ;;
  *) die "$(t health_fail)" ;;
esac

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$HOST_IP" ] || HOST_IP="127.0.0.1"

echo
echo -e "${GREEN}${BOLD}$(t done)${NC}"
echo
echo -e "  $(t url)      : ${BOLD}http://${HOST_IP}:${PORT}${NC}"
echo -e "  $(t user)     : ${BOLD}${ADMIN_USER}${NC}"
echo -e "  $(t password) : ${BOLD}${ADMIN_PASS}${NC}"
echo
echo -e "  $(t status)  : ${BOLD}systemctl status runvard${NC}"
echo -e "  $(t logs)    : ${BOLD}journalctl -u runvard -f${NC}"
echo -e "  $(t restart) : ${BOLD}systemctl restart runvard${NC}"
echo -e "  $(t stop)    : ${BOLD}systemctl stop runvard${NC}"
echo
