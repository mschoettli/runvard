#!/usr/bin/env sh
set -eu

SCRIPT_DIR="${0%/*}"
if [ "$SCRIPT_DIR" = "$0" ]; then
  SCRIPT_DIR="."
fi
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT"

preflight_missing=0
for tool in mktemp rm; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "missing command before verifier setup: $tool" >&2
    preflight_missing=$((preflight_missing + 1))
  fi
done
if [ "$preflight_missing" -gt 0 ]; then
  exit 1
fi

VERIFY_TMP="$(mktemp -d "${TMPDIR:-/tmp}/runvard-verify.XXXXXX")"
trap 'rm -rf "$VERIFY_TMP"' EXIT HUP INT TERM
export PYTHONPYCACHEPREFIX="$VERIFY_TMP/pycache"

echo "== required tools =="
missing_tools=0
for tool in mktemp rm python3 bash git node; do
  if command -v "$tool" >/dev/null 2>&1; then
    :
  else
    echo "missing command: $tool" >&2
    missing_tools=$((missing_tools + 1))
  fi
done
if [ "$missing_tools" -gt 0 ]; then
  exit 1
fi

echo "== dependency-light tests =="
python3 -m unittest discover -s tests

if [ -x ".venv/bin/python" ]; then
  echo "== full runtime tests (.venv) =="
  .venv/bin/python -m unittest discover -s tests
  PY=".venv/bin/python"
else
  echo "== full runtime tests (.venv) skipped: .venv/bin/python not found =="
  PY="python3"
fi

echo "== compile =="
"$PY" -m compileall server.py modules tests

echo "== generated python artifacts =="
python3 - <<'PY'
from pathlib import Path
import sys

ignored_roots = {".git", ".venv", "venv", "wheels"}
offenders = []
for path in Path(".").rglob("*"):
    parts = set(path.parts)
    if parts & ignored_roots:
        continue
    if path.is_dir() and path.name == "__pycache__":
        offenders.append(path.as_posix())
    elif path.is_file() and path.suffix in {".pyc", ".pyo"}:
        offenders.append(path.as_posix())

if offenders:
    print("\n".join(sorted(offenders)), file=sys.stderr)
    raise SystemExit(1)
PY

echo "== required artifacts =="
for path in README.md INSTALLATION.md DEVELOPMENT.md requirements.txt runvard.service modules/runtime.py static/btop.html tests/test_static_contracts.py tests/test_app_runtime.py scripts/verify-local.sh scripts/verify-target-host.sh scripts/verify-api-only.sh; do
  test -s "$path"
done

echo "== shell syntax =="
for script in install.sh uninstall.sh update.sh scripts/install-full.sh scripts/verify-local.sh scripts/verify-target-host.sh scripts/verify-api-only.sh; do
  bash -n "$script"
done

echo "== markdown links =="
python3 - <<'PY'
from pathlib import Path
import re
import sys

root = Path(".")
missing = []
for path in sorted(root.glob("*.md")):
    text = path.read_text(encoding="utf-8")
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = match.group(1).split("#", 1)[0]
        if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            continue
        if not (path.parent / target).exists():
            missing.append(f"{path}:{match.start(1)} -> {match.group(1)}")

if missing:
    print("\n".join(missing), file=sys.stderr)
    raise SystemExit(1)
PY

echo "== frontend script parse =="
node -e "const fs=require('fs'); const html=fs.readFileSync('static/index.html','utf8'); let i=0; for (const m of html.matchAll(/<script[^>]*>([\\s\\S]*?)<\\/script>/g)) { new Function(m[1]); i++; } console.log('checked script blocks', i);"

echo "== diff whitespace check =="
git diff --check

echo "OK"
