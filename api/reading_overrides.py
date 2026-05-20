"""
Predefined regex override layer for Yahoo Furigana / Suzuki-kun (OJAD) accent
results.

Yahoo's furigana service is context-blind — it gets common date and weekday-
bracket readings wrong (e.g. "5日" → にち instead of いつか, "(土)" → つち
instead of ど). We post-process Yahoo's tokenised response with a list of
`FuriganaOverride` entries: each entry is a regex against the concatenated
surface text, plus the replacement tokens that should appear instead.

The same overrides are applied a second time after OJAD alignment, replacing
both furigana and accent in one go.

Patterns are written with character classes that accept half-width, full-width,
and kanji-numeral variants of the same surface, so "3月5日(土)" / "３月５日（土）"
/ "三月五日（土）" all trigger the same overrides.

If a match doesn't fall on Yahoo's token boundaries we log a warning and leave
the match alone — Yahoo's result passes through unchanged.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, TypeVar

if TYPE_CHECKING:
    from api.accent_marker import WordAccentResult, WordResult

logger = logging.getLogger("api")


@dataclass(frozen=True)
class ReplacementToken:
    """One token in an override's replacement list.

    `surface=None` means: inherit the surface from the matched substring (full
    text for a single-token replacement, or per-position when the replacement
    count equals the match length — see _resolve_surface).

    `furigana=None` means: echo the resolved surface (useful for non-kana
    positions like brackets, mirroring Yahoo's own fallback in
    `_fetch_yahoo_raw` in `accent_marker.py`).

    `accent` is a per-moji sequence of (kana, accent_marking_type) — 0=none,
    1=heiban, 2=fall — matching the existing AccentInfo schema. Empty tuple →
    fall back to a single accent_marking_type=0 entry covering the whole
    furigana.
    """

    furigana: str | None = None
    surface: str | None = None
    accent: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class FuriganaOverride:
    pattern: re.Pattern[str]
    replacements: tuple[ReplacementToken, ...]
    description: str = ""


# accent_marking_type values (mirror AccentInfo)
_NONE, _HEIBAN, _FALL = 0, 1, 2

# Numeric / kanji-numeral class used in lookbehind & lookahead so partial
# matches don't fire (e.g. avoid "11日" → "1日" or "二十五日" → "五日").
_DIGIT_CLASS = r"\d一二三四五六七八九十百千"
_NOT_NUM_BEHIND = rf"(?<![{_DIGIT_CLASS}])"
_NOT_NUM_AHEAD = rf"(?![{_DIGIT_CLASS}])"


def _moji_seq(text: str, t: int = _HEIBAN) -> tuple[tuple[str, int], ...]:
    return tuple((c, t) for c in text)


def _atamadaka_seq(text: str) -> tuple[tuple[str, int], ...]:
    if not text:
        return ()
    return ((text[0], _FALL),) + tuple((c, _HEIBAN) for c in text[1:])


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
        # surface (Yahoo also returns the bracket char as its own furigana).
        out.append(
            FuriganaOverride(
                pattern=re.compile(rf"[(（]{kanji}[)）]"),
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
    # 11-31日 are regular (じゅういちにち etc.) but Yahoo often returns the
    # literal digits as their own "furigana" for numeric tokens; the accent
    # endpoint's align_accent also frequently misaligns numeric spans.
    # Listing every day-of-month here gives both endpoints a deterministic
    # reading + accent for any date.
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
            replacements=(
                ReplacementToken(furigana=furigana, accent=accent),
            ),
            description=f"特殊日期 {n}日",
        )
        for n, furigana, accent in readings
    ]


def _duration_overrides() -> list[FuriganaOverride]:
    """N日間 (counter for days as a duration) expansions.

    Yahoo tokenises e.g. `1日間` as [`1`, `日間`] and gives the numeric
    token no furigana (its result is just the literal digit), so the
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
            pattern=re.compile(
                rf"{_NOT_NUM_BEHIND}{_numeric_pattern(n)}日間"
            ),
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
                ReplacementToken(
                    furigana="はたち", accent=_atamadaka_seq("はたち")
                ),
            ),
            description="20歳 → はたち",
        ),
    ]


# Order matters: _collect_matches breaks ties on (start, -length) and
# discards anything overlapping an earlier pick. `N日間` (3-4 chars) is
# strictly longer than `N日` at the same start, so duration entries
# automatically win over date entries for the same N when 間 follows.
OVERRIDES: list[FuriganaOverride] = (
    _day_of_week_overrides()
    + _duration_overrides()
    + _date_overrides()
    + _age_overrides()
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
                "Override %r match at [%d, %d) does not align with Yahoo token "
                "boundaries — skipping",
                m.override.description or m.override.pattern.pattern,
                m.start,
                m.end,
            )
            continue
        if start_idx < cursor:
            # earlier non-overlapping match already consumed this region
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
    """Post-process Yahoo Furigana results, replacing matched spans."""
    from api.accent_marker import WordResult as _WordResult

    def build(
        repls: tuple[ReplacementToken, ...], pos: int, matched: str
    ) -> WordResult:
        rt = repls[pos]
        surface = _resolve_surface(repls, pos, matched)
        return _WordResult(surface=surface, furigana=_resolve_furigana(rt, surface))

    return _apply(words, lambda w: w.surface, build)


def apply_accent_overrides(
    words: list[WordAccentResult],
) -> list[WordAccentResult]:
    """Post-process accent-aligned results, replacing both furigana and accent."""
    from api.accent_marker import AccentInfo, WordAccentResult as _WordAccentResult

    def build(
        repls: tuple[ReplacementToken, ...], pos: int, matched: str
    ) -> WordAccentResult:
        rt = repls[pos]
        surface = _resolve_surface(repls, pos, matched)
        furigana = _resolve_furigana(rt, surface)
        if rt.accent:
            accent = [
                AccentInfo(
                    furigana=moji, accent_marking_type=t, length=len(moji)
                )
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
        return _WordAccentResult(
            surface=surface, furigana=furigana, accent=accent
        )

    return _apply(words, lambda w: w.surface, build)
