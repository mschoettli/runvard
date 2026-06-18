#!/usr/bin/env bash
#
# Verify a real Debian/Ubuntu target host after installing runvard.
#
# Default mode is non-destructive: it checks service health, API contracts,
# authentication, confirmation-token enforcement, and read-only subsystem
# discovery. Mutating host checks must be enabled explicitly on disposable VMs.
set -euo pipefail

RUNVARD_URL="${RUNVARD_URL:-}"
RUNVARD_USER="${RUNVARD_USER:-}"
RUNVARD_PASS="${RUNVARD_PASS:-}"
RUNVARD_DESTRUCTIVE="${RUNVARD_DESTRUCTIVE:-0}"
RUNVARD_API_ONLY="${RUNVARD_API_ONLY:-0}"
RUNVARD_TEST_SERVICE="${RUNVARD_TEST_SERVICE:-cron.service}"

pass_count=0
warn_count=0
fail_count=0

preflight_fail_count=0
for cmd in mktemp rm; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    preflight_fail_count=$((preflight_fail_count + 1))
    printf 'fail - missing command before verifier setup: %s\n' "$cmd"
  fi
done
if [ "$preflight_fail_count" -gt 0 ]; then
  printf '\n== Summary ==\npassed: 0\nwarnings: 0\nfailed: %s\n' "$preflight_fail_count"
  exit 1
fi

tmpdir="$(mktemp -d)"
cookie="${tmpdir}/cookies.txt"
body="${tmpdir}/body.txt"
invalid_upload="${tmpdir}/invalid-upload.txt"
printf 'x' > "$invalid_upload"

cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

log() {
  printf '\n== %s ==\n' "$1"
}

pass() {
  pass_count=$((pass_count + 1))
  printf 'ok  - %s\n' "$1"
}

warn() {
  warn_count=$((warn_count + 1))
  printf 'warn - %s\n' "$1"
}

fail() {
  fail_count=$((fail_count + 1))
  printf 'fail - %s\n' "$1"
}

finish() {
  log "Summary"
  printf 'passed: %s\nwarnings: %s\nfailed: %s\n' "$pass_count" "$warn_count" "$fail_count"
  if [ "$fail_count" -gt 0 ]; then
    exit 1
  fi
}

need_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    pass "command available: $1"
  else
    fail "missing command: $1"
  fi
}

http_code() {
  : > "$body"
  curl -sS -o "$body" -w '%{http_code}' --max-time 8 "$@" 2>/dev/null || true
}

api_get() {
  http_code -b "$cookie" "${RUNVARD_URL}/api/$1"
}

api_post() {
  local route="$1"
  shift
  http_code -b "$cookie" -c "$cookie" -X POST "$@" "${RUNVARD_URL}/api/${route}"
}

expect_code() {
  local got="$1"
  local want="$2"
  local label="$3"
  if [ "$got" = "$want" ]; then
    pass "$label returned HTTP $want"
  else
    fail "$label returned HTTP $got, expected $want"
    sed -n '1,5p' "$body" | sed 's/^/      /'
  fi
}

contains_body() {
  local needle="$1"
  local label="$2"
  if grep -Fq "$needle" "$body"; then
    pass "$label"
  else
    fail "$label"
    sed -n '1,5p' "$body" | sed 's/^/      /'
  fi
}

expect_json() {
  local label="$1"
  if python3 -m json.tool "$body" >/dev/null 2>&1; then
    pass "$label returned valid JSON"
  else
    fail "$label did not return valid JSON"
    sed -n '1,5p' "$body" | sed 's/^/      /'
  fi
}

json_has_key() {
  local key="$1"
  python3 - "$body" "$key" <<'PY' >/dev/null 2>&1
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

cur = data
for part in sys.argv[2].split("."):
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        raise SystemExit(1)
PY
}

