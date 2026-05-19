#!/bin/bash
# Send a test text to the local API and print one
# (surface|furigana|accent_marking_type) line per moji.
#
# Usage:
#   ./test.sh                          # default text on MarkAccent
#   ./test.sh "三月五日（土）"          # custom text
#   PORT=8000 ./test.sh                # different port
#   ENDPOINT=MarkFurigana ./test.sh    # furigana endpoint (accent column = "-")

set -euo pipefail

TEXT="${1:-3月5日(土)}"
PORT="${PORT:-8000}"
ENDPOINT="${ENDPOINT:-MarkAccent}"
URL="http://127.0.0.1:${PORT}/api/${ENDPOINT}/"

PAYLOAD=$(uv run python -c \
  'import json, sys; print(json.dumps({"text": sys.argv[1]}))' "$TEXT")

# Fail fast with a readable message if the server isn't reachable.
HTTP_STATUS=$(curl -s -o /tmp/test_sh_body.$$ -w '%{http_code}' \
  -X POST "$URL" -H 'Content-Type: application/json' --data-raw "$PAYLOAD" \
  || true)
if [[ "$HTTP_STATUS" != "200" || ! -s /tmp/test_sh_body.$$ ]]; then
  echo "Request to $URL failed (HTTP ${HTTP_STATUS:-no-response})." >&2
  echo "Is the server running? Try: uv run uvicorn main:app --host 127.0.0.1 --port ${PORT}" >&2
  rm -f /tmp/test_sh_body.$$
  exit 1
fi

read -r -d '' FORMAT_SCRIPT <<'PY' || true
import json, sys

data = json.load(sys.stdin)
if data.get("status") != 200 or not data.get("result"):
    print("ERROR:", data.get("error") or data)
    sys.exit(1)

for w in data["result"]:
    surface = w["surface"]
    accents = w.get("accent") or []
    if not accents:
        print(f"({surface}|{w['furigana']}|-)")
        continue
    for a in accents:
        print(f"({surface}|{a['furigana']}|{a['accent_marking_type']})")
PY

uv run python -c "$FORMAT_SCRIPT" < /tmp/test_sh_body.$$
rm -f /tmp/test_sh_body.$$
