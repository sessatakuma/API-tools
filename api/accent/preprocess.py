"""Pre-alignment text rewrites + post-alignment surface restoration.

Three independent rewrites run on the request text before tokenisation
and OJAD scraping; each pairs a `_strip_*` pass that mutates the text
with a `_restore_*` pass that walks the aligned results to put the
original surfaces back.

  * **URLs** (`_strip_urls` / `_restore_urls`) — swap each URL for one
    fixed placeholder so the alignment DP isn't dragged off-rail by
    Latin punctuation runs.
  * **Western-grouped thousands** (`_strip_number_commas` /
    `_restore_number_commas`) — `1,234` → `1234` so OJAD reads the
    whole integer as one phrase.
  * **× between digits** (`_strip_x_between_digits` /
    `_restore_x_between_digits`) — `19×19` → `19/19` so OJAD splits the
    reading instead of merging into `1919`.

`_has_japanese` is the early-exit gate for the pipeline: a chunk with
no kana / kanji is echoed back verbatim without hitting OJAD.
"""

from __future__ import annotations

import logging
import re

from api.accent.models import AccentInfo, WordAccentResult, WordResult

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


# URLs are stripped before the pipeline runs. OJAD's phrasing scraper
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
    lets the merged digit string flow as one numeric token through OJAD,
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
    OJAD-derived furigana / accent payload is left untouched — those
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


# `\d×\d` (and `\d × \d` with spaces) gets merged by OJAD's phrasing
# module into one number: `19×19` reads as せん きゅう ひゃく じゅう
# きゅう (= 1919) instead of two じゅう きゅう. Swap × → / for the
# OJAD/fugashi pass; `/` is one of the few separators OJAD treats as a
# phrase break without inserting any spoken kana. The original `×` is
# restored on the surface after alignment.
_X_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d)\s*[×✕✖]\s*(?=\d)")


