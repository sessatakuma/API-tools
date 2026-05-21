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

from api.accent.models import AccentInfo, WordAccentResult
from api.accent.preprocess import (
    NUMERIC_PATTERN,
    READABLE_COMPOUND_RE,
)


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
    and every char is an ASCII letter or digit.

    Mixed-with-digit tokens like `iPhone7` count as English. Pure-numeric
    tokens are excluded (they get their reading from OJAD).
    """
    if not surface:
        return False
    has_letter = False
    for c in surface:
        if "a" <= c <= "z" or "A" <= c <= "Z":
            has_letter = True
        elif "0" <= c <= "9":
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
_HEIBAN_FLATTEN_PARTICLES = frozenset({"の", "な", "は", "が"})


def flatten_heiban_particle_accent(
    result: list[WordAccentResult],
) -> list[WordAccentResult]:
    """Zero out the pitch on の / な / は / が following a 平板調 word.

    After a heiban noun (学校, 富士山, 元気 …), OJAD assigns HIGH (1) to
    the trailing particle to maintain the high plateau. Visually that
    paints the particle with a HIGH overlay that adds noise without
    new contour information. The caller prefers LOW (0) instead.

    Restricted to の, な, は, が by design — the four particles where
    the visual noise was reported. Other particles (に, を, へ, と, で
    …) keep their OJAD-derived pitch.
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
        prev = w
    return out


def apply_furigana_toggles(
    result: list[WordAccentResult],
    render_english: bool,
    render_katakana: bool,
) -> list[WordAccentResult]:
    """Suppress furigana on English / katakana tokens when their toggle is off.

    English (toggle off): clear both furigana AND accent — foreign
    tokens carry no meaningful Japanese pitch contour.

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
        if not render_english and _is_pure_english_surface(surface):
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
