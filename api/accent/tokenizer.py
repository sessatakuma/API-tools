"""Local UniDic tokeniser (data layer).

Replaces the former Yahoo MA HTTP path with in-process fugashi + NINJAL
UniDic CWJ 2025-12-31. Field mapping vs the old Yahoo MA response:

    surface          ← token.surface
    furigana         ← jaconv.kata2hira(feat.kana) (fugashi emits katakana)
    base             ← feat.lemma (with "-gloss" suffix stripped)
    pos              ← feat.pos1            (top-level POS, e.g. 動詞 / 助動詞)
    pos1             ← feat.pos2            (sub-category, e.g. 一般 / 普通名詞)
    conjugation_type ← feat.cType
    conjugation_form ← feat.cForm
    lexical_kernel   ← parsed from feat.aType (UniDic-only; was unavailable
                        via Yahoo MA)

All fields in fugashi's feature struct use "*" as the null marker; we map
those to None so the downstream patches see the same shape they did under
Yahoo MA.
"""

from __future__ import annotations

import re

import fugashi
import jaconv

from api.accent.models import WordResult
from api.accent.preprocess import NUMERIC_PATTERN, SYMBOL_READINGS

_UNIDIC_NULL = "*"

# Pure-ASCII-alphabet surface (used to detect the letter pieces of a
# fugashi-split acronym so they can be glued back together below).
_ALPHA_ONLY_RE = re.compile(r"^[A-Za-z]+$")

_TAGGER: fugashi.Tagger | None = None


def _get_tagger() -> fugashi.Tagger:
    """Lazy-instantiate the fugashi tagger.

    Singleton because constructing `fugashi.Tagger()` loads the UniDic
    dictionary (~1.3 GB) and takes a second or two; per-request construction
    would be wasteful.
    """
    global _TAGGER
    if _TAGGER is None:
        _TAGGER = fugashi.Tagger()
    return _TAGGER


def _none_if_null(value: str | None) -> str | None:
    return None if value in (None, _UNIDIC_NULL) else value


def _strip_lemma_gloss(lemma: str | None) -> str | None:
    """Drop the English/etymology gloss fugashi appends to loanword lemmas.

    UniDic stores `コーヒー-coffee` as the lemma for コーヒー; we want just
    `コーヒー` to keep parity with the lemmas Yahoo MA used to return.
    """
    if lemma is None:
        return None
    return lemma.split("-", 1)[0]


def _parse_atype(atype_raw: str | None) -> tuple[int | None, list[int] | None]:
    """Parse fugashi's `aType` field (UniDic per-morpheme kernel position).

    `aType` is `"*"` for tokens without accent annotation (particles,
    auxiliaries, etc.), a single int as string for unambiguous entries
    (`"2"`), or comma-separated ints when UniDic records multiple attested
    readings (`"2,0"`).

    Returns (primary, alternates):
      - `(None, None)` for unknown
      - `(2, None)` for a single reading
      - `(2, [2, 0])` for multi-reading (alts always contains the primary)
    """
    if atype_raw is None or atype_raw == _UNIDIC_NULL:
        return None, None
    parts = [p.strip() for p in atype_raw.split(",")]
    values: list[int] = []
    for p in parts:
        try:
            values.append(int(p))
        except ValueError:
            return None, None
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], None
    return values[0], values


def _is_alpha_piece(surface: str) -> bool:
    return bool(_ALPHA_ONLY_RE.match(surface))


def _is_digit_piece(surface: str) -> bool:
    return bool(surface) and all("0" <= c <= "9" for c in surface)


def _is_bridge_piece(surface: str) -> bool:
    """`-` / `_` / `.` between two alpha-or-digit pieces with no whitespace.

    Used to extend the acronym-merge across product-code and version-
    number separators (`PSP-1000`, `Wi-Fi`, `RTX-4090`, `foo_bar1`,
    `Wifi.7`, `Python3.11`). Sentence-level uses of these chars are
    filtered out by the look-ahead — a stray separator at the start /
    end of an input, around whitespace, or next to punctuation is left
    as its own token. So `Mr. Smith` keeps the standalone `.` (the
    next piece is whitespace-prefixed), and an end-of-line `Foo.`
    falls through too (no next piece).

    Decimals like `2.5` and IPs like `192.168.1.1` stay split: the
    `.` enters the buffered run, but the `has_letter` check in
    `flush_run` prevents pure-digit runs from being fused, so they
    spill back into individual tokens unchanged. `/` is intentionally
    NOT a bridge char — it conflicts with fractions (`1/2`) and dates
    (`2024/01/15`) where the desired reading isn't acronym-like.
    """
    return surface in ("-", "_", ".")


