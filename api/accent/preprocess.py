"""Pre-alignment text rewrites + post-alignment surface restoration.

Three independent rewrites run on the request text before tokenisation
and OpenJTalk accent extraction; each pairs a `_strip_*` pass that mutates
the text
with a `_restore_*` pass that walks the aligned results to put the
original surfaces back.

  * **URLs** (`_strip_urls` / `_restore_urls`) — swap each URL for one
    fixed placeholder so the alignment DP isn't dragged off-rail by
    Latin punctuation runs.
  * **Western-grouped thousands** (`_strip_number_commas` /
    `_restore_number_commas`) — `1,234` → `1234` so OpenJTalk reads the
    whole integer as one phrase.
  * **× between digits** (`_strip_x_between_digits` /
    `_restore_x_between_digits`) — `19×19` → `19/19` so OpenJTalk splits the
    reading instead of merging into `1919`.

`_has_japanese` is the early-exit gate for the pipeline: a chunk with
no kana / kanji is echoed back verbatim without hitting OpenJTalk.
"""

from __future__ import annotations

import logging
import re

from api.accent.models import AccentInfo, WordAccentResult

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
# URL for one fixed-string placeholder (which the tokeniser keeps as a
# single "alphabet" word), run the pipeline, then walk the result and
# restore the originals in order.
# URL body stops at whitespace, any Japanese char (so `…はhttps://x.jp/aです`
# strips just the URL, leaving `です` to be processed), or common quoting
# punctuation `,()<>[]"'` (so `(https://x.jp)` strips just the URL).
_URL_RE = re.compile(r"https?://[^\s　-鿿,()<>\[\]\"']+")
_URL_PLACEHOLDER = "URLPLACEHOLDER"


def strip_urls(text: str) -> tuple[str, list[str]]:
    """Replace each URL with `_URL_PLACEHOLDER`, returning URLs in order."""
    urls: list[str] = []

    def repl(m: "re.Match[str]") -> str:
        urls.append(m.group(0))
        return _URL_PLACEHOLDER

    return _URL_RE.sub(repl, text), urls


def restore_urls(
    result: list[WordAccentResult], urls: list[str]
) -> list[WordAccentResult]:
    """Swap placeholder tokens in `result` back to their original URLs."""
    if not urls:
        return result
    it = iter(urls)
    out: list[WordAccentResult] = []
    for w in result:
        if w.surface == _URL_PLACEHOLDER:
            url = next(it, None)
            if url is None:
                # Placeholder count exceeded URL count: leave the token
                # untouched. Indicates a tokenisation surprise; the
                # output is still readable.
                out.append(w)
                continue
            out.append(
                WordAccentResult(surface=url, furigana=url, accent=[], subword=[])
            )
        else:
            out.append(w)
    return out