def strip_x_between_digits(text: str) -> tuple[str, int]:
    """Replace `\\d × \\d` with `\\d/\\d` so OJAD splits the reading.

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
# and an ASCII letter) — i.e. `Wifi.7`, `iPhone.7`, `i.e`. OJAD silently
# normalises these `.`s to full-width `。` (`Wifi.7` → echoed as `Wifi。7`)
# and the prosody CRF then collapses pitch accents on everything after
# that point (`といった技術` came back all-zero, the user-reported
# "音調不見了" symptom). We strip the `.` from OJAD's query so the CRF
# sees a clean `Wifi7` and produces a normal contour for the rest of
# the sentence; fugashi still sees the original surface, so the
# tokenizer's acronym-merge step keeps the user-visible `Wifi.7`.
#
# Digit-flanked `.` is deliberately NOT stripped: `2.5` and `192.168.1.1`
# don't trigger OJAD's `.` → `。` normalisation (the CRF stays sane),
# and stripping would change the spoken reading from "two point five"
# to "twenty five".
_OJAD_ACRONYM_DOT_RE = re.compile(
    r"(?<=[A-Za-z])\.(?=[A-Za-z0-9])|(?<=[A-Za-z0-9])\.(?=[A-Za-z])"
)


def strip_acronym_dots_for_ojad(text: str) -> str:
    """Remove `.` between alpha+alphanumeric pairs in the OJAD query.

    Asymmetric strip (no restore): only the OJAD-side text loses the
    `.`. fugashi continues to see the original so its tokenization +
    the tokenizer's acronym-merge keep the user-visible `Wifi.7`
    surface intact. The aligner's english-compound branch is permissive
    enough to align the merged token against OJAD's `Wifi7` reading
    without further accommodation.
    """
    return _OJAD_ACRONYM_DOT_RE.sub("", text)


# Sentence terminators that close a Japanese clause: kuten (。), full-width
# question (？), full-width exclamation (！), and full-width period (．).
# ASCII `.!?` are intentionally excluded — they appear in abbreviations,
# decimals, and code/identifier fragments that we don't want to split on.
# A zero-width split (lookbehind) keeps the terminator attached to the
# preceding sentence so accent prediction still sees the clause boundary.
_SENTENCE_SPLIT_RE = re.compile("(?<=[。！？．])")


def split_sentences(line: str) -> list[str]:
    """Split a line into sentence-sized chunks for parallel processing.

    OJAD's phrasing module degrades badly on long inputs (a single
    misaligned mora can cascade across the whole paragraph), and the
    streaming endpoint can't parallelise within a `\\n`-delimited chunk.
    Splitting on full-width sentence terminators fixes both: each sentence
    is short enough for OJAD to handle reliably, and they fan out across
    the in-flight Semaphore.
    """
    return [s for s in _SENTENCE_SPLIT_RE.split(line) if s.strip()]


# Symbols that are not kana/kanji but DO carry a spoken kana reading (e.g.
# `%` → パーセント, `℃` → ど). Treated separately from pure punctuation:
# we merge (numeric, readable-symbol) adjacencies into a single compound
# token so OJAD's multi-mora reading lands on the symbol rather than
# leaking onto the preceding digits.
READABLE_SYMBOLS = {"%", "％", "℃", "°", "$", "＄", "¥", "￥", "€"}

# Numeric pattern accepted as a "standalone number" token (also reused by
# align.py for is_numeric classification).
NUMERIC_PATTERN = re.compile(r"^-?\d+(\.\d+)?$")

# Compound surface = optional sign + digits + decimal + one or more readable
# symbols. `NUMERIC_PATTERN` already accepts decimals/negatives; this regex
# is the same shape with a trailing symbol run.
READABLE_COMPOUND_RE = re.compile(
    r"^-?\d+(?:\.\d+)?[" + "".join(re.escape(c) for c in READABLE_SYMBOLS) + r"]+$"
)


def merge_readable_symbol_compounds(tokens: list[WordResult]) -> list[WordResult]:
    """Glue `(digit, %)` style adjacencies into one numeric-like token.

    fugashi splits `2%` into two tokens; the DP's punct branch then refuses
    to absorb the `パーセント` morae OJAD produces, so they leak onto the
    digit. Pre-merging gives the alignment a single token whose surface
    matches what OJAD's phrasing module treated as one phrase, and lets
    the build path reuse the existing numeric branch that synthesises
    the displayed furigana from the OJAD span.
    """
    merged: list[WordResult] = []
    i = 0
    while i < len(tokens):
        cur = tokens[i]
        if (
            i + 1 < len(tokens)
            and NUMERIC_PATTERN.match(cur.surface)
            and all(c in READABLE_SYMBOLS for c in tokens[i + 1].surface)
            and tokens[i + 1].surface
        ):
            sym = tokens[i + 1]
            combined_surface = cur.surface + sym.surface
            combined_furigana = (cur.furigana or cur.surface) + (
                sym.furigana or sym.surface
            )
            merged.append(
                WordResult(
                    surface=combined_surface,
                    furigana=combined_furigana,
                    base=None,
                    pos=None,
                    pos1=None,
                    conjugation_type=None,
                    conjugation_form=None,
                    lexical_kernel=None,
                    lexical_kernel_alts=None,
                )
            )
            i += 2
        else:
            merged.append(cur)
            i += 1
    return merged


# Re-exported so non-pipeline callers can re-build a fallback WordAccentResult
# without importing AccentInfo separately.
__all__ = [
    "AccentInfo",
    "NUMERIC_PATTERN",
    "READABLE_COMPOUND_RE",
    "READABLE_SYMBOLS",
    "has_japanese",
    "merge_readable_symbol_compounds",
    "restore_number_commas",
    "restore_urls",
    "restore_x_between_digits",
    "split_sentences",
    "strip_acronym_dots_for_ojad",
    "strip_number_commas",
    "strip_urls",
    "strip_x_between_digits",
]