json_key_equals() {
  local key="$1"
  local want="$2"
  python3 - "$body" "$key" "$want" <<'PY' >/dev/null 2>&1
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

cur = data
for part in sys.argv[2].split("."):
    if isinstance(cur, dict) and part in cur:
        cur = cur[part]
    else:
        raise SystemExit(1)

if str(cur) != sys.argv[3]:
    raise SystemExit(1)
PY
}

expect_json_key() {
  local key="$1"
  local label="$2"
  if json_has_key "$key"; then
    pass "$label exposes JSON key ${key}"
  else
    fail "$label missing JSON key ${key}"
    sed -n '1,5p' "$body" | sed 's/^/      /'
  fi
}

expect_invalid_mutation() {
  local route="$1"
  local action="$2"
  local target="$3"
  local label="$4"
  shift 4

  code="$(api_post confirm-token -d "action=${action}" -d "target=${target}")"
  expect_code "$code" "200" "${label} token issue"
  expect_json "${label} token issue"
  token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token",""))' "$body" 2>/dev/null || true)"
  if [ -n "$token" ]; then
    code="$(api_post "$route" "$@" -d "confirm_token=${token}")"
    expect_code "$code" "400" "$label"
    expect_json "$label"
  else
    fail "${label} token could not be parsed"
  fi
}

load_env_file() {
  local env_file="/opt/runvard/data/runvard.env"
  if [ -r "$env_file" ]; then
    # shellcheck disable=SC1090
    . "$env_file"
    RUNVARD_USER="${RUNVARD_USER:-${ADMIN_USER:-admin}}"
    RUNVARD_PASS="${RUNVARD_PASS:-${ADMIN_PASS:-runvard}}"
    if [ -z "$RUNVARD_URL" ]; then
      RUNVARD_URL="http://127.0.0.1:${RUNVARD_PORT:-8080}"
    fi
    pass "loaded /opt/runvard/data/runvard.env"
  else
    warn "cannot read /opt/runvard/data/runvard.env; using provided env/defaults"
  fi
}

if [ "$RUNVARD_API_ONLY" = "1" ]; then
  log "Environment"
  pass "API-only verifier mode enabled; systemd and host integration checks will be skipped"
else
  log "Environment"
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}" in
      debian|ubuntu) pass "target OS is ${PRETTY_NAME:-$ID}" ;;
      *) warn "target OS is ${PRETTY_NAME:-unknown}; Debian/Ubuntu expected" ;;
    esac
  else
    warn "/etc/os-release not readable"
  fi

  load_env_file
fi

RUNVARD_URL="${RUNVARD_URL:-http://127.0.0.1:8080}"
RUNVARD_USER="${RUNVARD_USER:-admin}"
if [ -z "$RUNVARD_PASS" ]; then
  warn "RUNVARD_PASS is empty; authenticated API checks will be skipped"
fi
printf 'target url: %s\n' "$RUNVARD_URL"

log "Required Tools"
if [ "$RUNVARD_API_ONLY" = "1" ]; then
  required_tools="mktemp rm curl grep sed python3"
else
  required_tools="mktemp rm curl grep sed systemctl journalctl python3"
fi
for cmd in $required_tools; do
  need_cmd "$cmd"
done
if [ "$fail_count" -gt 0 ]; then
  finish
fi

if [ "$RUNVARD_API_ONLY" = "1" ]; then
  log "Service"
  pass "service checks skipped in API-only mode"
else
  for cmd in docker virsh lsblk findmnt; do
    if command -v "$cmd" >/dev/null 2>&1; then
      pass "optional host tool available: $cmd"
    else
      warn "optional host tool missing: $cmd"
    fi
  done

  log "Service"
  if systemctl is-active --quiet runvard; then
    pass "runvard service is active"
  else
    fail "runvard service is not active"
    systemctl --no-pager --full status runvard 2>/dev/null | sed -n '1,20p' | sed 's/^/      /' || true
  fi

  if journalctl -u runvard -n 120 --no-pager 2>/dev/null | grep -Eq 'Traceback|Exception in ASGI'; then
    fail "recent runvard journal contains Python errors"
  else
    pass "recent runvard journal has no obvious Python traceback"
  fi
