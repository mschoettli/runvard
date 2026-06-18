#!/usr/bin/env bash
#
# Run the target-host HTTP/API contract verifier against a temporary local
# runvard process. This is intentionally API-only: it does not prove systemd,
# Docker, libvirt, storage, or other target-host integrations.
set -euo pipefail

SCRIPT_DIR="${0%/*}"
if [ "$SCRIPT_DIR" = "$0" ]; then
  SCRIPT_DIR="."
fi
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="${PYTHON_BIN_FALLBACK:-python3}"
fi

for tool in curl mktemp rm sleep "$PYTHON_BIN"; do
  command -v "$tool" >/dev/null 2>&1
done

VERIFY_TMP="$(mktemp -d "${TMPDIR:-/tmp}/runvard-api-only.XXXXXX")"
RUNVARD_PORT="${RUNVARD_PORT:-8876}"
RUNVARD_URL="${RUNVARD_URL:-http://127.0.0.1:${RUNVARD_PORT}}"
RUNVARD_USER="${RUNVARD_USER:-admin}"
RUNVARD_PASS="${RUNVARD_PASS:-runvard}"
SERVER_PID=""

cleanup() {
  if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  rm -rf "$VERIFY_TMP"
}
trap cleanup EXIT HUP INT TERM

export RUNVARD_DATA_DIR="${RUNVARD_DATA_DIR:-${VERIFY_TMP}/data}"
export RUNVARD_USER
export RUNVARD_PASS
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-${VERIFY_TMP}/pycache}"

"$PYTHON_BIN" -m uvicorn server:app --host 127.0.0.1 --port "$RUNVARD_PORT" \
  > "${VERIFY_TMP}/uvicorn.log" 2>&1 &
SERVER_PID="$!"

ready=0
for ((attempt = 1; attempt <= 80; attempt++)); do
  if curl -fsS "${RUNVARD_URL}/login" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

if [ "$ready" != "1" ]; then
  sed -n '1,80p' "${VERIFY_TMP}/uvicorn.log" >&2
  echo "runvard API did not become ready at ${RUNVARD_URL}" >&2
  exit 1
fi

RUNVARD_API_ONLY=1 \
RUNVARD_URL="$RUNVARD_URL" \
RUNVARD_USER="$RUNVARD_USER" \
RUNVARD_PASS="$RUNVARD_PASS" \
  scripts/verify-target-host.sh
