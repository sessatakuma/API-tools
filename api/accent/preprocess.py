"""Pre-alignment text rewrites + post-alignment surface restoration.

Three independent rewrites run on the request text before tokenisation
and OpenJTalk accent extraction; each pairs a `_strip_*` pass that mutates
the text
with a `_restore_*` pass that walks the aligned results to put the
original surfaces back.

  * **URLs** (`_strip_urls` / `_restore_urls`) — swap each URL for a
    placeholder so the alignment DP isn't dragged off-rail by Latin runs.
  * **Western-grouped thousands** (`_strip_number_commas` /
    `_restore_number_commas`) — `1,234` → `1234` so OpenJTalk reads the
    whole integer as one phrase.
  * **× between digits** (`_strip_x_between_digits` /
    `_restore_x_between_digits`) — `19×19` → `19と19` so OpenJTalk emits a
    spoken boundary instead of merging the numbers into `1919`.

`has_japanese` contributes to the pipeline's early-exit gate; digits, spoken
symbols, and requested English readings still run through OpenJTalk.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_left

import neologdn

from api.accent.models import AccentInfo, WordAccentResult
from api.accent.surface_rewrites import (
    SurfaceRewrite,
    restore_rewrites,
    rewrite_matches,
)

logger = logging.getLogger("api")


# Hiragana / katakana / CJK Unified Ideographs (incl. Extension A). A
# chunk with no chars in this set is treated as pure English / code /
# markdown / URL — pipeline is skipped entirely and the line is echoed
# back verbatim so document reconstruction still works.
_CJK_RE = re.compile(
    "["
    "぀-ゟ"  # Hiragana
    "゠-ヿ"  # Katakana
    "㐀-䶿"  # CJK Unified Ideographs Extension A
    "一-鿿"  # CJK Unified Ideographs
    "]"
)


def has_japanese(text: str) -> bool:
    """True if `text` contains any hiragana, katakana, or CJK ideograph."""
    return bool(_CJK_RE.search(text))


# URLs are stripped before the pipeline runs. OpenJTalk's phrasing frontend
# produces only noise for Latin punctuation runs, and the local tokeniser
# can fragment a URL across several alphabet/symbol tokens — both drag
# the alignment DP off-rail for the surrounding Japanese. We swap each
# URL for one placeholder (which the tokeniser keeps as a single "alphabet"
# word), run the pipeline, then restore the exact recorded output span.
# URL body stops at whitespace, any Japanese char (so `…はhttps://x.jp/aです`
# strips just the URL, leaving `です` to be processed), or common quoting
# punctuation `,()<>[]"'` (so `(https://x.jp)` strips just the URL).
_URL_RE = re.compile(r"https?://[^\s　-鿿,()<>\[\]\"']+")
_URL_PLACEHOLDER = "「URLPLACEHOLDER」"


def strip_urls(text: str) -> tuple[str, list[SurfaceRewrite]]:
    """Replace URLs and retain their exact output spans for restoration."""
    return rewrite_matches(text, _URL_RE, lambda _match: _URL_PLACEHOLDER)


def restore_urls(
    result: list[WordAccentResult], rewrites: list[SurfaceRewrite]
) -> list[WordAccentResult]:
    """Swap placeholder tokens in `result` back to their original URLs."""
    return restore_rewrites(result, rewrites, lambda _rewrite: "")


# Western-style grouped numbers: `1,234`, `1,234,567`, `12,345.67`. The
# match must start with 1-3 digits and have each group be exactly 3 digits
# so we don't fire on `1,23` or list-separated number runs like
# `1, 2, 3`. After neologdn normalisation we only see ASCII commas.
_NUMERIC_COMMA_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def strip_number_commas(text: str) -> tuple[str, list[SurfaceRewrite]]:
    """Strip commas from western-grouped numbers in `text`.

    Returns the cleaned text and offset-tagged `1,234` → `1234` rewrites. After
    pipeline alignment, `restore_number_commas` swaps the surfaces back.
    fugashi splits `1,234` into three tokens (digit / `,` / digit) and the
    DP can't easily reassemble them; doing the splice at the text level
    lets the merged digit string flow as one numeric token through OpenJTalk,
    which then reads it as a single integer (せんにひゃくさんじゅうよん).
    """
    return rewrite_matches(
        text, _NUMERIC_COMMA_RE, lambda match: match.group(0).replace(",", "")
    )


def restore_number_commas(
    result: list[WordAccentResult], rewrites: list[SurfaceRewrite]
) -> list[WordAccentResult]:
    """Walk `result` in order, restoring `1,234`-style surfaces.

    Rewrites only the recorded output span back to the original comma form. The
    OpenJTalk-derived furigana / accent payload is left untouched — those
    were produced from the cleaned `1234` form and remain correct as the
    spoken reading.
    """
    return restore_rewrites(result, rewrites)


# `\d×\d` (and `\d × \d` with spaces) gets merged by OpenJTalk's phrasing
# module into one number. Swap × → と for the OpenJTalk/fugashi pass so the
# connector anchors both numeric spans; restore `×` with empty ruby afterward.
_X_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d)\s*[×✕✖]\s*(?=\d)")


def strip_x_between_digits(text: str) -> tuple[str, list[SurfaceRewrite]]:
    """Replace `\\d × \\d` with `\\dと\\d` so OpenJTalk splits the reading.

    Returns the cleaned text and exact spans that must be restored to `×`.
    """
    cleaned, rewrites = rewrite_matches(text, _X_BETWEEN_DIGITS_RE, lambda _match: "と")
    return cleaned, [
        SurfaceRewrite(
            start=rewrite.start,
            replacement=rewrite.replacement,
            original=rewrite.original,
        )
        for rewrite in rewrites
    ]


def normalize_and_strip_x_between_digits(
    text: str,
) -> tuple[str, list[SurfaceRewrite]]:
    placeholder = "\ue000"
    while placeholder in text:
        placeholder += "\ue000"

    originals: list[str] = []

    def protect(match: re.Match[str]) -> str:
        originals.append(match.group(0))
        return placeholder

    protected = _X_BETWEEN_DIGITS_RE.sub(protect, text)
    normalized = neologdn.normalize(protected, tilde="normalize")
    placeholder_pattern = re.compile(re.escape(placeholder))
    cleaned, rewrites = rewrite_matches(
        normalized,
        placeholder_pattern,
        lambda _match: "と",
    )
    return cleaned, [
        SurfaceRewrite(
            start=rewrite.start,
            replacement=rewrite.replacement,
            original=original,
        )
        for rewrite, original in zip(rewrites, originals, strict=True)
    ]


def restore_x_between_digits(
    result: list[WordAccentResult], rewrites: list[SurfaceRewrite]
) -> list[WordAccentResult]:
    """Restore only `と` spans that originated from multiplication signs."""
    return restore_rewrites(result, rewrites, lambda _rewrite: "")


# `.` between an ASCII letter and an alphanumeric (or between alphanumeric
# and an ASCII letter) — i.e. `Wifi.7`, `iPhone.7`, `i.e`. OpenJTalk silently
# normalises these `.`s to full-width `。` (`Wifi.7` → echoed as `Wifi。7`)
# and the prosody CRF then collapses pitch accents on everything after
# that point (`といった技術` came back all-zero, the user-reported
# "音調不見了" symptom). We strip the `.` from OpenJTalk's query so the CRF
# sees a clean `Wifi7` and produces a normal contour for the rest of
# the sentence; fugashi still sees the original surface, so the
# tokenizer's acronym-merge step keeps the user-visible `Wifi.7`.
#
# Digit-flanked `.` is deliberately NOT stripped: `2.5` and `192.168.1.1`
# don't trigger OpenJTalk's `.` → `。` normalisation (the CRF stays sane),
# and stripping would change the spoken reading from "two point five"
# to "twenty five".
_ACRONYM_DOT_RE = re.compile(
    r"(?<=[A-Za-z])\.(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])\.(?=[A-Za-z])"
)


def strip_acronym_dots(text: str) -> str:
    """Remove `.` between alpha+alphanumeric pairs in the OpenJTalk query.

    Asymmetric strip (no restore): only the OpenJTalk-side text loses the
    `.`. fugashi continues to see the original so its tokenization +
    the tokenizer's acronym-merge keep the user-visible `Wifi.7`
    surface intact. The aligner's english-compound branch is permissive
    enough to align the merged token against OpenJTalk's `Wifi7` reading
    without further accommodation.
    """
    return _ACRONYM_DOT_RE.sub("", text)


# Sentence terminators that close a Japanese clause: kuten (。), full-width
# question (？), full-width exclamation (！), and full-width period (．).
# ASCII `.!?` are intentionally excluded — they appear in abbreviations,
# decimals, and code/identifier fragments that we don't want to split on.
# A zero-width split (lookbehind) keeps the terminator attached to the
# preceding sentence so accent prediction still sees the clause boundary.
_SENTENCE_SPLIT_RE = re.compile("(?<=[。！？．])")


def split_sentences(line: str) -> list[str]:
    """Split a line into sentence-sized chunks for parallel processing.

    OpenJTalk's phrasing module degrades badly on long inputs (a single
    misaligned mora can cascade across the whole paragraph), and the
    streaming endpoint can't parallelise within a `\\n`-delimited chunk.
    Splitting on full-width sentence terminators fixes both: each sentence
    is short enough for OpenJTalk to handle reliably, and they fan out across
    the four-task sliding window.
    """
    return [s for s in _SENTENCE_SPLIT_RE.split(line) if s.strip()]


# Hard per-chunk length cap. The alignment DP is O(n * m * _K_MAX), quadratic
# in chunk length, so an unbroken chunk (a line/sentence with no `。！？．` to
# split on) can blow up: a worst-case dense-kanji chunk fits
# `time ≈ 1.18e-4 * chars²`, crossing ~5 s at ~205 chars on the reference dev
# machine (200 chars ≈ 4.7 s). We cap each chunk at 200 chars and hard-split
# anything longer so no single chunk exceeds that ~5 s ceiling. This bites only
# on degenerate input (no sentence terminators) — normal Japanese sentences are
# far shorter, so `split_sentences` alone already keeps them under the cap.
# Retune if the deployment hardware or the 5 s target changes; see `align._K_MAX`.
MAX_CHUNK_CHARS = 200

# Only oversized chunks are split at all, and only after `split_sentences` has
# already consumed the sentence terminators (`。！？．`). For those, we prefer to
# cut at a weaker pause mark — a comma above all, then other pause/separator
# characters — so the boundary lands between clauses rather than mid-word. These
# are exactly the marks that stay *inside* a sentence during normal splitting.
_SOFT_BREAK_CHARS = frozenset("、，,・：:；;　 ")

# Don't accept a soft break in the front part of the window: a comma at char 3
# would leave a tiny sliver and barely shrink the remainder. Require the cut to
# fill at least half the window, else fall back to a hard cut at the limit.
_MIN_CHUNK_FILL = MAX_CHUNK_CHARS // 2


def cap_chunk_length(chunk: str, token_boundaries: set[int] | None = None) -> list[str]:
    """Split an oversized chunk into ≤ `MAX_CHUNK_CHARS` pieces, in order.

    Returns `[chunk]` unchanged when it already fits. Only degenerate,
    terminator-free input ever exceeds the cap. When it does, each cut prefers
    the last `_SOFT_BREAK_CHARS` (comma / pause mark) in the back half of the
    window so the boundary lands between clauses, keeping the break char with
    the left piece; it falls back to a hard mid-word cut at the limit only when
    the window has no usable pause (truly unbroken text). Never rejects input —
    every character is preserved across the returned pieces.
    """
    if len(chunk) <= MAX_CHUNK_CHARS:
        return [chunk]
    pieces: list[str] = []
    ordered_boundaries = sorted(token_boundaries or ())
    start = 0
    while len(chunk) - start > MAX_CHUNK_CHARS:
        window_end = start + MAX_CHUNK_CHARS
        minimum_cut = start + _MIN_CHUNK_FILL
        cut = window_end
        for index in range(window_end - 1, minimum_cut - 1, -1):
            if chunk[index] in _SOFT_BREAK_CHARS:
                cut = index + 1
                break
        if cut == window_end and ordered_boundaries:
            boundary_index = bisect_left(ordered_boundaries, window_end) - 1
            if boundary_index >= 0 and ordered_boundaries[boundary_index] > start:
                cut = ordered_boundaries[boundary_index]
        if cut == window_end and not ordered_boundaries:
            for index in range(window_end, minimum_cut, -1):
                left = ord(chunk[index - 1])
                right = ord(chunk[index])
                right_is_kanji = 0x3400 <= right <= 0x9FFF
                left_is_kana = 0x3040 <= left <= 0x30FF
                if left_is_kana and right_is_kanji:
                    cut = index
                    break
        pieces.append(chunk[start:cut])
        start = cut
    if start < len(chunk):
        pieces.append(chunk[start:])
    return pieces


# Symbols that are not kana/kanji but DO carry a spoken kana reading (e.g.
# `%` → パーセント, `℃` → ど). Treated separately from pure punctuation:
# we merge (numeric, readable-symbol) adjacencies into a single compound
# token so OpenJTalk's multi-mora reading lands on the symbol rather than
# leaking onto the preceding digits.
READABLE_SYMBOLS = {"%", "％", "℃", "°", "$", "＄", "¥", "￥", "€"}

_NUMERIC_UNIT_BODY = (
    r"-?\d+(?:\.\d+)?(?:ghz|khz|mhz|km|kg|mg|mm|cm|ml|kw|mw|kv|ma|ms|"
    r"hz|db|nm|m|g|l|w|v|a|s|h)"
)
NUMERIC_UNIT_RE = re.compile(rf"^{_NUMERIC_UNIT_BODY}$", re.IGNORECASE)
_NUMERIC_UNIT_TEXT_RE = re.compile(_NUMERIC_UNIT_BODY, re.IGNORECASE)


def clean_hidden_english(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _NUMERIC_UNIT_TEXT_RE.finditer(text):
        parts.append(
            "".join(
                char
                for char in text[cursor : match.start()]
                if not ("a" <= char <= "z" or "A" <= char <= "Z")
            )
        )
        parts.append(match.group(0))
        cursor = match.end()
    parts.append(
        "".join(
            char
            for char in text[cursor:]
            if not ("a" <= char <= "z" or "A" <= char <= "Z")
        )
    )
    return "".join(parts)


# Standalone-symbol → katakana reading. OpenJTalk spells these out as multi-mora
# katakana when they appear mid-text (`#病` → シャープびょう). UniDic's
# `feat.kana` is empty for these chars, so `tokenizer.tag_local` would emit
# furigana='' and `align._match_cost`'s edit-distance branch would refuse
# them (`|len(y)-len(o)|>3` cuts off) — the OpenJTalk morae then leak onto the
# neighbouring kana token (the `#病→と` cascade in test_0 idx 1411).
# Filling in a reading here lets the aligner match the OpenJTalk span at
# edit-distance 0.
SYMBOL_READINGS = {
    "#": "シャープ",
    "＃": "シャープ",
    "%": "パーセント",
    "％": "パーセント",
    "@": "アットマーク",
    "＠": "アットマーク",
    "&": "アンド",
    "＆": "アンド",
    "+": "プラス",
    "＋": "プラス",
    "=": "イコール",
    "＝": "イコール",
    "$": "ドル",
    "＄": "ドル",
    "¥": "エン",
    "￥": "エン",
    "€": "ユーロ",
    "℃": "ドシー",
    "°": "ド",
    "*": "アスタリスク",
    "＊": "アスタリスク",
    "~": "チルダ",
    "～": "チルダ",
    "§": "セクション",
}

# Numeric pattern accepted as a "standalone number" token (also reused by
# align.py for is_numeric classification).
NUMERIC_PATTERN = re.compile(r"^-?\d+(\.\d+)?$")

# Compound surface = optional sign + digits + decimal + one or more readable
# symbols. `NUMERIC_PATTERN` already accepts decimals/negatives; this regex
# is the same shape with a trailing symbol run.
READABLE_COMPOUND_RE = re.compile(
    r"^-?\d+(?:\.\d+)?[" + "".join(re.escape(c) for c in READABLE_SYMBOLS) + r"]+$"
)


# Re-exported so non-pipeline callers can re-build a fallback WordAccentResult
# without importing AccentInfo separately.
__all__ = [
    "AccentInfo",
    "NUMERIC_PATTERN",
    "READABLE_COMPOUND_RE",
    "READABLE_SYMBOLS",
    "SYMBOL_READINGS",
    "has_japanese",
    "restore_number_commas",
    "restore_urls",
    "restore_x_between_digits",
    "split_sentences",
    "strip_acronym_dots",
    "strip_number_commas",
    "strip_urls",
    "strip_x_between_digits",
]
