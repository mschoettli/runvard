#!/bin/bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Run this updater as root.${NC}"
  exit 1
fi

INSTALL="/opt/runvard"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${INSTALL}/data/runvard.env"
VERSION_FILE="${INSTALL}/data/runvard.version"
SERVICE_FILE="/etc/systemd/system/runvard.service"

echo -e "${CYAN}Updating runvard...${NC}"

if [ ! -f "$ENV_FILE" ]; then
  echo -e "${RED}Missing ${ENV_FILE}. Run install.sh first.${NC}"
  exit 1
fi

TS=$(date +%Y%m%d%H%M%S)
echo -e "${CYAN}Backing up current files (.bak.${TS})...${NC}"
for f in server.py requirements.txt runvard.service static/index.html static/login.html static/btop.html; do
  [ -f "$INSTALL/$f" ] && cp -f "$INSTALL/$f" "$INSTALL/$f.bak.$TS"
done
for m in "$INSTALL/modules/"*.py; do
  [ -f "$m" ] && cp -f "$m" "$m.bak.$TS"
done

if [ "$SRC" != "$INSTALL" ]; then
  rsync -a --delete \
    --exclude '.git' --exclude '.DS_Store' \
    --exclude 'data' --exclude 'venv' --exclude '.venv' \
    --exclude '__pycache__' --exclude '.pytest_cache' \
    --exclude '*.pyc' --exclude '*.pyo' --exclude '*.bak*' \
    "$SRC"/ "$INSTALL"/
else
  echo -e "${CYAN}Update source is already ${INSTALL}; skipping file sync.${NC}"
fi
chmod +x "$INSTALL/scripts/verify-local.sh" \
         "$INSTALL/scripts/verify-target-host.sh" \
         "$INSTALL/scripts/verify-api-only.sh" 2>/dev/null || true
if [ -f "$INSTALL/runvard.service" ]; then
  cp -f "$INSTALL/runvard.service" "$SERVICE_FILE"
  systemctl daemon-reload
fi
SOURCE_COMMIT="${RUNVARD_SOURCE_COMMIT:-}"
if [ -z "$SOURCE_COMMIT" ] && [ -d "$SRC/.git" ] && command -v git >/dev/null 2>&1; then
  SOURCE_COMMIT="$(git -C "$SRC" rev-parse HEAD 2>/dev/null || true)"
fi
if [[ "$SOURCE_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  printf '%s\n' "$SOURCE_COMMIT" > "$VERSION_FILE"
fi

"$INSTALL/venv/bin/pip" install -q -r "$INSTALL/requirements.txt"
"$INSTALL/venv/bin/pip" install -q libvirt-python==10.7.0 2>/dev/null || true

echo -e "${CYAN}Restarting service...${NC}"
systemctl restart runvard
sleep 3

# shellcheck source=/dev/null
. "$ENV_FILE"
# Health-Check ohne Auth: die API nutzt Cookie-Sessions (kein HTTP-Basic),
# daher die anmeldefreie Login-Seite prüfen.
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "http://127.0.0.1:${RUNVARD_PORT:-8080}/login")

if [ "$HTTP" = "200" ] || [ "$HTTP" = "302" ]; then
  echo -e "${GREEN}Update succeeded. Dienst antwortet (HTTP ${HTTP}).${NC}"
else
  echo -e "${RED}Update failed. Dienst antwortet nicht (HTTP ${HTTP}).${NC}"
  journalctl -u runvard -n 20 --no-pager
fi
