#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: RUNVARD_TM_SMB_PASSWORD=... $0 --share //host/share --user account [--cross-user account]"
  echo "Optional cross-user password: RUNVARD_TM_CROSS_PASSWORD"
  echo "The check is read-only and never uploads or deletes backup data."
}

share=""
user=""
cross_user=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --share) share="${2:-}"; shift 2 ;;
    --user) user="${2:-}"; shift 2 ;;
    --cross-user) cross_user="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This acceptance tool must run on the Linux runvard host." >&2
  exit 2
fi
if [[ ! "$share" =~ ^//[^/]+/[A-Za-z0-9][A-Za-z0-9._-]{0,79}$ ]]; then
  echo "--share must use //host/share without credentials." >&2
  exit 2
fi
if [[ ! "$user" =~ ^[a-z_][a-z0-9_-]{0,30}\$?$ ]]; then
  echo "--user is invalid." >&2
  exit 2
fi
if [[ -z "${RUNVARD_TM_SMB_PASSWORD:-}" ]]; then
  echo "RUNVARD_TM_SMB_PASSWORD is required." >&2
  exit 2
fi
for command in testparm smbclient avahi-browse systemctl; do
  command -v "$command" >/dev/null || {
    echo "Required command is missing: $command" >&2
    exit 1
  }
done

auth_file="$(mktemp)"
cross_auth_file=""
cleanup() {
  if [[ -n "$auth_file" ]]; then rm -f -- "$auth_file"; fi
  if [[ -n "$cross_auth_file" ]]; then rm -f -- "$cross_auth_file"; fi
}
trap cleanup EXIT
chmod 0600 "$auth_file"
printf 'username = %s\npassword = %s\n' "$user" "$RUNVARD_TM_SMB_PASSWORD" >"$auth_file"

share_name="${share##*/}"
config="$(testparm -s --suppress-prompt 2>/dev/null)"
section="$(printf '%s\n' "$config" | awk -v wanted="[$share_name]" '
  $0 == wanted { active=1; next }
  /^\[/ { active=0 }
  active { print }
')"
grep -Eq 'fruit:time machine[[:space:]]*=[[:space:]]*yes' <<<"$section"
grep -Eq 'server smb encrypt[[:space:]]*=[[:space:]]*required' <<<"$section"
grep -Eq 'hosts allow[[:space:]]*=' <<<"$section"

systemctl is-active --quiet smbd
systemctl is-active --quiet avahi-daemon
systemctl is-active --quiet runvard-time-machine-maintenance.timer

smbclient "$share" -A "$auth_file" --client-protection=encrypt -c 'ls' >/dev/null
avahi-browse -rt _adisk._tcp | grep -F -- "$share_name" >/dev/null

if [[ -n "$cross_user" ]]; then
  if [[ ! "$cross_user" =~ ^[a-z_][a-z0-9_-]{0,30}\$?$ ]] \
      || [[ -z "${RUNVARD_TM_CROSS_PASSWORD:-}" ]]; then
    echo "A valid --cross-user and RUNVARD_TM_CROSS_PASSWORD are required together." >&2
    exit 2
  fi
  cross_auth_file="$(mktemp)"
  chmod 0600 "$cross_auth_file"
  printf 'username = %s\npassword = %s\n' \
    "$cross_user" "$RUNVARD_TM_CROSS_PASSWORD" >"$cross_auth_file"
  if smbclient "$share" -A "$cross_auth_file" \
      --client-protection=encrypt -c 'ls' >/dev/null 2>&1; then
    echo "Cross-user denial failed: $cross_user accessed $share_name" >&2
    exit 1
  fi
fi

echo "PASS: Samba config, encrypted owner access, Bonjour and maintenance timer"
if [[ -n "$cross_user" ]]; then
  echo "PASS: cross-user access denied"
fi
