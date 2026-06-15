#!/usr/bin/env bash
#
# runvard – Einsteigerfreundliches Installationsskript
# Aufruf:  sudo bash install.sh
#
set -euo pipefail

# ─────────────────────────── Farben & Symbole ───────────────────────────
if [ -t 1 ] && [ "${NO_COLOR:-}" = "" ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[0;31m'; GREEN=$'\033[0;32m'
  YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; PURPLE=$'\033[0;35m'; NC=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; PURPLE=""; NC=""
fi

step()  { echo -e "\n${PURPLE}${BOLD}▸ $*${NC}"; }
info()  { echo -e "  ${DIM}$*${NC}"; }
ok()    { echo -e "  ${GREEN}✓${NC} $*"; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; }
die()   { echo -e "\n${RED}✗ $*${NC}\n" >&2; exit 1; }

TOTAL_STEPS=6
CURRENT=0
phase() { CURRENT=$((CURRENT+1)); step "[${CURRENT}/${TOTAL_STEPS}] $*"; }

# Fortschrittsbalken:  [#########·········]  5/12  paketname
progress_bar() {
  local cur=$1 total=$2 label="${3:-}" width=24 filled empty bar
  [ "$total" -gt 0 ] || total=1
  filled=$(( cur * width / total )); empty=$(( width - filled ))
  bar="$(printf '%*s' "$filled" '' | tr ' ' '#')$(printf '%*s' "$empty" '' | tr ' ' '·')"
  printf '\r  %s[%s]%s %2d/%-2d  %-22.22s' "$CYAN" "$bar" "$NC" "$cur" "$total" "$label"
}

# Spinner, der läuft solange ein Hintergrundprozess (PID) aktiv ist
spinner() {
  local pid=$1 text="$2" frames='|/-\' i=0
  if [ ! -t 1 ]; then info "$text"; return 0; fi   # ohne Terminal: nur Text
  while kill -0 "$pid" 2>/dev/null; do
    i=$(( (i + 1) % 4 ))
    printf '\r  %s%s%s %s' "$CYAN" "${frames:$i:1}" "$NC" "$text"
    sleep 0.2
  done
  printf '\r%*s\r' 70 ''   # Zeile löschen
}

# Befehl im Hintergrund ausführen, derweil Spinner zeigen; Logausgabe puffern
SPIN_LOG="/tmp/runvard_install.log"
run_spin() {
  local text="$1"; shift
  ( "$@" ) >"$SPIN_LOG" 2>&1 &
  local pid=$!
  spinner "$pid" "$text"
  if wait "$pid"; then return 0; fi
  echo; tail -n 8 "$SPIN_LOG" 2>/dev/null | sed 's/^/      /'; return 1
}

enable_extra_repos() {
  local os_id=""
  local codename=""
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
    *)
      return 1
      ;;
  esac
}

install_apt_packages() {
  local packages=("$@")
  local failed=()
  local total=${#packages[@]}
  local idx=0

  info "Installiere ${total} Pakete:"
  for p in "${packages[@]}"; do
    idx=$((idx+1))
    progress_bar "$idx" "$total" "$p"
    apt-get install -y -qq "$p" >>"$SPIN_LOG" 2>&1 || failed+=("$p")
  done
  progress_bar "$total" "$total" "fertig"; echo

  if [ "${#failed[@]}" -eq 0 ]; then
    ok "Pakete installiert."
    return 0
  fi

  warn "Nicht installierbar beim ersten Versuch: ${failed[*]}"
  info "Aktiviere zusaetzliche Paketquellen-Komponenten und versuche erneut ..."
  enable_extra_repos || warn "Konnte Paketquellen-Komponenten nicht automatisch erweitern."
  run_spin "Paketquellen erneut aktualisieren ..." apt-get update -qq \
    || die "Paketquellen konnten nach Repository-Anpassung nicht aktualisiert werden."

  local retry_failed=()
  for p in "${failed[@]}"; do
    apt-get install -y -qq "$p" >>"$SPIN_LOG" 2>&1 || retry_failed+=("$p")
  done

  if [ "${#retry_failed[@]}" -gt 0 ]; then
    die "Diese Pakete konnten nicht installiert werden: ${retry_failed[*]}"
  fi

  ok "Alle Pakete installiert."
}

# Bei jedem Fehler eine hilfreiche Meldung statt eines kryptischen Abbruchs
trap 'die "Etwas ist schiefgelaufen (Zeile $LINENO). Prüfe die Ausgabe oben.\n  Logs nach der Installation:  journalctl -u runvard -e"' ERR

# ─────────────────────────── Kommandozeilen-Optionen ───────────────────────────
usage() {
  cat << USAGE
runvard – Installations-Assistent

Verwendung:
  sudo bash install.sh [Optionen]

Optionen:
  --port <n>         Web-Port (1-65535, Standard 8080)
  --user <name>      Admin-Benutzername (Standard admin)
  -y, --yes          Ohne Rückfragen installieren (nutzt Standardwerte/Flags)
  -h, --help         Diese Hilfe anzeigen

Das Passwort wird aus Sicherheitsgründen nicht als Option übergeben, sondern
interaktiv abgefragt oder über die Umgebungsvariable RUNVARD_PASS gesetzt
(leer = automatisch erzeugtes Zufallspasswort).

Beispiele:
  sudo bash install.sh --port 9090
  sudo RUNVARD_PASS='geheim' bash install.sh --yes --port 8443
USAGE
}

_need() { [ -n "${2:-}" ] || die "Option $1 benötigt einen Wert (--help für Hilfe)"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --port)    _need "$1" "${2:-}"; RUNVARD_PORT="$2"; shift 2 ;;
    --user)    _need "$1" "${2:-}"; RUNVARD_USER="$2"; shift 2 ;;
    -y|--yes)  RUNVARD_YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)         die "Unbekannte Option: $1  (--help für Hilfe)" ;;
  esac
