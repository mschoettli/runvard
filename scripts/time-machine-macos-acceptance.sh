#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --share smb://host/share [--apply-destination] [--verify]"
  echo "Without --apply-destination this script performs read-only checks."
}

share=""
apply_destination=0
verify=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --share) share="${2:-}"; shift 2 ;;
    --apply-destination) apply_destination=1; shift ;;
    --verify) verify=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This acceptance tool must run on macOS." >&2
  exit 2
fi
if [[ -z "$share" || "$share" != smb://* ]]; then
  echo "--share must be an smb:// URL without an embedded password." >&2
  exit 2
fi
if [[ "$share" == *"@"* ]]; then
  echo "Do not place credentials in the share URL." >&2
  exit 2
fi

command -v tmutil >/dev/null
command -v smbutil >/dev/null
echo "Checking SMB reachability for $share"
smbutil view "$share"
echo "Current Time Machine destinations:"
tmutil destinationinfo || true

if [[ "$apply_destination" -eq 1 ]]; then
  echo "Applying the requested Time Machine destination. macOS may request credentials."
  tmutil setdestination "$share"
else
  echo "No Time Machine destination was changed. Re-run with --apply-destination after reviewing the target."
fi

if [[ "$verify" -eq 1 ]]; then
  echo "Starting Apple's network-backup verification. This can take a long time."
  tmutil verifybackups
else
  echo "Verification was not started. Re-run with --verify after one complete backup."
fi

echo "Manual release gates remain: restore files in Time Machine and perform a Migration Assistant restore on test hardware."
