#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTEST=".venv/bin/pytest"
if [ ! -x "$PYTEST" ]; then
  PYTEST="pytest"
fi

RUNVARD_FEDERATION_NO_WORKER=1 "$PYTEST" -q \
  tests/test_federation_crypto.py \
  tests/test_federation_membership.py \
  tests/test_federation_protocol.py \
  tests/test_federation_pairing.py \
  tests/test_federation_status.py \
  tests/test_federation_sso.py \
  tests/test_federation_api.py \
  tests/test_federation_mesh.py

node -e '
const fs = require("fs");
const html = fs.readFileSync("static/index.html", "utf8");
for (const match of html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/g)) {
  if (match[1].trim()) new Function(match[1]);
}
'

echo "Federation verification passed."