# Western-style grouped numbers: `1,234`, `1,234,567`, `12,345.67`. The
# match must start with 1-3 digits and have each group be exactly 3 digits
# so we don't fire on `1,23` or list-separated number runs like
# `1, 2, 3`. After neologdn normalisation we only see ASCII commas.
_NUMERIC_COMMA_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def strip_number_commas(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Strip commas from western-grouped numbers in `text`.

    Returns `(cleaned, [(stripped_form, original_form), ...])` where each
    pair records a `1,234` → `1234` rewrite in order of appearance. After
    pipeline alignment, `restore_number_commas` swaps the surfaces back.
    fugashi splits `1,234` into three tokens (digit / `,` / digit) and the
    DP can't easily reassemble them; doing the splice at the text level
    lets the merged digit string flow as one numeric token through OpenJTalk,
    which then reads it as a single integer (せんにひゃくさんじゅうよん).
    """
    strips: list[tuple[str, str]] = []

    def repl(m: "re.Match[str]") -> str:
        original = m.group(0)
        stripped = original.replace(",", "")
        strips.append((stripped, original))
        return stripped

    return _NUMERIC_COMMA_RE.sub(repl, text), strips


def restore_number_commas(
    result: list[WordAccentResult], strips: list[tuple[str, str]]
) -> list[WordAccentResult]:
    """Walk `result` in order, restoring `1,234`-style surfaces.

    Matches a token whose surface equals the next pending stripped form
    and rewrites its surface back to the original (commas intact). The
    OpenJTalk-derived furigana / accent payload is left untouched — those
    were produced from the cleaned `1234` form and remain correct as the
    spoken reading.
    """
    if not strips:
        return result
    pending = iter(strips)
    cur = next(pending, None)
    out: list[WordAccentResult] = []
    for w in result:
        if cur is not None and w.surface == cur[0]:
            out.append(
                WordAccentResult(
                    surface=cur[1],
                    furigana=w.furigana,
                    accent=w.accent,
                    subword=w.subword,
                    base=w.base,
                    pos=w.pos,
                    pos1=w.pos1,
                    conjugation_type=w.conjugation_type,
                    conjugation_form=w.conjugation_form,
                    lexical_kernel=w.lexical_kernel,
                    lexical_kernel_alts=w.lexical_kernel_alts,
                    kernel_absorbed=w.kernel_absorbed,
                )
            )
            cur = next(pending, None)
        else:
            out.append(w)
    if cur is not None:
        # An override merged the numeric surface into a kanji-counter token
        # (e.g. `1234` + `円` → `1234円` with a custom furigana); we lose
        # the chance to restore the comma. Logging instead of failing
        # because the user still sees the right reading; only the
        # comma-formatted surface is missing.
        logger.warning(
            "Number-comma restore: %d strip(s) unmatched (first=%r)",
            sum(1 for _ in pending) + 1,
            cur,
        )
    return out


# `\d×\d` (and `\d × \d` with spaces) gets merged by OpenJTalk's phrasing
# module into one number: `19×19` reads as せん きゅう ひゃく じゅう
# きゅう (= 1919) instead of two じゅう きゅう. Swap × → / for the
# OpenJTalk/fugashi pass; `/` is one of the few separators OpenJTalk treats as a
# phrase break without inserting any spoken kana. The original `×` is
# restored on the surface after alignment.
_X_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d)\s*[×✕✖]\s*(?=\d)")


def strip_x_between_digits(text: str) -> tuple[str, int]:
    """Replace `\\d × \\d` with `\\d/\\d` so OpenJTalk splits the reading.

    Returns `(cleaned, count)`. `count` is the number of substitutions
    so `restore_x_between_digits` knows how many `/` surfaces to swap
    back to `×`.
    """
    cleaned, count = _X_BETWEEN_DIGITS_RE.subn("/", text)
    return cleaned, count


def restore_x_between_digits(
    result: list[WordAccentResult], count: int
) -> list[WordAccentResult]:
    """Swap `/` surfaces back to `×`, in order, up to `count` times.

    Matches each `/` token left-to-right against the pending substitution
    budget. We don't track which exact `/` was a stripped × — there's no
    way to do that after fugashi has already tokenised — so any literal
    `/` the user wrote between two digits will get rewritten back to ×.
    In practice digit-flanked `/` is overwhelmingly used for arithmetic
    or "and" contexts where × is the more common surface, so the
    one-direction mapping is fine.

    KNOWN HAZARD (accepted): the budget is consumed by the FIRST `count`
    `/` surfaces in token order, not the ones that were actually stripped
    from `×`. So a chunk that mixes a real `19×19` (stripped to `19/19`,
    count=1) with a user-written literal `1/2` will mis-restore when the
    literal appears earlier in token order: the budget lands on `1/2` →
    `1×2` and the real `19/19` is left as-is. We accept this because a
    digit-flanked `×` is far more common in real input than a literal
    digit-flanked `/`, so spending the budget on the wrong surface is the
    rarer failure. (Fixing it would require threading per-occurrence
    provenance through tokenisation, which fugashi erases.)
    """
    if count == 0:
        return result
    remaining = count
    out: list[WordAccentResult] = []
    for w in result:
        if remaining > 0 and w.surface == "/":
            out.append(
                WordAccentResult(
                    surface="×",
                    furigana=w.furigana,
                    accent=w.accent,
                    subword=w.subword,
                    base=w.base,
                    pos=w.pos,
                    pos1=w.pos1,
                    conjugation_type=w.conjugation_type,
                    conjugation_form=w.conjugation_form,
                    lexical_kernel=w.lexical_kernel,
                    lexical_kernel_alts=w.lexical_kernel_alts,
                    kernel_absorbed=w.kernel_absorbed,
                )
            )
            remaining -= 1
        else:
            out.append(w)
    if remaining > 0:
        logger.warning(
            "x-between-digits restore: %d substitution(s) unmatched",
            remaining,
        )
    return out


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
    the in-flight Semaphore.
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


def cap_chunk_length(chunk: str) -> list[str]:
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
    rest = chunk
    while len(rest) > MAX_CHUNK_CHARS:
        cut = MAX_CHUNK_CHARS  # hard-cut fallback
        for i in range(MAX_CHUNK_CHARS - 1, _MIN_CHUNK_FILL - 1, -1):
            if rest[i] in _SOFT_BREAK_CHARS:
                cut = i + 1  # keep the pause mark with the left piece
                break
        pieces.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        pieces.append(rest)
    return pieces


# Symbols that are not kana/kanji but DO carry a spoken kana reading (e.g.
# `%` → パーセント, `℃` → ど). Treated separately from pure punctuation:
# we merge (numeric, readable-symbol) adjacencies into a single compound
# token so OpenJTalk's multi-mora reading lands on the symbol rather than
# leaking onto the preceding digits.
READABLE_SYMBOLS = {"%", "％", "℃", "°", "$", "＄", "¥", "￥", "€"}

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
