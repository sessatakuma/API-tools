"""Post-alignment passes — rendering polish on aligned WordAccentResults.

Runs after `align_accent` + `apply_accent_overrides` + `apply_accent_patches`:

  * `suppress_punct_furigana` — drop ruby on pure-punct tokens even when
    `apply_accent_overrides` rebuilt them with a fallback type-0 accent
    (e.g. bracket-style `(土)` tokens).
  * `flatten_heiban_particle_accent` — zero out the trailing HIGH overlay
    OJAD assigns to `の / な / は / が` after a heiban noun.
  * `apply_furigana_toggles` — drop ruby (and, for English, the accent
    list) on pure-English / pure-katakana tokens when the request toggle
    is off.
  * `suppress_particle_furigana` — clear redundant top-level furigana on
    每 助詞 token so per-mora overlays aren't crowded by duplicate ruby.

Each pass is idempotent and pure: input list isn't mutated; new list is
returned. Order matters — see `pipeline.process_accent_chunk` for the
canonical call order.
"""

from __future__ import annotations

import logging

import jaconv

from api.accent.models import AccentInfo, WordAccentResult, WordResult
from api.accent.preprocess import (
    NUMERIC_PATTERN,
    READABLE_COMPOUND_RE,
    SYMBOL_READINGS,
)

logger = logging.getLogger("api")


def _is_pure_punct_surface(surface: str) -> bool:
    """Internal: True if `surface` is non-empty, non-numeric, non-readable-
    compound, and contains no kana/kanji/ASCII-letter chars.

    Used by `suppress_punct_furigana` to identify ruby-less tokens after
    override-driven rebuilds. Inlined from `align._is_punct_token`'s rules
    to avoid the circular dep on align.py.
    """
    if not surface:
        return False
    if NUMERIC_PATTERN.match(surface):
        return False
    if READABLE_COMPOUND_RE.match(surface):
        return False
    # Standalone symbols with a spoken reading (`#`, `%`, `@` …) carry
    # real morae after `tokenizer.tag_local` fills in a `SYMBOL_READINGS`
    # fallback. Treating them as punct here would wipe both the ruby and
    # the per-mora pitch list emitted by the aligner.
    if surface in SYMBOL_READINGS:
        return False
    # Inline `is_kana_or_kanji` to keep postprocess free of align.py imports.
    exception_symbols = {"゠", "・", "ー", "ヽ", "ヾ", "ヿ"}
    for c in surface:
        if c in exception_symbols:
            continue
        # ASCII-letter tokens are English, not punct.
        if "a" <= c <= "z" or "A" <= c <= "Z":
            return False
        code = ord(c)
        # 0x3040-0x30FF = hira+kata, 0x4E00-0x9FFF = CJK Unified Ideographs.
        if 0x3040 <= code <= 0x30FF or 0x4E00 <= code <= 0x9FFF:
            return False
    return True


