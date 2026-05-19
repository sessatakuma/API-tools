#!/bin/bash
# Send a test text to the local API and print one
# (surface|furigana|accent_marking_type) line per moji.
#
# Usage:
#   ./test.sh                                  # default text on MarkAccent
#   ./test.sh "三月五日（土）"                  # custom text
#   PORT=8000 ./test.sh                        # different port
#   ENDPOINT=MarkFurigana ./test.sh            # furigana endpoint (accent="-")
#   STREAM=1 ./test.sh $'first\nsecond'        # streaming endpoint, NDJSON

set -euo pipefail

TEXT="${1:-3月5日(土)}"
PORT="${PORT:-8000}"
ENDPOINT="${ENDPOINT:-MarkAccent}"
STREAM="${STREAM:-0}"

if [[ "$STREAM" == "1" ]]; then
  URL="http://127.0.0.1:${PORT}/api/${ENDPOINT}/stream/"
else
  URL="http://127.0.0.1:${PORT}/api/${ENDPOINT}/"
fi

PAYLOAD=$(uv run python -c \
  'import json, sys; print(json.dumps({"text": sys.argv[1]}))' "$TEXT")

if [[ "$STREAM" == "1" ]]; then
  # Streaming mode: pipe NDJSON straight to a per-line viewer.
  read -r -d '' STREAM_VIEWER <<'PY' || true
import sys, json

seen = 0
for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    seen += 1
    d = json.loads(raw)
    chunk = d["chunk"]
    status = d["status"]
    err = d.get("error")
    result = d.get("result") or []
    print(f"--- chunk {chunk}  status={status}  words={len(result)} ---")
    if err:
        print(f"  ERROR: {err}")
        continue
    for w in result:
        surface = w["surface"]
        accents = w.get("accent") or []
        if not accents:
            print(f"  ({surface}|{w['furigana']}|-)")
            continue
        for a in accents:
            moji = a["furigana"]
            t = a["accent_marking_type"]
            print(f"  ({surface}|{moji}|{t})")
if seen == 0:
    print("(empty stream — no non-blank input lines)")
PY

  # -N disables curl's output buffering so each NDJSON line lands in the
  # viewer as soon as the server flushes it.
  curl -sN -X POST "$URL" \
    -H 'Content-Type: application/json' \
    --data-raw "$PAYLOAD" \
  | uv run python -c "$STREAM_VIEWER"
  exit 0
fi

# Non-streaming mode (original behaviour).
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
