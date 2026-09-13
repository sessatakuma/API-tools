#!/usr/bin/env bash
# Download and install a UniDic dictionary for fugashi.
#
# Usage:
#   ./scripts/download_unidic.sh                # default: cwj-2025-12-31
#   ./scripts/download_unidic.sh cwj-2025-12-31 # written-language (CWJ), latest
#   ./scripts/download_unidic.sh csj-2025-12-31 # spoken-language (CSJ), latest
#   ./scripts/download_unidic.sh cwj-2021-08-31 # older CWJ 3.1.0
#
# NINJAL publishes two dictionary variants:
#   CWJ (現代書き言葉) — trained on written corpora (BCCWJ).
#     Best for analysing articles, novels, web text, etc.
#   CSJ (現代話し言葉) — trained on spoken transcripts (CEJC).
#     Best for conversational / transcribed speech.
#
# The default is CWJ since this service primarily processes written input.
# Pass a csj-* tag to switch to the spoken-language variant.

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────

# Mapping: tag → (NINJAL archive path, version marker, project-verified SHA-256).
#
# These digests were computed from archives downloaded over HTTPS on 2026-08-04,
# then validated with `unzip -t` and by checking that each archive contains
# `sys.dic`. NINJAL does not publish checksums, so this is a project trust-on-
# first-use pin: it detects later replacement or corruption of a fixed archive.
declare -A DICT_URLS
DICT_URLS[cwj-2025-12-31]="unidic_archive/2512/unidic-cwj-202512.zip|cwj-3.1.0+2025-12-31|d94216b589d15d05c408ed59abc5259086703ebbac14e225b5314e4cd106c4db"
DICT_URLS[csj-2025-12-31]="unidic_archive/2512/unidic-csj-202512.zip|csj-3.1.0+2025-12-31|e02f9d39bef87b2d8e80d6f09993139e6ee8662461fc7a518ba4ede28fdbc0c5"
DICT_URLS[cwj-2021-08-31]="unidic_archive/cwj/3.1.0/unidic-cwj-3.1.0.zip|cwj-3.1.0+2021-08-31|340651aa78cc02caf690f94de8809d41c4acda694226517884ecad81c51523fc"

# Backward compat: bare date tags default to CWJ.
DICT_URLS[2025-12-31]=${DICT_URLS[cwj-2025-12-31]}
DICT_URLS[2021-08-31]=${DICT_URLS[cwj-2021-08-31]}

NINJAL_BASE="https://clrd.ninjal.ac.jp"
DEFAULT_VERSION="cwj-2025-12-31"

# ── Args ──────────────────────────────────────────────────────────────

VERSION_TAG="${1:-$DEFAULT_VERSION}"

if [[ ! -v "DICT_URLS[$VERSION_TAG]" ]]; then
    echo "ERROR: Unknown UniDic version '$VERSION_TAG'" >&2
    echo "Available versions:" >&2
    for key in "${!DICT_URLS[@]}"; do echo "  $key" >&2; done
    exit 1
fi

IFS="|" read -r DICT_PATH DICT_VERSION DICT_SHA256 <<< "${DICT_URLS[$VERSION_TAG]}"
DICT_URL="${NINJAL_BASE}/${DICT_PATH}"

# ── Locate dicdir ────────────────────────────────────────────────────

# Prefer an active venv; fall back to `uv run` to find one.
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    PYTHON="${VIRTUAL_ENV}/bin/python"
else
    PYTHON="uv run python"
fi

DICDIR=$($PYTHON -c "import unidic; print(unidic.DICDIR)" 2>/dev/null) || {
    echo "ERROR: Cannot locate unidic dicdir. Is the 'unidic' package installed?" >&2
    exit 1
}

echo "UniDic target dicdir: ${DICDIR}"
echo "Requested version:    ${VERSION_TAG} (${DICT_VERSION})"
echo "Download URL:         ${DICT_URL}"

# ── Download ─────────────────────────────────────────────────────────

TMPDIR=$(mktemp -d)
ZIPFILE="${TMPDIR}/unidic.zip"

echo "Downloading (~700 MB compressed download, ~1.3 GB installed)..."
curl -fSL --progress-bar -o "$ZIPFILE" "$DICT_URL"

echo "Verifying SHA-256..."
if ! printf "%s  %s\\n" "$DICT_SHA256" "$ZIPFILE" | sha256sum --check --status; then
    echo "ERROR: UniDic archive checksum mismatch; refusing to extract." >&2
    rm -rf "$TMPDIR"
    exit 1
fi

# ── Extract ───────────────────────────────────────────────────────────

# NINJAL's recent zips use Deflate64 which Python's zipfile cannot
# handle.  Try `unzip` first (most Linux distros), then `7z` as
# fallback (common in Docker slim images via p7zip-full).

EXTRACTED="${TMPDIR}/extract"
mkdir -p "$EXTRACTED"

if command -v unzip &>/dev/null; then
    echo "Extracting with unzip..."
    unzip -q "$ZIPFILE" -d "$EXTRACTED"
elif command -v 7z &>/dev/null; then
    echo "Extracting with 7z..."
    7z x -y -o"$EXTRACTED" "$ZIPFILE" >/dev/null
else
    echo "ERROR: Neither 'unzip' nor '7z' found. Install one to proceed." >&2
    rm -rf "$TMPDIR"
    exit 1
fi

# ── Install ───────────────────────────────────────────────────────────

echo "Installing to ${DICDIR}..."
rm -rf "${DICDIR}"
mkdir -p "${DICDIR}"

# The zip may contain a single top-level directory (e.g. unidic-cwj-202512/)
# or flat files (sys.dic, matrix.bin, …) right at the root.  Handle both.
# Heuristic: the correct root is the one containing sys.dic.
SRC_DIR="$EXTRACTED"
if ! [[ -f "$SRC_DIR/sys.dic" ]]; then
    INNER=$(find "$EXTRACTED" -mindepth 1 -maxdepth 1 -type d | head -1)
    if [[ -n "$INNER" && -f "$INNER/sys.dic" ]]; then
        SRC_DIR="$INNER"
    fi
fi

mv "$SRC_DIR"/* "${DICDIR}/"

# Write version marker and dummy mecabrc (fugashi / unidic loader expects these).
printf "unidic-%s" "$DICT_VERSION" > "${DICDIR}/version"
printf "# This is a dummy file.\n" > "${DICDIR}/mecabrc"

# ── Cleanup ───────────────────────────────────────────────────────────

rm -rf "$TMPDIR"

echo "Done — UniDic ${DICT_VERSION} installed."
$PYTHON -c "import unidic; print('Verified:', unidic.VERSION)"