def suppress_punct_furigana(
    result: list[WordAccentResult],
) -> list[WordAccentResult]:
    """Empty out furigana + accent on every pure-punct token in `result`.

    The DP build already does this for tokens that flow through alignment,
    but `apply_accent_overrides` rebuilds bracket / weekday-bracket tokens
    with a fallback `accent_marking_type=0` AccentInfo — undoing the
    suppression for inputs like `3月5日(土)`. Running this pass after
    overrides gives the same "skip ruby" signal regardless of which path
    produced the token.
    """
    out: list[WordAccentResult] = []
    for w in result:
        if _is_pure_punct_surface(w.surface):
            out.append(
                WordAccentResult(
                    surface=w.surface,
                    furigana="",
                    accent=[],
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
        else:
            out.append(w)
    return out


def _is_pure_english_surface(surface: str) -> bool:
    """True if `surface` is non-empty, contains at least one ASCII letter,
    and every char is an ASCII letter, digit, or acronym bridge
    (`-` / `_` / `.`).

    Mixed-with-digit tokens like `iPhone7` count as English; so do
    `tokenizer.tag_local`-fused model codes (`PSP-1000`, `Wi-Fi`,
    `RTX-4090`, `foo_bar1`) and version-style identifiers (`Wifi.7`,
    `Python3.11`). The bridge allowance must mirror
    `align._is_english_compound_surface` so the aligner's free-consume
    branch and `apply_furigana_toggles`'s wipe agree on which surfaces
    qualify. Pure-numeric / pure-bridge tokens are excluded.
    """
    if not surface:
        return False
    has_letter = False
    for c in surface:
        if "a" <= c <= "z" or "A" <= c <= "Z":
            has_letter = True
        elif "0" <= c <= "9" or c in ("-", "_", "."):
            continue
        else:
            return False
    return has_letter


def _is_pure_katakana_surface(surface: str) -> bool:
    """True if `surface` is non-empty and every char is in the katakana block.

    Includes the half-width-katakana-friendly extension chars (ー, ・ …)
    so tokens like `コーヒー` and `バスケットボール` qualify.
    """
    if not surface:
        return False
    for c in surface:
        if not (0x30A0 <= ord(c) <= 0x30FF):
            return False
    return True


def _is_heiban_token(w: WordAccentResult) -> bool:
    """True if `w` has the heiban (平板調) accent shape: no FALL kernel
    anywhere in the per-mora list, plus at least one HIGH plateau mora.

    Matches the heiban detection rule documented on `AccentInfo`.
    """
    if not w.accent:
        return False
    if any(a.accent_marking_type == 2 for a in w.accent):
        return False
    return any(a.accent_marking_type == 1 for a in w.accent)


# Particles where OJAD's heiban-continuation HIGH overlay adds visual
# noise without information. Caller preference: render these as flat
# LOW after a heiban word so the per-mora ruler doesn't stretch a HIGH
# bar across the noun boundary.
_HEIBAN_FLATTEN_PARTICLES = frozenset({"の", "な", "は", "が", "を", "に", "で"})

# Closing brackets/quotes that don't break the heiban→particle relationship:
# 「柔道」は still wants the は flattened against 柔道's heiban contour. The
# closing mark carries no spoken kana (already empty-accent after align), so
# we treat it as transparent and look further back for the heiban predecessor.
# Sentence-ending punct (。、 etc.) is deliberately NOT in this set — those
# mark a real prosodic break and the following particle should keep its OJAD
# pitch.
_TRANSPARENT_CLOSE_PUNCT = frozenset(
    {"」", "』", "）", ")", "〉", "》", "】", "]", "〕", "”", "’"}
)


def flatten_heiban_particle_accent(
    result: list[WordAccentResult],
) -> list[WordAccentResult]:
    """Zero out the pitch on の / な / は / が / を / に / で following a 平板調 word.

    After a heiban noun (学校, 富士山, 元気 …), OJAD assigns HIGH (1) to
    the trailing particle to maintain the high plateau. Visually that
    paints the particle with a HIGH overlay that adds noise without
    new contour information. The caller prefers LOW (0) instead.

    Scoped to a fixed set of case/topic particles (の, な, は, が, を,
    に, で) — the ones where the heiban-continuation HIGH was reported
    as visual noise. Other particles (へ, と, や, も …) keep their
    OJAD-derived pitch.

    Look-back skips closing brackets/quotes (`_TRANSPARENT_CLOSE_PUNCT`)
    so 「柔道」は flattens against 柔道, not against the `」` (which has
    empty accent after align and would otherwise short-circuit the rule).
    """
    out: list[WordAccentResult] = []
    prev: WordAccentResult | None = None
    for w in result:
        if (
            prev is not None
            and w.surface in _HEIBAN_FLATTEN_PARTICLES
            and _is_heiban_token(prev)
            and w.accent
        ):
            new_accent = [
                AccentInfo(
                    furigana=a.furigana,
                    accent_marking_type=0,
                    length=a.length,
                )
                for a in w.accent
            ]
            out.append(
                WordAccentResult(
                    surface=w.surface,
                    furigana=w.furigana,
                    accent=new_accent,
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
        else:
            out.append(w)
        # Track the ORIGINAL `w` (pre-modification) so a の-after-の
        # chain doesn't cascade-flatten based on a flat predecessor.
        # Closing brackets/quotes are transparent: keep the previous
        # `prev` so 「柔道」は still sees 柔道 as predecessor.
        if w.surface not in _TRANSPARENT_CLOSE_PUNCT:
            prev = w
    return out


def _has_kana(s: str) -> bool:
    """True if `s` contains at least one hiragana or katakana char."""
    return any(0x3040 <= ord(c) <= 0x30FF for c in s)


def apply_furigana_toggles(
    result: list[WordAccentResult],
    render_english: bool,
    render_katakana: bool,
) -> list[WordAccentResult]:
    """Suppress furigana on English / katakana tokens when their toggle is off.

    English (toggle off): clear both furigana AND accent — foreign
    tokens carry no meaningful Japanese pitch contour. Skipped when
    the token already carries a Japanese reading: unit compounds like
    `53mm` (furi=みりめーとる) and `33m/s` (furi=めーとるまいびょう)
    have ASCII surfaces but Japanese furigana fugashi/UniDic resolved
    for the unit — wiping those would lose the unit reading.

    Katakana (toggle off): clear only the top-level `furigana` (a
    learner who reads katakana doesn't need ruby on コーヒー), but keep
    `accent` so callers can still render the pitch curve over the
    surface text. Each AccentInfo's per-mora furigana lets the client
    align pitch indicators with the katakana morae.

    Tokens containing kanji are never touched, even if mixed with
    English/katakana, because their reading is still load-bearing.
    """
    if render_english and render_katakana:
        return result
    out: list[WordAccentResult] = []
    for w in result:
        surface = w.surface
        if (
            not render_english
            and _is_pure_english_surface(surface)
            and not _has_kana(w.furigana)
        ):
            out.append(
                WordAccentResult(
                    surface=surface,
                    furigana="",
                    accent=[],
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
        elif not render_katakana and _is_pure_katakana_surface(surface):
            out.append(
                WordAccentResult(
                    surface=surface,
                    furigana="",
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
        else:
            out.append(w)
    return out


def suppress_particle_furigana(
    result: list[WordAccentResult],
) -> list[WordAccentResult]:
    """Clear the top-level `furigana` on 助詞 (particle) tokens.

    Particles are always hiragana (に, を, と, や, で, は, が, の …),
    so the `furigana` field is just identical to the surface.
    Rendering ruby on top of a hiragana char duplicates the glyph and
    crowds out the pitch overlay clients draw against the surface —
    visible symptom was "particles look like they don't have accent".
    We clear `furigana` but keep `accent` so the per-mora pitch contour
    still renders; each AccentInfo carries its own per-mora furigana.

    Scoped to `pos == "助詞"` deliberately. 助動詞 (です, ます), 代名詞
    (これ, それ), and other pure-hiragana parts of speech are
    untouched: their kanji-less surface is incidental, not a signal
    that ruby is redundant for the learner.
    """
    out: list[WordAccentResult] = []
    for w in result:
        if w.pos == "助詞":
            out.append(
                WordAccentResult(
                    surface=w.surface,
                    furigana="",
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
        else:
            out.append(w)
    return out


# --- okurigana subword split -------------------------------------------------


def _is_kanji_char(c: str) -> bool:
    """CJK Unified Ideographs (BMP + Ext A/B). Matches the kanji we care
    about for okurigana segmentation — fugashi only ever emits chars
    inside these ranges as kanji."""
    cp = ord(c)
    return (
        0x3400 <= cp <= 0x4DBF  # Ext A
        or 0x4E00 <= cp <= 0x9FFF  # Unified
        or 0x20000 <= cp <= 0x2A6DF  # Ext B
    )


def _is_kana_char(c: str) -> bool:
    """Hiragana + katakana block, including small kana, chōonpu, and
    nakaguro (`・`)."""
    cp = ord(c)
    return 0x3040 <= cp <= 0x309F or 0x30A0 <= cp <= 0x30FF


def _segment_kanji_kana(surface: str, furigana: str) -> list[WordResult] | None:
    """Split a mixed kanji+kana surface against its hiragana reading.

    Returns one `WordResult` per surface segment: kanji runs carry their
    furigana slice, kana runs carry `furigana=""`. Returns None when the
    surface contains chars that are neither kanji nor kana (numerals,
    symbols, ASCII — we don't try to segment those), or when the kana
    in the surface can't be aligned to the reading (irregular reading;
    fall back to emitting no subword).

    Example: `聞き分け` + `ききわけ` → [{聞|き}, {き|""}, {分|わ}, {け|""}].
    """
    if not surface or not furigana:
        return None
    kinds: list[str] = []
    for c in surface:
        if _is_kanji_char(c):
            kinds.append("kanji")
        elif _is_kana_char(c):
            kinds.append("kana")
        else:
            return None
    if "kanji" not in kinds:
        return None

    reading = jaconv.kata2hira(furigana)
    segments: list[WordResult] = []
    s_idx = 0
    r_idx = 0
    n = len(surface)
    while s_idx < n:
        if kinds[s_idx] == "kanji":
            run_start = s_idx
            while s_idx < n and kinds[s_idx] == "kanji":
                s_idx += 1
            kanji_run = surface[run_start:s_idx]
            if s_idx >= n:
                slice_end = len(reading)
            else:
                next_kana = jaconv.kata2hira(surface[s_idx])
                slice_end = reading.find(next_kana, r_idx + 1)
                if slice_end == -1:
                    return None
            kanji_furi = reading[r_idx:slice_end]
            if not kanji_furi:
                return None
            segments.append(WordResult(surface=kanji_run, furigana=kanji_furi))
            r_idx = slice_end
        else:
            surf_h = jaconv.kata2hira(surface[s_idx])
            if r_idx >= len(reading) or reading[r_idx] != surf_h:
                return None
            segments.append(WordResult(surface=surface[s_idx], furigana=""))
            s_idx += 1
            r_idx += 1
    if r_idx != len(reading):
        return None
    return segments


def split_okurigana(
    result: list[WordAccentResult],
) -> list[WordAccentResult]:
    """Populate `subword` for tokens with mixed kanji + kana surfaces.

    Top-level `surface` / `furigana` / `accent` stay untouched — the
    `subword` list adds a per-segment view so clients that want the
    `聞|き|分|け` reading style can render furigana only over the kanji
    portions. Tokens whose surface is pure kanji, pure kana, or contains
    non-CJK chars are left as-is. Irregular readings that can't be
    aligned against the surface kana also skip the split — we preserve
    the flat representation rather than emit garbled segments.
    """
    out: list[WordAccentResult] = []
    for w in result:
        segments = _segment_kanji_kana(w.surface, w.furigana)
        if not segments or len(segments) <= 1:
            out.append(w)
            continue
        out.append(
            WordAccentResult(
                surface=w.surface,
                furigana=w.furigana,
                accent=w.accent,
                subword=segments,
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
    return out


# --- furigana script conversion ----------------------------------------------


_SCRIPT_LITERALS = ("hiragana", "katakana", "romaji")


def _convert_one(s: str, script: str) -> str:
    """Convert a kana string to the target script.

    `jaconv.kata2hira` normalises mixed-script input first so per-mora
    furigana that OJAD echoed back as katakana (`ラ`, `イ` for ライター)
    end up matching the script the caller asked for, instead of leaking
    through verbatim.
    """
    if not s:
        return s
    hira = jaconv.kata2hira(s)
    if script == "hiragana":
        return hira
    if script == "katakana":
        return jaconv.hira2kata(hira)
    if script == "romaji":
        return jaconv.kana2alphabet(hira)
    return s


def convert_furigana_script(
    result: list[WordAccentResult],
    script: str,
) -> list[WordAccentResult]:
    """Rewrite every furigana field to the requested script.

    Internal alignment / accent prediction stays hiragana; this is the
    last response-shape pass before serialisation. Covers top-level
    `furigana`, every `AccentInfo.furigana`, and every `subword[].furigana`.

    Even the default `hiragana` runs through here so per-mora furigana
    that OJAD echoed back as katakana (e.g. `ラ`/`イ` morae on
    ライター) gets normalised to hiragana — without this pass, the
    per-mora script was inconsistent between katakana-surface and
    kanji-surface tokens.

    `accent[].length` is recomputed against the converted string so
    clients that draw the pitch overlay against the rendered ruby get
    a correct width — `len("shi") = 3` for the mora `し` in romaji mode
    is intentional (the ruler widens accordingly).
    """
    if script not in _SCRIPT_LITERALS:
        return result
    out: list[WordAccentResult] = []
    for w in result:
        new_accent: list[AccentInfo] = []
        for a in w.accent:
            new_furi = _convert_one(a.furigana, script)
            new_accent.append(
                AccentInfo(
                    furigana=new_furi,
                    accent_marking_type=a.accent_marking_type,
                    length=len(new_furi) if new_furi else a.length,
                )
            )
        new_subword: list[WordResult] = []
        for s in w.subword:
            new_subword.append(
                WordResult(
                    surface=s.surface,
                    furigana=_convert_one(s.furigana, script),
                    subword=s.subword,
                )
            )
        out.append(
            WordAccentResult(
                surface=w.surface,
                furigana=_convert_one(w.furigana, script),
                accent=new_accent,
                subword=new_subword,
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
    return out