fi

log "HTTP And Auth"
code="$(http_code "${RUNVARD_URL}/login")"
case "$code" in
  200|302|307) pass "login page responds with HTTP $code" ;;
  000)
    fail "login page returned HTTP 000; ${RUNVARD_URL} is not reachable"
    finish
    ;;
  *) fail "login page returned HTTP $code" ;;
esac

code="$(http_code "${RUNVARD_URL}/api/system/info")"
expect_code "$code" "401" "unauthenticated API request"
expect_json "unauthenticated API request"
contains_body '"ok":false' "unauthenticated error is JSON"

if [ -n "$RUNVARD_PASS" ]; then
  code="$(api_post login --data-urlencode "username=${RUNVARD_USER}" --data-urlencode "password=${RUNVARD_PASS}" -d "remember=0")"
  expect_code "$code" "200" "login API"
  expect_json "login API"
  contains_body '"ok":true' "login API reports success"

  code="$(api_get auth/status)"
  expect_code "$code" "200" "auth status API"
  expect_json "auth status API"
  if json_key_equals role admin; then
    pass "verifier account has admin role"
  else
    fail "verifier account must have admin role"
    finish
  fi

  code="$(api_get config)"
  expect_code "$code" "200" "config API"
  expect_json "config API"
  contains_body '"data_dir"' "config API exposes runtime data directory"

  code="$(api_get system/info)"
  expect_code "$code" "200" "system info API"
  expect_json "system info API"

  log "Confirmation Token Contract"
  code="$(api_post storage/format -d "partition=/dev/sdz1" -d "fstype=ext4")"
  expect_code "$code" "403" "storage format without confirmation token"
  expect_json "storage format without confirmation token"
  contains_body 'confirmation token' "missing confirmation token is rejected before host command"

  code="$(api_post confirm-token -d "action=power:reboot" -d "target=reboot")"
  expect_code "$code" "200" "confirmation token issue"
  expect_json "confirmation token issue"
  token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token",""))' "$body" 2>/dev/null || true)"
  if [ -n "$token" ]; then
    pass "confirmation token parsed"
    code="$(api_post sysmgr/power -d "action=shutdown" -d "delay=0" -d "confirm_token=${token}")"
    expect_code "$code" "403" "mismatched confirmation token"
    expect_json "mismatched confirmation token"
  else
    fail "confirmation token could not be parsed"
  fi

  log "Read-Only Discovery APIs"
  for route in \
    system/stats system/info system/disks system/temps \
    docker/available docker/compose \
    backup/jobs dashboard apps/catalog sysmgr/updates sysmgr/cron monitoring/alerts \
    storage/devices storage/swap storage/raid storage/luks \
    storage/zfs storage/btrfs storage/iscsi network/interfaces network/firewall \
    shares/samba shares/nfs shares/ftp security/users security/groups \
    vms/available vms/networks vms/pools; do
    code="$(api_get "$route")"
    case "$code" in
      200)
        pass "GET /api/${route}"
        expect_json "GET /api/${route}"
        ;;
      5*) fail "GET /api/${route} returned HTTP $code" ;;
      *) warn "GET /api/${route} returned HTTP $code" ;;
    esac
  done

  log "Structured Discovery Contracts"
  for spec in \
    "docker/containers:containers" \
    "docker/images:images" \
    "docker/volumes:volumes" \
    "system/processes:processes" \
    "system/disk-io:disk_io" \
    "system/net-detail:interfaces" \
    "services/list:services" \
    "storage/lvm:pvs" \
    "storage/lvm:vgs" \
    "storage/lvm:lvs" \
    "network/interfaces:interfaces" \
    "security/smb-users:users" \
    "security/certs:certificates" \
    "vms/list:vms" \
    "vms/networks:networks" \
    "vms/pools:pools"; do
    route="${spec%%:*}"
    key="${spec#*:}"
    code="$(api_get "$route")"
    case "$code" in
      200)
        pass "GET /api/${route}"
        expect_json "GET /api/${route}"
        expect_json_key "$key" "GET /api/${route}"
        ;;
      5*) fail "GET /api/${route} returned HTTP $code" ;;
      *) warn "GET /api/${route} returned HTTP $code" ;;
    esac
  done

  log "Parameterized Read API Validation"
  code="$(api_get "services/logs?name=../bad")"
  expect_code "$code" "400" "service logs invalid unit"
  expect_json "service logs invalid unit"

  code="$(api_get "services/logs?name=${RUNVARD_TEST_SERVICE}&lines=999999")"
  expect_code "$code" "400" "service logs invalid line count"
  expect_json "service logs invalid line count"

  code="$(api_get "files/download?path=/proc/cpuinfo")"
  expect_code "$code" "403" "file download rejects blocked path"
  expect_json "file download rejects blocked path"

  code="$(api_get "files/preview?path=/")"
  expect_code "$code" "400" "file preview rejects directory path"
  expect_json "file preview rejects directory path"

  log "Mutating API Input Validation"
  code="$(api_post confirm-token -d "action=backup-add" -d "target=badbackup")"
  expect_code "$code" "200" "backup validation token issue"
  expect_json "backup validation token issue"
  token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token",""))' "$body" 2>/dev/null || true)"
  if [ -n "$token" ]; then
    code="$(api_post backup/add -d "name=badbackup" -d "source=-e:evil" -d "dest=/tmp" -d "schedule=manual" -d "confirm_token=${token}")"
    expect_code "$code" "400" "backup rejects unsafe rsync source"
    expect_json "backup rejects unsafe rsync source"
  else
    fail "backup validation token could not be parsed"
  fi

  code="$(api_post confirm-token -d "action=share-nfs-add" -d "target=/tmp")"
  expect_code "$code" "200" "NFS validation token issue"
  expect_json "NFS validation token issue"
  token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token",""))' "$body" 2>/dev/null || true)"
  if [ -n "$token" ]; then
    code="$(api_post shares/nfs/add -d "path=/tmp" -d "clients=*" --data-urlencode "options=rw)
