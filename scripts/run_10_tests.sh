#!/bin/bash
# Drive 10 of the 30 sample files through /api/MarkAccent/ and persist
# both raw JSON and the (surface|furigana|accent_marking_type) view per file.
#
# Outputs land in output/test_<id>.{json,txt} alongside summary.txt.

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${ROOT}/data"
OUT_DIR="${ROOT}/output"
PORT="${PORT:-8000}"
ENDPOINT="${ENDPOINT:-MarkAccent}"
URL="http://127.0.0.1:${PORT}/api/${ENDPOINT}/"

# All 30 fixtures (overridable via TESTS env var, e.g. TESTS="0 5 10").
read -r -a TESTS <<< "${TESTS:-0 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29}"

mkdir -p "${OUT_DIR}"
SUMMARY="${OUT_DIR}/summary.txt"
: > "${SUMMARY}"

echo "Endpoint: ${URL}" | tee -a "${SUMMARY}"
echo "Tests:    ${TESTS[*]}" | tee -a "${SUMMARY}"
echo | tee -a "${SUMMARY}"

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

for id in "${TESTS[@]}"; do
  INPUT="${DATA_DIR}/test_${id}.txt"
  JSON_OUT="${OUT_DIR}/test_${id}.json"
  TXT_OUT="${OUT_DIR}/test_${id}.txt"

  if [[ ! -f "${INPUT}" ]]; then
    echo "test_${id}: MISSING ${INPUT}" | tee -a "${SUMMARY}"
    continue
  fi

  TEXT="$(cat "${INPUT}")"
  CHARS=$(printf '%s' "${TEXT}" | wc -m | tr -d ' ')

  PAYLOAD=$(uv run python -c \
    'import json, sys; print(json.dumps({"text": sys.argv[1]}))' "${TEXT}")

  START_NS=$(date +%s%N)
  HTTP_STATUS=$(curl -s -o "${JSON_OUT}" -w '%{http_code}' \
    -X POST "${URL}" -H 'Content-Type: application/json' \
    --data-raw "${PAYLOAD}" || echo "000")
  END_NS=$(date +%s%N)
  ELAPSED_MS=$(( (END_NS - START_NS) / 1000000 ))

  if [[ "${HTTP_STATUS}" != "200" || ! -s "${JSON_OUT}" ]]; then
    LINE="test_${id}: HTTP ${HTTP_STATUS}  chars=${CHARS}  time=${ELAPSED_MS}ms  FAILED"
    echo "${LINE}" | tee -a "${SUMMARY}"
    continue
  fi

  if uv run python -c "${FORMAT_SCRIPT}" < "${JSON_OUT}" > "${TXT_OUT}"; then
    LINES=$(wc -l < "${TXT_OUT}" | tr -d ' ')
    LINE="test_${id}: HTTP 200      chars=${CHARS}  time=${ELAPSED_MS}ms  rows=${LINES}  OK"
  else
    LINES=$(wc -l < "${TXT_OUT}" | tr -d ' ')
    LINE="test_${id}: HTTP 200      chars=${CHARS}  time=${ELAPSED_MS}ms  rows=${LINES}  FORMAT_ERR"
  fi
  echo "${LINE}" | tee -a "${SUMMARY}"
done

echo | tee -a "${SUMMARY}"
echo "Done. Artifacts in ${OUT_DIR}/" | tee -a "${SUMMARY}"