done

# ─────────────────────────── Banner ───────────────────────────
clear 2>/dev/null || true
echo -e "${CYAN}${BOLD}"
cat << 'BANNER'
  ██████╗ ██╗   ██╗███╗   ██╗██╗   ██╗ █████╗ ██████╗ ██████╗
  ██╔══██╗██║   ██║████╗  ██║██║   ██║██╔══██╗██╔══██╗██╔══██╗
  ██████╔╝██║   ██║██╔██╗ ██║██║   ██║███████║██████╔╝██║  ██║
  ██╔══██╗██║   ██║██║╚██╗██║╚██╗ ██╔╝██╔══██║██╔══██╗██║  ██║
  ██║  ██║╚██████╔╝██║ ╚████║ ╚████╔╝ ██║  ██║██║  ██║██████╔╝
  ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═══╝  ╚═══╝  ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝
BANNER
echo -e "${NC}  ${DIM}Dein Server. Deine Kontrolle. – Installations-Assistent${NC}\n"

# ─────────────────────────── Vorbedingungen ───────────────────────────
[ "$(id -u)" -eq 0 ] || die "Bitte mit root-Rechten starten:  ${BOLD}sudo bash install.sh${NC}"
command -v apt-get >/dev/null 2>&1 || die "Dieses Skript benötigt Debian/Ubuntu (apt wurde nicht gefunden)."

INSTALL_DIR="/opt/runvard"
SERVICE_FILE="/etc/systemd/system/runvard.service"
ENV_FILE="${INSTALL_DIR}/data/runvard.env"
VERSION_FILE="${INSTALL_DIR}/data/runvard.version"
LEGACY_INSTALL_DIR="/opt/actax"
LEGACY_SERVICE_FILE="/etc/systemd/system/actax.service"
LEGACY_ENV_FILE="${LEGACY_INSTALL_DIR}/data/actax.env"
SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
SRC="${RUNVARD_SOURCE_DIR:-${ACTAX_SOURCE_DIR:-$(cd "${SCRIPT_DIR}/.." && pwd)}}"
[ -f "${SRC}/server.py" ] || die "server.py nicht gefunden. Installer bitte aus dem runvard-Release starten."

if [ ! -f "$ENV_FILE" ] && [ -f "$LEGACY_ENV_FILE" ]; then
  # shellcheck source=/dev/null
  . "$LEGACY_ENV_FILE"
  RUNVARD_USER="${RUNVARD_USER:-${ACTAX_USER:-}}"
  RUNVARD_PASS="${RUNVARD_PASS:-${ACTAX_PASS:-}}"
  RUNVARD_PORT="${RUNVARD_PORT:-${ACTAX_PORT:-}}"
fi

# Vorhandene Installation erkennen
REINSTALL=0
if [ -d "$INSTALL_DIR" ] && [ -f "$SERVICE_FILE" ]; then
  REINSTALL=1
  warn "runvard ist bereits installiert. Der Code wird aktualisiert, deine Daten (Konten, Schlüssel, Zertifikate) bleiben erhalten."
elif [ -d "$LEGACY_INSTALL_DIR" ] || [ -f "$LEGACY_SERVICE_FILE" ]; then
  REINSTALL=1
  warn "actax-Installation gefunden. Daten und Zugangsdaten werden nach runvard übernommen."
fi