def _flush_alphanumeric_run(buf: list[WordResult]) -> WordResult:
    """Fuse a buffered run of alpha/digit/bridge WordResults into one
    acronym token (e.g. `[G, 2, P]` → `G2P`, `[PSP, -, 1000]` →
    `PSP-1000`, `[Wifi, ., 7]` → `Wifi.7`).

    Strips MA-derived metadata: the merged token represents a foreign
    acronym, not a Japanese morpheme, so kernel / POS hints don't apply.
    """
    surface = "".join(w.surface for w in buf)
    furigana = "".join(w.furigana or w.surface for w in buf)
    return WordResult(
        surface=surface,
        furigana=furigana,
        base=None,
        pos=None,
        pos1=None,
        conjugation_type=None,
        conjugation_form=None,
        lexical_kernel=None,
        lexical_kernel_alts=None,
    )


def _flush_decimal_run(buf: list[WordResult]) -> WordResult:
    """Fuse `[digit, ., digit]` into one numeric token (`[12, ., 5]` →
    `12.5`).

    Without this, fugashi's three-token split lets the DP align `12` →
    `じゅうに(い)`, `.` (punct) → nothing, and `5` greedily absorbs the
    `てん` (decimal-point reading) along with `ごお`. Visually the `てん`
    morae from `.` end up over the `5`, not the `.`. Merging into one
    `12.5` numeric surface lets the numeric branch free-consume all of
    OJAD's morae for the decimal as one unit — `12.5` then renders with
    a single per-mora ruler covering `じゅうにいてんごお`.

    `pos` is set to "名詞" so downstream POS-aware patches (the ます/たい
    detection in `apply_accent_patches`) treat the merged token like the
    original digit tokens fugashi emitted. NUMERIC_PATTERN.match handles
    the surface classification in align.py.
    """
    surface = "".join(w.surface for w in buf)
    return WordResult(
        surface=surface,
        furigana=surface,
        base=None,
        pos="名詞",
        pos1=None,
        conjugation_type=None,
        conjugation_form=None,
        lexical_kernel=None,
        lexical_kernel_alts=None,
    )


