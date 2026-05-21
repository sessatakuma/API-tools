"""Predefined regex override layer + POS-driven accent patches.

Two layers run after the local fugashi tokeniser + OJAD alignment:

(1) **Regex overrides** — `apply_furigana_overrides` (before OJAD align)
    and `apply_accent_overrides` (after align). Each entry is a regex
    against the concatenated surface text plus the replacement tokens
    that should appear instead. The same overrides are applied a second
    time after OJAD alignment, replacing both furigana and accent in
    one go. Patterns accept half-width, full-width, and kanji-numeral
    variants of the same surface, so "3月5日(土)" / "３月５日（土）" /
    "三月五日（土）" all trigger the same overrides.

(2) **POS-driven patches** — `apply_accent_patches`, runs after the
    full-span overrides. Inspects each token's MA-provided POS metadata
    (`pos`, `pos1`, `base`, `conjugation_form`) and may rewrite its
    trailing accent without touching the verb stem on the previous
    token. Currently covers polite ます and desiderative たい.

If a regex match doesn't fall on token boundaries we log a warning and
leave the match alone — the original result passes through unchanged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from api.accent.models import AccentInfo, WordAccentResult, WordResult
from api.accent.user_patches import USER_PATCHES

logger = logging.getLogger("api")


@dataclass(frozen=True)
class ReplacementToken:
    """One token in an override's replacement list.

    `surface=None` means: inherit the surface from the matched substring (full
    text for a single-token replacement, or per-position when the replacement
    count equals the match length — see _resolve_surface).

    `furigana=None` means: echo the resolved surface (useful for non-kana
    positions like brackets, mirroring the tokeniser's own fallback in
    `tag_local` in `tokenizer.py`).

    `accent` is a per-moji sequence of (kana, accent_marking_type) — 0=LOW,
    1=HIGH plateau, 2=FALL kernel — matching the existing AccentInfo schema.
    Empty tuple → fall back to a single accent_marking_type=0 entry covering
    the whole furigana.
    """

    furigana: str | None = None
    surface: str | None = None
    accent: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class FuriganaOverride:
    pattern: re.Pattern[str]
    replacements: tuple[ReplacementToken, ...]
    description: str = ""
    # When set, the regex match is additionally filtered: only fires if
    # `pos_match(tokens_in_match_span)` returns True. None preserves the
    # original surface-only matching used by every existing override. The
    # span is `list[WordResult]` for furigana overrides, `list[WordAccentResult]`
    # for accent overrides — both expose the same POS attributes after the
    # local-tokeniser migration.
    pos_match: Callable[[list[Any]], bool] | None = field(default=None)


# accent_marking_type values (mirror AccentInfo)
_NONE, _HEIBAN, _FALL = 0, 1, 2

# Numeric / kanji-numeral class used in lookbehind & lookahead so partial
# matches don't fire (e.g. avoid "11日" → "1日" or "二十五日" → "五日").
_DIGIT_CLASS = r"\d一二三四五六七八九十百千"
_NOT_NUM_BEHIND = rf"(?<![{_DIGIT_CLASS}])"
_NOT_NUM_AHEAD = rf"(?![{_DIGIT_CLASS}])"


def _moji_seq(text: str, t: int = _HEIBAN) -> tuple[tuple[str, int], ...]:
    return tuple((c, t) for c in text)


# Small kana that attach to the preceding mora (so じゅ counts as one
# mora rather than じ + ゅ). Both hiragana and katakana variants are
# included so the splitter works regardless of script.
_SMALL_KANA = frozenset("ャュョァィゥェォヮゎぁぃぅぇぉゃゅょ")


def _mora_seq(text: str, t: int = _HEIBAN) -> tuple[tuple[str, int], ...]:
    """Split `text` into Japanese morae and emit an accent entry per mora.

    Used by patches whose furigana contains 拗音 — `_moji_seq` would
    split `じゅ` into two entries `(じ, t)` `(ゅ, t)`, breaking client-
    side mora counts. `_mora_seq("さんじゅう")` → `(さ,t)(ん,t)(じゅ,t)(う,t)`.
    """
    if not text:
        return ()
    morae: list[str] = []
    for c in text:
        if c in _SMALL_KANA and morae:
            morae[-1] += c
        else:
            morae.append(c)
    return tuple((m, t) for m in morae)


def _atamadaka_seq(text: str) -> tuple[tuple[str, int], ...]:
    # Atamadaka (頭高): mora 1 carries the FALL kernel, every later mora is
    # LOW. Marking the tail as _HEIBAN renders a high plateau after the
    # downstep, which contradicts the pitch contour and looks visually
    # wrong in strong-mode renderers.
    if not text:
        return ()
    return ((text[0], _FALL),) + tuple((c, _NONE) for c in text[1:])


# Numeric variant helpers: keep N-prefixed patterns (N日, N日間, N歳) from
# repeating the (arabic, fullwidth, kanji) triple per row.

_FULLWIDTH_TRANS = str.maketrans("0123456789", "０１２３４５６７８９")


def _int_to_kanji(n: int) -> str:
    """Traditional kanji numeral for n (1-99).

    Examples: 1→'一', 10→'十', 14→'十四', 20→'二十', 24→'二十四', 31→'三十一'.
    """
    if not 1 <= n <= 99:
        raise ValueError(f"_int_to_kanji supports 1-99, got {n}")
    digits = "〇一二三四五六七八九"
    tens, ones = divmod(n, 10)
    if tens == 0:
        return digits[ones]
    tens_part = "十" if tens == 1 else digits[tens] + "十"
    return tens_part + (digits[ones] if ones else "")


def _numeric_pattern(n: int) -> str:
    """Regex alternation matching n in arabic / full-width / traditional-kanji.

    Alternatives are emitted longest-first so multi-char kanji forms like
    '二十四' aren't shadowed by their numeric-form prefixes.
    """
    arabic = str(n)
    fullwidth = arabic.translate(_FULLWIDTH_TRANS)
    kanji = _int_to_kanji(n)
    variants = sorted({arabic, fullwidth, kanji}, key=len, reverse=True)
    return "(?:" + "|".join(variants) + ")"


def _day_of_week_overrides() -> list[FuriganaOverride]:
    readings: list[tuple[str, str]] = [
        ("月", "げつ"),
        ("火", "か"),
        ("水", "すい"),
        ("木", "もく"),
        ("金", "きん"),
        ("土", "ど"),
        ("日", "にち"),
    ]
    out: list[FuriganaOverride] = []
    for kanji, reading in readings:
        # surface=None on all three lets _resolve_surface inherit per-position
        # from the matched substring, preserving half-width vs full-width
        # brackets in the input. furigana=None on the brackets echoes their
        # surface (the tokeniser also returns the bracket char as its own
        # furigana).
        out.append(
            FuriganaOverride(
                pattern=re.compile(rf"[((]{kanji}[))]"),
                replacements=(
                    ReplacementToken(),  # left bracket: both inherit
                    ReplacementToken(furigana=reading, accent=_moji_seq(reading)),
                    ReplacementToken(),  # right bracket: both inherit
                ),
                description=f"曜日 ({kanji})",
            )
        )
    return out


def _date_overrides() -> list[FuriganaOverride]:
    # 1-10日, 14日, 20日, 24日 are irregular (ついたち, ふつか, ..., はつか).
    # 11-31日 are regular (じゅういちにち etc.) but the numeric token's
    # reading is often the literal digit; align_accent also frequently
    # misaligns numeric spans. Listing every day-of-month here gives both
    # endpoints a deterministic reading + accent for any date.
    #
    # Only 1日 (ついたち) is atamadaka — the rest sit in heiban-style.
    readings: list[tuple[int, str, tuple[tuple[str, int], ...]]] = [
        (1, "ついたち", _atamadaka_seq("ついたち")),
        (2, "ふつか", _moji_seq("ふつか")),
        (3, "みっか", _moji_seq("みっか")),
        (4, "よっか", _moji_seq("よっか")),
        (5, "いつか", _moji_seq("いつか")),
        (6, "むいか", _moji_seq("むいか")),
        (7, "なのか", _moji_seq("なのか")),
        (8, "ようか", _moji_seq("ようか")),
        (9, "ここのか", _moji_seq("ここのか")),
        (10, "とおか", _moji_seq("とおか")),
        (11, "じゅういちにち", _moji_seq("じゅういちにち")),
        (12, "じゅうににち", _moji_seq("じゅうににち")),
        (13, "じゅうさんにち", _moji_seq("じゅうさんにち")),
        (14, "じゅうよっか", _moji_seq("じゅうよっか")),
        (15, "じゅうごにち", _moji_seq("じゅうごにち")),
        (16, "じゅうろくにち", _moji_seq("じゅうろくにち")),
        (17, "じゅうしちにち", _moji_seq("じゅうしちにち")),
        (18, "じゅうはちにち", _moji_seq("じゅうはちにち")),
        (19, "じゅうくにち", _moji_seq("じゅうくにち")),
        (20, "はつか", _moji_seq("はつか")),
        (21, "にじゅういちにち", _moji_seq("にじゅういちにち")),
        (22, "にじゅうににち", _moji_seq("にじゅうににち")),
        (23, "にじゅうさんにち", _moji_seq("にじゅうさんにち")),
        (24, "にじゅうよっか", _moji_seq("にじゅうよっか")),
        (25, "にじゅうごにち", _moji_seq("にじゅうごにち")),
        (26, "にじゅうろくにち", _moji_seq("にじゅうろくにち")),
        (27, "にじゅうしちにち", _moji_seq("にじゅうしちにち")),
        (28, "にじゅうはちにち", _moji_seq("にじゅうはちにち")),
        (29, "にじゅうくにち", _moji_seq("にじゅうくにち")),
        (30, "さんじゅうにち", _moji_seq("さんじゅうにち")),
        (31, "さんじゅういちにち", _moji_seq("さんじゅういちにち")),
    ]
    return [
        FuriganaOverride(
            pattern=re.compile(
                rf"{_NOT_NUM_BEHIND}{_numeric_pattern(n)}日{_NOT_NUM_AHEAD}"
            ),
            replacements=(ReplacementToken(furigana=furigana, accent=accent),),
            description=f"特殊日期 {n}日",
        )
        for n, furigana, accent in readings
    ]


def _duration_overrides() -> list[FuriganaOverride]:
    """N日間 (counter for days as a duration) expansions.

    Bare numeric tokens get no useful reading from the tokeniser, so the
    furigana endpoint surfaces unreadable output like `1にちかん`. We
    override the full N日間 span so the user sees a complete reading.

    Most readings are the existing date reading + `かん`, with two
    intentional deviations:
    - `1日間` → `いちにちかん` (NOT `ついたちかん` — the 1st-of-month
      reading is impossible when 1日 is a duration).
    - `7日間` → `しちにちかん` (preferred in modern technical writing
      over the older `なのかかん`).
    """
    readings: list[tuple[int, str]] = [
        (1, "いちにちかん"),
        (2, "ふつかかん"),
        (3, "みっかかん"),
        (4, "よっかかん"),
        (5, "いつかかん"),
        (6, "むいかかん"),
        (7, "しちにちかん"),
        (8, "ようかかん"),
        (9, "ここのかかん"),
        (10, "とおかかん"),
        (11, "じゅういちにちかん"),
        (12, "じゅうににちかん"),
        (13, "じゅうさんにちかん"),
        (14, "じゅうよっかかん"),
        (15, "じゅうごにちかん"),
        (16, "じゅうろくにちかん"),
        (17, "じゅうしちにちかん"),
        (18, "じゅうはちにちかん"),
        (19, "じゅうくにちかん"),
        (20, "はつかかん"),
        (21, "にじゅういちにちかん"),
        (22, "にじゅうににちかん"),
        (23, "にじゅうさんにちかん"),
        (24, "にじゅうよっかかん"),
        (25, "にじゅうごにちかん"),
        (26, "にじゅうろくにちかん"),
        (27, "にじゅうしちにちかん"),
        (28, "にじゅうはちにちかん"),
        (29, "にじゅうくにちかん"),
        (30, "さんじゅうにちかん"),
        (31, "さんじゅういちにちかん"),
    ]
    return [
        FuriganaOverride(
            pattern=re.compile(rf"{_NOT_NUM_BEHIND}{_numeric_pattern(n)}日間"),
            replacements=(
                ReplacementToken(furigana=furigana, accent=_moji_seq(furigana)),
            ),
            description=f"期間 {n}日間",
        )
        for n, furigana in readings
    ]


def _age_overrides() -> list[FuriganaOverride]:
    """Irregular age readings.

    20歳 / 二十歳 (and the casual 才 variant) → はたち, not the regular
    にじゅっさい. Only 20 is irregular for ages; the rest are systematic
    so we don't need a 1-99 table here.
    """
    return [
        FuriganaOverride(
            pattern=re.compile(
                rf"{_NOT_NUM_BEHIND}{_numeric_pattern(20)}[歳才]{_NOT_NUM_AHEAD}"
            ),
            replacements=(
                ReplacementToken(furigana="はたち", accent=_atamadaka_seq("はたち")),
            ),
            description="20歳 → はたち",
        ),
    ]


def _user_patch_overrides() -> list[FuriganaOverride]:
    """Compile `user_patches.USER_PATCHES` into FuriganaOverride entries.

    Each dict value is `((segment_surface, segment_furigana), ...)`;
    the segment surfaces must concatenate to the key. Heiban accent
    is assumed for the whole reading — patches needing fancier accent
    contours should be written as full FuriganaOverride entries here
    instead of going through this helper.
    """
    out: list[FuriganaOverride] = []
    for literal_text, segments in USER_PATCHES.items():
        seg_sum = sum(len(s) for s, _ in segments)
        if seg_sum != len(literal_text):
            logger.warning(
                "USER_PATCHES[%r]: segment surfaces sum to %d chars but key "
                "is %d chars — skipping (boundary mismatch will not apply).",
                literal_text,
                seg_sum,
                len(literal_text),
            )
            continue
        out.append(
            FuriganaOverride(
                pattern=re.compile(re.escape(literal_text)),
                replacements=tuple(
                    ReplacementToken(
                        surface=surf,
                        furigana=furi,
                        accent=_mora_seq(furi),
                    )
                    for surf, furi in segments
                ),
                description=f"user patch: {literal_text}",
            )
        )
    return out


# Order matters: _collect_matches breaks ties on (start, -length) and
# discards anything overlapping an earlier pick. `N日間` (3-4 chars) is
# strictly longer than `N日` at the same start, so duration entries
# automatically win over date entries for the same N when 間 follows.
# User patches go last so they can override the built-in date/duration
# entries when the user explicitly lists an overlapping key.
OVERRIDES: list[FuriganaOverride] = (
    _day_of_week_overrides()
    + _duration_overrides()
    + _date_overrides()
    + _age_overrides()
    + _user_patch_overrides()
)


# ---------- Apply algorithm (shared between furigana & accent variants) ----------


@dataclass
class _Match:
    start: int
    end: int
    override: FuriganaOverride


def _collect_matches(text: str) -> list[_Match]:
    """Run every override's regex and return non-overlapping matches.

    Earlier start wins; ties broken by longer match.
    """
    raw: list[_Match] = []
    for ov in OVERRIDES:
        for rm in ov.pattern.finditer(text):
            raw.append(_Match(start=rm.start(), end=rm.end(), override=ov))
    raw.sort(key=lambda x: (x.start, -(x.end - x.start)))
    chosen: list[_Match] = []
    last_end = 0
    for cm in raw:
        if cm.start < last_end:
            continue
        chosen.append(cm)
        last_end = cm.end
    return chosen


def _resolve_surface(
    replacements: tuple[ReplacementToken, ...],
    position: int,
    matched_text: str,
) -> str:
    """Pick the surface for a replacement token.

    - explicit `surface` always wins;
    - single-token replacement with `surface=None` → use full matched substring;
    - multi-token replacement with `surface=None`, where the number of tokens
      equals the matched span length, → inherit per-position from the matched
      substring (this preserves half/full-width brackets, kanji vs arabic
      digits, etc. that the regex character classes accepted);
    - otherwise → warn and fall back to the furigana string.
    """
    rt = replacements[position]
    if rt.surface is not None:
        return rt.surface
    n = len(replacements)
    if n == 1:
        return matched_text
    if n == len(matched_text):
        return matched_text[position]
    fallback = rt.furigana if rt.furigana is not None else matched_text
    logger.warning(
        "Override replacement at position %d missing explicit `surface` and "
        "cannot inherit (n_replacements=%d, match_len=%d); falling back to %r",
        position,
        n,
        len(matched_text),
        fallback,
    )
    return fallback


T = TypeVar("T")


def _apply(
    words: list[T],
    surface_of: Callable[[T], str],
    build: Callable[[tuple[ReplacementToken, ...], int, str], T],
) -> list[T]:
    if not words:
        return list(words)

    surfaces = [surface_of(w) for w in words]
    full_text = "".join(surfaces)

    # offsets[i] = char position of token i's start; offsets[-1] = total length
    offsets: list[int] = [0]
    for s in surfaces:
        offsets.append(offsets[-1] + len(s))
    boundary_to_index = {off: i for i, off in enumerate(offsets)}

    matches = _collect_matches(full_text)
    if not matches:
        return list(words)

    out: list[T] = []
    cursor = 0
    for m in matches:
        start_idx = boundary_to_index.get(m.start)
        end_idx = boundary_to_index.get(m.end)
        if start_idx is None or end_idx is None:
            logger.warning(
                "Override %r match at [%d, %d) does not align with token "
                "boundaries — skipping",
                m.override.description or m.override.pattern.pattern,
                m.start,
                m.end,
            )
            continue
        if start_idx < cursor:
            # earlier non-overlapping match already consumed this region
            continue
        if m.override.pos_match is not None:
            token_span = list(words[start_idx:end_idx])
            if not m.override.pos_match(token_span):
                # POS check rejected — pretend this match never happened.
                # Earlier-start, lower-priority matches (already filtered
                # out by _collect_matches) don't get a second chance, but
                # subsequent non-overlapping matches still do.
                logger.debug(
                    "Override %r matched at [%d, %d) but pos_match rejected",
                    m.override.description or m.override.pattern.pattern,
                    m.start,
                    m.end,
                )
                continue
        while cursor < start_idx:
            out.append(words[cursor])
            cursor += 1
        matched_text = full_text[m.start : m.end]
        replacements = m.override.replacements
        for idx in range(len(replacements)):
            out.append(build(replacements, idx, matched_text))
        cursor = end_idx
    while cursor < len(words):
        out.append(words[cursor])
        cursor += 1
    return out


def _resolve_furigana(rt: ReplacementToken, surface: str) -> str:
    return rt.furigana if rt.furigana is not None else surface


def apply_furigana_overrides(words: list[WordResult]) -> list[WordResult]:
    """Post-process tokeniser results, replacing matched spans."""

    def build(
        repls: tuple[ReplacementToken, ...], pos: int, matched: str
    ) -> WordResult:
        rt = repls[pos]
        surface = _resolve_surface(repls, pos, matched)
        return WordResult(surface=surface, furigana=_resolve_furigana(rt, surface))

    return _apply(words, lambda w: w.surface, build)


def apply_accent_overrides(
    words: list[WordAccentResult],
) -> list[WordAccentResult]:
    """Post-process accent-aligned results, replacing both furigana and accent."""

    def build(
        repls: tuple[ReplacementToken, ...], pos: int, matched: str
    ) -> WordAccentResult:
        rt = repls[pos]
        surface = _resolve_surface(repls, pos, matched)
        furigana = _resolve_furigana(rt, surface)
        if rt.accent:
            accent = [
                AccentInfo(furigana=moji, accent_marking_type=t, length=len(moji))
                for moji, t in rt.accent
            ]
        else:
            accent = [
                AccentInfo(
                    furigana=furigana,
                    accent_marking_type=_NONE,
                    length=len(furigana),
                )
            ]
        return WordAccentResult(surface=surface, furigana=furigana, accent=accent)

    return _apply(words, lambda w: w.surface, build)


# ---------- POS-driven in-place accent patches ----------
#
# Distinct from `apply_accent_overrides` above (which does full-span
# replacement). Each rule inspects a single token's MA-provided POS metadata
# (`pos`, `pos1`, `base`, `conjugation_form`) and may rewrite its trailing
# accent — without touching the verb stem on the previous token.
#
# Rules are defined as functions to `WordAccentResult → WordAccentResult |
# None`. Returning None means "no change"; returning a new object swaps the
# token in place. The patch pipeline is small enough that we don't bother
# with a generic rule-table indirection.


# Empty initial set — the Phase 1 spike found zero false-positive cases
# where MA mis-tags a 五段動詞 as 接尾辞. Populate as real cases arrive.
_PATCH_EXCEPTIONS: frozenset[str] = frozenset()


# Conjugation forms where the first-mora FALL rule applies. Polite ます
# behaves like an atamadaka 2-mora foot in 終止形 (ます → ま↓す) and in
# 連用形 (まし[た] → ま↓し-た). It does NOT apply in 未然形 — in ません,
# the kernel falls on せ before the final ん, not on ま. OJAD already
# predicts ません correctly, and our patch would un-fix it.
_FIRST_MORA_FALL_FORMS = ("終止形", "連用形")


def _patches_first_mora(conjugation_form: str | None) -> bool:
    if not conjugation_form:
        return False
    return any(conjugation_form.startswith(p) for p in _FIRST_MORA_FALL_FORMS)


def _is_masu_auxiliary(token: WordAccentResult) -> bool:
    """Self-check: pos × conjugation_type × base × surface × conjugation_form.

    UniDic tags polite-form ます as pos=助動詞 with
    conjugation_type=助動詞-マス and base=ます (regardless of which
    conjugated form: ます / まし / ませ all share base=ます). The
    conjugation_form check narrows the patch to forms where the kernel
    really is on the first mora — see `_FIRST_MORA_FALL_FORMS` above.

    Failure modes filtered out:
    - 五段動詞 励ます: pos=動詞 → fails first axis.
    - UniDic-mistagged 升 (surname, reading=ます): surface=升 (kanji)
      doesn't start with ま → fails surface prefix.
    - ませ in ません (cform=未然形-一般): rejected by _patches_first_mora.
    """
    if token.surface in _PATCH_EXCEPTIONS:
        return False
    return (
        token.pos == "助動詞"
        and token.conjugation_type == "助動詞-マス"
        and token.base == "ます"
        and token.surface.startswith("ま")
        and token.furigana.startswith("ま")
        and _patches_first_mora(token.conjugation_form)
    )


def _is_tai_auxiliary(token: WordAccentResult) -> bool:
    """Self-check for the desiderative たい suffix.

    UniDic tags たい / たく / たかった with pos=助動詞,
    conjugation_type=助動詞-タイ, base=たい. All known conjugations have
    the kernel on the first mora (た), so the conjugation_form gate
    accepts all 終止形 / 連用形 variants.
    """
    if token.surface in _PATCH_EXCEPTIONS:
        return False
    return (
        token.pos == "助動詞"
        and token.conjugation_type == "助動詞-タイ"
        and token.base == "たい"
        and token.surface.startswith("た")
        and token.furigana.startswith("た")
        and _patches_first_mora(token.conjugation_form)
    )


def _patch_first_mora_fall(token: WordAccentResult) -> WordAccentResult:
    """Return a copy of `token` with accent[0]=FALL, accent[1:]=LOW.

    Mirrors the rule "for ます/たい-family suffixes the accent kernel sits
    on the first mora": the high pitch of the verb stem drops as soon as
    the suffix begins, and the rest of the suffix stays low. Post-FALL
    morae must be LOW (_NONE), not HEIBAN — _HEIBAN renders as a high
    plateau after the downstep, which conflicts with the actual pitch
    contour.
    """
    if not token.accent:
        return token
    new_accent: list[AccentInfo] = []
    for i, a in enumerate(token.accent):
        marking = _FALL if i == 0 else _NONE
        new_accent.append(
            AccentInfo(
                furigana=a.furigana,
                accent_marking_type=marking,
                length=a.length,
            )
        )
    return WordAccentResult(
        surface=token.surface,
        furigana=token.furigana,
        accent=new_accent,
        subword=token.subword,
        base=token.base,
        pos=token.pos,
        pos1=token.pos1,
        conjugation_type=token.conjugation_type,
        conjugation_form=token.conjugation_form,
    )


def apply_accent_patches(
    words: list[WordAccentResult],
) -> list[WordAccentResult]:
    """POS-driven in-place accent patches on aligned MA tokens.

    Idempotent — applying the same patch twice yields the same output. Safe
    to run after `apply_accent_overrides`: tokens that were entirely
    replaced by an override have `pos=None`/`base=None` and the self-check
    predicates therefore reject them.
    """
    out: list[WordAccentResult] = []
    for token in words:
        if _is_masu_auxiliary(token) or _is_tai_auxiliary(token):
            out.append(_patch_first_mora_fall(token))
        else:
            out.append(token)
    return out