# ─────────────────────────── Eingaben (mit Defaults) ───────────────────────────
ASSUME_YES="${RUNVARD_YES:-${ACTAX_YES:-0}}"
ask() {  # ask "Frage" "default" -> Antwort auf stdout
  local prompt="$1" def="$2" ans=""
  if [ "$ASSUME_YES" = "1" ]; then echo "$def"; return; fi
  read -r -p "  ${prompt} ${DIM}[${def}]${NC} " ans </dev/tty || ans=""
  echo "${ans:-$def}"
}

step "Konfiguration"
ADMIN_USER="$(ask 'Admin-Benutzername' "${RUNVARD_USER:-${ACTAX_USER:-admin}}")"

# Passwort: versteckt einlesen, bestätigen; leer => sicheres Zufallspasswort
ADMIN_PASS="${RUNVARD_PASS:-${ACTAX_PASS:-}}"
GEN_PASS=0
if [ "$ASSUME_YES" != "1" ] && [ -z "$ADMIN_PASS" ]; then
  while :; do
    read -r -s -p "  Admin-Passwort (leer = automatisch erzeugen): " p1 </dev/tty; echo
    if [ -z "$p1" ]; then GEN_PASS=1; break; fi
    read -r -s -p "  Passwort wiederholen: " p2 </dev/tty; echo
    if [ "$p1" = "$p2" ]; then ADMIN_PASS="$p1"; break; fi
    warn "Die Passwörter stimmen nicht überein – bitte erneut."
  done
fi
if [ -z "$ADMIN_PASS" ]; then
  GEN_PASS=1
  ADMIN_PASS="$(openssl rand -base64 12 2>/dev/null | tr -d '/+=' | cut -c1-16 || true)"
  [ -n "$ADMIN_PASS" ] || ADMIN_PASS="$(head -c 12 /dev/urandom | base64 | tr -d '/+=' | cut -c1-16)"
fi

# Port (validiert)
while :; do
  PORT="$(ask 'Web-Port' "${RUNVARD_PORT:-${ACTAX_PORT:-8080}}")"
  if [[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ]; then break; fi
  warn "Bitte eine gültige Portnummer (1–65535) angeben."
  [ "$ASSUME_YES" = "1" ] && die "Ungültiger Port: ${PORT}"
done

# Komponenten: immer vollständige Installation (alle Module)
echo
info "Es werden alle Komponenten installiert (Speicher, Verschlüsselung,"
info "Datei-Freigaben, Docker, Virtualisierung (KVM), ZFS/Btrfs/iSCSI, Wartungstools)."
WANT_KVM=1

# Zusammenfassung & Bestätigung
echo
echo -e "  ${BOLD}Zusammenfassung${NC}"
echo -e "  ${DIM}────────────────────────────────${NC}"
echo -e "  Benutzer : ${BOLD}${ADMIN_USER}${NC}"
echo -e "  Passwort : ${BOLD}$( [ "$GEN_PASS" = "1" ] && echo "(wird automatisch erzeugt)" || echo "(verborgen)")${NC}"
echo -e "  Port     : ${BOLD}${PORT}${NC}"
echo -e "  Umfang   : ${BOLD}Vollständig (alle Komponenten)${NC}"
echo -e "  Ziel     : ${BOLD}${INSTALL_DIR}${NC}"
echo
if [ "$ASSUME_YES" != "1" ]; then
  c="$(ask 'Installation jetzt starten? (j/n)' 'j')"
  case "$c" in j|J|y|Y|ja|Ja|yes) : ;; *) die "Abgebrochen. Es wurde nichts verändert." ;; esac
fi

# ─────────────────────────── 1. Pakete ───────────────────────────
phase "Systempakete installieren"
export DEBIAN_FRONTEND=noninteractive

PKGS=(python3 python3-pip python3-venv rsync curl ca-certificates btop htop smartmontools mdadm parted lvm2)
PKGS+=(cryptsetup dosfstools samba nfs-kernel-server docker.io)
PKGS+=(qemu-kvm libvirt-daemon-system libvirt-clients virtinst
       zfsutils-linux btrfs-progs open-iscsi
       unattended-upgrades tuned kdump-tools sosreport vsftpd
       gcc python3-dev libvirt-dev pkg-config)

info "Paketquellen aktualisieren …"
run_spin "Paketquellen aktualisieren …" apt-get update -qq \
  || warn "apt-get update meldete Warnungen – fahre fort."

install_apt_packages "${PKGS[@]}"