def tag_local(text: str) -> list[WordResult]:
    """Tokenise `text` with local fugashi + NINJAL UniDic.

    Drop-in replacement for the former Yahoo Furigana fetch — returns the
    same `list[WordResult]` shape that `apply_furigana_overrides` and
    `align_accent` expect, with two added fields populated from UniDic:
    `lexical_kernel` (= aType primary) and `lexical_kernel_alts`
    (= aType alternates when multi-reading).

    Adjacent (alpha|digit) fugashi tokens with no whitespace between them
    are fused into one acronym token (`G + 2 + P` → `G2P`, `iPhone + 7` →
    `iPhone7`); a single `-` / `_` / `.` between two such pieces is
    absorbed too (`PSP-1000`, `Wi-Fi`, `RTX-4090`, `foo_bar1`,
    `Wifi.7`, `Python3.11`). The run must contain at least one
    alphabetic piece, so pure-digit ranges (`1-2`, `0-9`) and decimals
    (`2.5`, `192.168.1.1`) stay as their original tokens and flow
    through the numeric branch unchanged.

    fugashi's `white_space` attribute is the gate: `Hello world` has
    `world.white_space == " "` and stays as two tokens; `G2P` has empty
    `white_space` between pieces so it merges. The merged surface is
    what `postprocess._is_pure_english_surface` recognises, so
    `apply_furigana_toggles` wipes the entire reading uniformly when
    `render_english_furigana=False`.
    """
    tagger = _get_tagger()
    # Materialise the fugashi token list once so the bridge logic can
    # peek ahead at `toks[i + 1]` — generators don't support indexing.
    toks = list(tagger(text))
    n = len(toks)
    parsed: list[WordResult] = []
    run_buf: list[WordResult] = []
    run_has_letter = False

    def flush_run() -> None:
        nonlocal run_has_letter
        if not run_buf:
            return
        if len(run_buf) >= 2 and run_has_letter:
            parsed.append(_flush_alphanumeric_run(run_buf))
        elif len(run_buf) >= 2 and not run_has_letter:
            # Letter-less run: only fuse if the joined surface looks like
            # a single decimal number (`12.5`). Excludes pure-digit ranges
            # (`1-2`), version triples (`1.2.3`), IP-style runs
            # (`192.168.1.1`) — those join via `NUMERIC_PATTERN.match`
            # below only if the shape is `\d+(\.\d+)?`, so they spill
            # back into individual tokens unchanged.
            joined = "".join(w.surface for w in run_buf)
            if NUMERIC_PATTERN.match(joined):
                parsed.append(_flush_decimal_run(run_buf))
            else:
                parsed.extend(run_buf)
        else:
            parsed.extend(run_buf)
        run_buf.clear()
        run_has_letter = False

    def _leading_space(t: object) -> str:
        return getattr(t, "white_space", "") or ""

    for i, tok in enumerate(toks):
        feat = tok.feature
        surface = tok.surface
        # Pick the orthographic kana (`kana`) rather than the phonological
        # `pron`: UniDic stores 忙しい as `kana=イソガシイ` (matches OJAD's
        # ortho-kana output) but `pron=イソガシー` (with chōonpu, which
        # would never align). Fall back through pron then surface if kana
        # is missing or null (e.g. punctuation tokens have `kana="*"`).
        kana_kata = _none_if_null(getattr(feat, "kana", None))
        if kana_kata is None:
            kana_kata = _none_if_null(getattr(feat, "pron", None))
        # UniDic emits empty `kana` for non-CJK symbols (#, %, @ …). OJAD,
        # however, vocalises them (シャープ, パーセント, アットマーク).
        # Without a synthetic reading the aligner's edit-distance branch
        # rejects the OJAD span and morae leak onto the next kana token.
        if kana_kata is None and surface in SYMBOL_READINGS:
            kana_kata = SYMBOL_READINGS[surface]
        reading = jaconv.kata2hira(kana_kata) if kana_kata else surface
        primary, alts = _parse_atype(getattr(feat, "aType", None))
        word = WordResult(
            surface=surface,
            furigana=reading,
            base=_strip_lemma_gloss(getattr(feat, "lemma", None)),
            pos=_none_if_null(getattr(feat, "pos1", None)),
            pos1=_none_if_null(getattr(feat, "pos2", None)),
            conjugation_type=_none_if_null(getattr(feat, "cType", None)),
            conjugation_form=_none_if_null(getattr(feat, "cForm", None)),
            lexical_kernel=primary,
            lexical_kernel_alts=alts,
        )

        is_alpha = _is_alpha_piece(surface)
        is_digit = _is_digit_piece(surface)
        is_bridge = _is_bridge_piece(surface)
        leading_space = _leading_space(tok)

        # Bridge (`-` / `_`) joins the run only when (a) we're mid-run,
        # (b) no leading whitespace, AND (c) the NEXT token is itself
        # alpha/digit with no leading whitespace. Without the look-ahead,
        # `Foo-` at end-of-input or `Mr. -` mid-sentence would absorb
        # the standalone `-` into the acronym surface.
        if run_buf and not leading_space and is_bridge:
            nxt = toks[i + 1] if i + 1 < n else None
            if (
                nxt is not None
                and not _leading_space(nxt)
                and (_is_alpha_piece(nxt.surface) or _is_digit_piece(nxt.surface))
            ):
                run_buf.append(word)
                continue
            # Fall through: this `-` doesn't actually bridge anything.

        # An alpha/digit token joins the current run only when (a) it
        # is itself alpha or digit, AND (b) it was emitted with no
        # leading whitespace — `Hello world` must not collapse into
        # `Helloworld`. fugashi exposes the source-text whitespace
        # before the token as `white_space` (empty string when the
        # previous token was glued to this one in the source).
        joins_run = (is_alpha or is_digit) and (not run_buf or not leading_space)

        if joins_run:
            run_buf.append(word)
            if is_alpha:
                run_has_letter = True
        else:
            flush_run()
            if is_alpha or is_digit:
                run_buf.append(word)
                run_has_letter = is_alpha
            else:
                parsed.append(word)

    flush_run()
    return parsed