/ *(rw" -d "confirm_token=${token}")"
    expect_code "$code" "400" "NFS rejects unsafe export options"
    expect_json "NFS rejects unsafe export options"
  else
    fail "NFS validation token could not be parsed"
  fi

  code="$(api_post confirm-token -d "action=files-upload" -d "target=/tmp")"
  expect_code "$code" "200" "file upload validation token issue"
  expect_json "file upload validation token issue"
  token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token",""))' "$body" 2>/dev/null || true)"
  if [ -n "$token" ]; then
    code="$(api_post files/upload -F "path=/tmp" -F "confirm_token=${token}" -F "file=@${invalid_upload};filename=..")"
    expect_code "$code" "400" "file upload rejects invalid filename"
    expect_json "file upload rejects invalid filename"
  else
    fail "file upload validation token could not be parsed"
  fi

  code="$(api_post confirm-token -d "action=files-share-link" -d "target=/tmp")"
  expect_code "$code" "200" "file share-link directory validation token issue"
  expect_json "file share-link directory validation token issue"
  token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token",""))' "$body" 2>/dev/null || true)"
  if [ -n "$token" ]; then
    code="$(api_post files/share -d "path=/tmp" -d "confirm_token=${token}")"
    expect_code "$code" "400" "file share-link rejects directory path"
    expect_json "file share-link rejects directory path"
  else
    fail "file share-link directory validation token could not be parsed"
  fi

  code="$(api_post confirm-token -d "action=files-job:copy" -d "target=/tmp/a||/tmp/b")"
  expect_code "$code" "200" "file job path-list validation token issue"
  expect_json "file job path-list validation token issue"
  token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token",""))' "$body" 2>/dev/null || true)"
  if [ -n "$token" ]; then
    code="$(api_post files/job -d "action=copy" -d "paths=/tmp/a||/tmp/b" -d "dst_dir=/tmp" -d "confirm_token=${token}")"
    expect_code "$code" "400" "file job rejects empty path-list entries"
    expect_json "file job rejects empty path-list entries"
  else
    fail "file job path-list validation token could not be parsed"
  fi

  expect_invalid_mutation \
    accounts/add account-add verifier_bad_role \
    "account creation rejects invalid role" \
    -d "username=verifier_bad_role" -d "password=secret" -d "role=owner"

  expect_invalid_mutation \
    sysmgr/packages/install sysmgr-package-install -bad \
    "package install rejects invalid package name" \
    -d "name=-bad"

  expect_invalid_mutation \
    sysmgr/packages/remove sysmgr-package-remove bad/pkg \
    "package remove rejects invalid package name" \
    -d "name=bad/pkg"

  expect_invalid_mutation \
    storage/format storage-format /dev/sdz9 \
    "storage format rejects invalid filesystem type" \
    -d "partition=/dev/sdz9" -d "fstype=ntfs"

  expect_invalid_mutation \
    vms/pool/vol-create vm-volume-create default/verifier-bad \
    "VM volume creation rejects invalid disk format" \
    -d "pool=default" -d "name=verifier-bad" -d "size_gb=10" -d "format=vmdk"

  expect_invalid_mutation \
    files/job files-job:copy /tmp/runvard-missing \
    "file job rejects invalid action" \
    -d "action=sync" -d "paths=/tmp/runvard-missing" -d "dst_dir=/tmp"

  expect_invalid_mutation \
    sysmgr/apparmor/set sysmgr-apparmor-set /usr/sbin/nginx \
    "AppArmor rejects invalid mode" \
    -d "profile=/usr/sbin/nginx" -d "mode=audit"

  expect_invalid_mutation \
    sysmgr/kdump/action sysmgr-kdump-action:enable kdump \
    "kdump rejects invalid action" \
    -d "action=reload"
fi

log "Host Integrations"
if [ "$RUNVARD_API_ONLY" = "1" ]; then
  pass "host integration checks skipped in API-only mode"
else
  if command -v docker >/dev/null 2>&1; then
    if docker info >/dev/null 2>&1; then
      pass "Docker daemon is reachable"
    else
      warn "Docker command exists but daemon is not reachable"
    fi
    if docker compose version >/dev/null 2>&1; then
      pass "Docker Compose v2 is usable"
    else
      warn "Docker Compose v2 is not usable"
    fi
  fi

  if command -v virsh >/dev/null 2>&1; then
    if virsh -c qemu:///system list --all >/dev/null 2>&1; then
      pass "libvirt qemu:///system is reachable"
    else
      warn "virsh exists but qemu:///system is not reachable"
    fi
  fi
fi

if [ "$RUNVARD_API_ONLY" = "1" ]; then
  log "Destructive Checks"
  pass "destructive checks skipped in API-only mode"
elif [ "$RUNVARD_DESTRUCTIVE" = "1" ]; then
  log "Destructive Checks"
  warn "destructive checks are enabled; run this only on disposable target hosts"
  if [ -n "$RUNVARD_PASS" ]; then
    code="$(api_post confirm-token -d "action=service-action:restart" -d "target=${RUNVARD_TEST_SERVICE}")"
    expect_code "$code" "200" "service restart token issue"
    expect_json "service restart token issue"
    token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("token",""))' "$body" 2>/dev/null || true)"
    if [ -n "$token" ]; then
      code="$(api_post services/action -d "name=${RUNVARD_TEST_SERVICE}" -d "action=restart" -d "confirm_token=${token}")"
      case "$code" in
        200) pass "service restart API executed for ${RUNVARD_TEST_SERVICE}" ;;
        *) fail "service restart API returned HTTP $code for ${RUNVARD_TEST_SERVICE}" ;;
      esac
    fi
  else
    warn "destructive API checks skipped because RUNVARD_PASS is empty"
  fi
else
  log "Destructive Checks"
  pass "destructive checks skipped; set RUNVARD_DESTRUCTIVE=1 on a disposable VM"
fi

finish