# Docker Compose v2 sicherstellen – runvard' Apps-Funktion braucht "docker compose".
# Debians docker.io bringt das Plugin NICHT mit.
case " ${PKGS[*]} " in
  *" docker.io "*)
    systemctl enable --now docker >/dev/null 2>&1 || true
    if docker compose version >/dev/null 2>&1; then
      ok "Docker Compose v2 vorhanden."
    else
      run_spin "Docker Compose v2 nachrüsten …" bash -c '
        set -e
        if apt-get install -y -qq docker-compose-plugin >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
          exit 0
        fi
        arch=$(uname -m)
        case "$arch" in x86_64) a=x86_64;; aarch64|arm64) a=aarch64;; armv7l) a=armv7;; *) a=$arch;; esac
        mkdir -p /usr/local/lib/docker/cli-plugins
        curl -fSL --max-time 180 \
          "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-${a}" \
          -o /usr/local/lib/docker/cli-plugins/docker-compose
        chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
        docker compose version >/dev/null 2>&1
      ' && ok "Docker Compose v2 installiert." \
        || warn "Docker Compose v2 nicht installiert – Apps brauchen es (mit Internet nachrüstbar)."
    fi
    ;;
esac

# ─────────────────────────── 2. Dateien kopieren ───────────────────────────
phase "runvard nach ${INSTALL_DIR} kopieren"
if [ ! -d "$INSTALL_DIR" ] && [ -d "$LEGACY_INSTALL_DIR" ]; then
  mv "$LEGACY_INSTALL_DIR" "$INSTALL_DIR"
  ok "Bestehende Daten nach ${INSTALL_DIR} übernommen."
fi
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/data"
if [ "$SRC" != "$INSTALL_DIR" ]; then
  # data/ niemals überschreiben; venv & Müll ausschließen
  rsync -a --delete \
    --exclude 'data' --exclude 'venv' --exclude '__pycache__' \
    --exclude '*.pyc' --exclude '*.bak*' \
    "$SRC"/ "$INSTALL_DIR"/
  ok "Programmdateien kopiert."
else
  info "Installation läuft bereits im Zielverzeichnis – kein Kopieren nötig."
fi
SOURCE_COMMIT="${RUNVARD_SOURCE_COMMIT:-${ACTAX_SOURCE_COMMIT:-}}"
if [ -z "$SOURCE_COMMIT" ] && [ -d "$SRC/.git" ] && command -v git >/dev/null 2>&1; then
  SOURCE_COMMIT="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)"
fi
if [[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  printf '%s\n' "$SOURCE_COMMIT" > "$VERSION_FILE"
fi

# ─────────────────────────── 3. Python-Umgebung ───────────────────────────
phase "Python-Umgebung einrichten"
PIP="$INSTALL_DIR/venv/bin/pip"
if [ -d "$INSTALL_DIR/venv" ] && ! "$PIP" --version >/dev/null 2>&1; then
  warn "Bestehende Python-Umgebung ist nicht mehr gültig und wird neu erstellt."
  rm -rf "$INSTALL_DIR/venv"
fi
if [ ! -x "$INSTALL_DIR/venv/bin/python" ]; then
  run_spin "Virtuelle Umgebung anlegen …" python3 -m venv "$INSTALL_DIR/venv" \
    || die "Konnte keine Python-Umgebung anlegen (ist python3-venv installiert?)."
fi
if [ -d "$INSTALL_DIR/wheels" ] && [ -n "$(ls -A "$INSTALL_DIR/wheels" 2>/dev/null)" ]; then
  # Offline-Wheels zuerst nutzen, danach fehlende native Pakete online nachziehen.
  info "Offline-Wheels gefunden – installiere Basis-Pakete lokal."
  run_spin "Python-Pakete installieren (offline) …" \
    "$PIP" install -q --no-index --find-links "$INSTALL_DIR/wheels" -r "$INSTALL_DIR/requirements.txt" \
    || die "Offline-Installation der Python-Pakete fehlgeschlagen (siehe Ausgabe oben)."
  run_spin "pip aktualisieren ..." "$PIP" install -q --upgrade pip \
    || die "pip konnte nicht aktualisiert werden."
  if [ "$WANT_KVM" = "1" ]; then
    run_spin "libvirt-Python-Anbindung installieren ..." \
      "$PIP" install -q libvirt-python \
      || die "libvirt-python konnte nicht installiert werden."
  fi
else
  # Online-Modus: von PyPI
  run_spin "pip aktualisieren …" "$PIP" install -q --upgrade pip \
    || die "pip konnte nicht aktualisiert werden."
  run_spin "Python-Pakete installieren (kann etwas dauern) …" \
    "$PIP" install -q -r "$INSTALL_DIR/requirements.txt" \
    || die "Python-Pakete konnten nicht installiert werden (siehe Ausgabe oben)."
  if [ "$WANT_KVM" = "1" ]; then
    run_spin "libvirt-Python-Anbindung installieren …" \
      "$PIP" install -q libvirt-python \
      || die "libvirt-python konnte nicht installiert werden."
  fi
fi
ok "Python-Umgebung bereit."

# ─────────────────────────── 4. Konfiguration ───────────────────────────
phase "Zugangsdaten & Konfiguration schreiben"
umask 077
cat > "$ENV_FILE" << EOF
# Von install.sh erzeugt – zentrale Konfiguration für runvard
RUNVARD_USER=${ADMIN_USER}
RUNVARD_PASS=${ADMIN_PASS}
RUNVARD_PORT=${PORT}
EOF
chmod 600 "$ENV_FILE"
ok "Konfiguration gespeichert: ${DIM}${ENV_FILE}${NC}"

# ─────────────────────────── 5. systemd-Service ───────────────────────────
phase "Dienst einrichten und starten"
cat > "$SERVICE_FILE" << EOF
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
# TLS/HTTPS optional: Zertifikat unter System → Sicherheit → SSL erzeugen, dann
# obige Zeile durch folgende ersetzen und 'systemctl daemon-reload && systemctl restart runvard':
# ExecStart=${INSTALL_DIR}/venv/bin/uvicorn server:app --host 0.0.0.0 --port ${PORT} --workers 1 --ssl-keyfile ${INSTALL_DIR}/data/certs/<CN>.key --ssl-certfile ${INSTALL_DIR}/data/certs/<CN>.crt
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$SERVICE_FILE"

systemctl daemon-reload
if [ -f "$LEGACY_SERVICE_FILE" ]; then
  systemctl stop actax >/dev/null 2>&1 || true
  systemctl disable actax >/dev/null 2>&1 || true
  rm -f "$LEGACY_SERVICE_FILE"
  systemctl daemon-reload
fi
systemctl enable runvard >/dev/null 2>&1 || true
systemctl restart runvard

# ─────────────────────────── 6. Health-Check ───────────────────────────
phase "Funktionsprüfung"
info "Warte auf den Dienst …"
# Die API nutzt Cookie-Sessions (kein HTTP-Basic) – daher die anmeldefreie
# Login-Seite prüfen. Ein HTTP-Code != 000 bedeutet: der Dienst antwortet.
HTTP="000"
for _ in $(seq 1 20); do
  HTTP="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
    "http://127.0.0.1:${PORT}/login" 2>/dev/null || echo 000)"
  case "$HTTP" in 200|302|307) break ;; esac
  sleep 1
done

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"; LAN_IP="${LAN_IP:-127.0.0.1}"
URL="http://${LAN_IP}:${PORT}"

case "$HTTP" in
  200|302|307) ok "runvard läuft und ist erreichbar." ;;
  000) die "Dienst antwortet nicht. Status:  journalctl -u runvard -e" ;;
  *)   die "Unerwartete Antwort (HTTP ${HTTP}). Status:  journalctl -u runvard -e" ;;
esac

# ─────────────────────────── Abschluss ───────────────────────────
echo
echo -e "${GREEN}${BOLD}  ╔══════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}  ║              Installation fertig 🎉           ║${NC}"
echo -e "${GREEN}${BOLD}  ╚══════════════════════════════════════════════╝${NC}"
echo
echo -e "  🌐  Adresse   : ${BOLD}${CYAN}${URL}${NC}"
echo -e "  👤  Benutzer  : ${BOLD}${ADMIN_USER}${NC}"
if [ "$GEN_PASS" = "1" ]; then
  echo -e "  🔑  Passwort  : ${BOLD}${YELLOW}${ADMIN_PASS}${NC}   ${DIM}(automatisch erzeugt – jetzt notieren!)${NC}"
else
  echo -e "  🔑  Passwort  : ${DIM}(wie eingegeben)${NC}"
fi
echo
echo -e "  ${DIM}Nützliche Befehle:${NC}"
echo -e "    Status   : ${BOLD}systemctl status runvard${NC}"
echo -e "    Logs     : ${BOLD}journalctl -u runvard -f${NC}"
echo -e "    Neustart : ${BOLD}systemctl restart runvard${NC}"
echo -e "    Stoppen  : ${BOLD}systemctl stop runvard${NC}"
echo
[ "$REINSTALL" = "1" ] && info "Hinweis: Bestehende Daten wurden beibehalten."
echo -e "  ${DIM}Tipp: Weitere Konten findest du oben rechts im Benutzermenü.${NC}"
echo
