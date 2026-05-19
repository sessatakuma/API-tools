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
    from api.accent_marker import WordAccentResult
    from api.furigana_marker import WordResult

logger = logging.getLogger("api")


@dataclass(frozen=True)
class ReplacementToken:
    """One token in an override's replacement list.

    `surface=None` means: inherit the surface from the matched substring (full
    text for a single-token replacement, or per-position when the replacement
    count equals the match length — see _resolve_surface).

    `furigana=None` means: echo the resolved surface (useful for non-kana
    positions like brackets, mirroring Yahoo's own fallback in
    `furigana_marker.py`).

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
    # (arabic, fullwidth, kanji, furigana, accent)
    entries: list[
        tuple[str, str, str, str, tuple[tuple[str, int], ...]]
    ] = [
        # 1-10日, 14日, 20日, 24日 are irregular (ついたち, ふつか, ..., はつか).
        # 11-31日 are regular (じゅういちにち etc.) but Yahoo often returns
        # the literal digits as their own "furigana" for numeric tokens; the
        # accent endpoint's align_accent also frequently misaligns numeric
        # spans. Listing every day-of-month here gives both endpoints a
        # deterministic reading + accent for any date.
        ("1", "１", "一", "ついたち", _atamadaka_seq("ついたち")),
        ("2", "２", "二", "ふつか", _moji_seq("ふつか")),
        ("3", "３", "三", "みっか", _moji_seq("みっか")),
        ("4", "４", "四", "よっか", _moji_seq("よっか")),
        ("5", "５", "五", "いつか", _moji_seq("いつか")),
        ("6", "６", "六", "むいか", _moji_seq("むいか")),
        ("7", "７", "七", "なのか", _moji_seq("なのか")),
        ("8", "８", "八", "ようか", _moji_seq("ようか")),
        ("9", "９", "九", "ここのか", _moji_seq("ここのか")),
        ("10", "１０", "十", "とおか", _moji_seq("とおか")),
        ("11", "１１", "十一", "じゅういちにち", _moji_seq("じゅういちにち")),
        ("12", "１２", "十二", "じゅうににち", _moji_seq("じゅうににち")),
        ("13", "１３", "十三", "じゅうさんにち", _moji_seq("じゅうさんにち")),
        ("14", "１４", "十四", "じゅうよっか", _moji_seq("じゅうよっか")),
        ("15", "１５", "十五", "じゅうごにち", _moji_seq("じゅうごにち")),
        ("16", "１６", "十六", "じゅうろくにち", _moji_seq("じゅうろくにち")),
        ("17", "１７", "十七", "じゅうしちにち", _moji_seq("じゅうしちにち")),
        ("18", "１８", "十八", "じゅうはちにち", _moji_seq("じゅうはちにち")),
        ("19", "１９", "十九", "じゅうくにち", _moji_seq("じゅうくにち")),
        ("20", "２０", "二十", "はつか", _moji_seq("はつか")),
        ("21", "２１", "二十一", "にじゅういちにち", _moji_seq("にじゅういちにち")),
        ("22", "２２", "二十二", "にじゅうににち", _moji_seq("にじゅうににち")),
        ("23", "２３", "二十三", "にじゅうさんにち", _moji_seq("にじゅうさんにち")),
        ("24", "２４", "二十四", "にじゅうよっか", _moji_seq("にじゅうよっか")),
        ("25", "２５", "二十五", "にじゅうごにち", _moji_seq("にじゅうごにち")),
        ("26", "２６", "二十六", "にじゅうろくにち", _moji_seq("にじゅうろくにち")),
        ("27", "２７", "二十七", "にじゅうしちにち", _moji_seq("にじゅうしちにち")),
        ("28", "２８", "二十八", "にじゅうはちにち", _moji_seq("にじゅうはちにち")),
        ("29", "２９", "二十九", "にじゅうくにち", _moji_seq("にじゅうくにち")),
        ("30", "３０", "三十", "さんじゅうにち", _moji_seq("さんじゅうにち")),
        ("31", "３１", "三十一", "さんじゅういちにち", _moji_seq("さんじゅういちにち")),
    ]
    out: list[FuriganaOverride] = []
    for arabic, fullwidth, kanji, furigana, accent in entries:
        # Longest alternatives first so "二十四" doesn't get shadowed by "二".
        pattern = re.compile(
            rf"{_NOT_NUM_BEHIND}(?:{kanji}|{fullwidth}|{arabic})日{_NOT_NUM_AHEAD}"
        )
        out.append(
            FuriganaOverride(
                pattern=pattern,
                replacements=(
                    ReplacementToken(furigana=furigana, accent=accent),
                ),
                description=f"特殊日期 {arabic}日",
            )
        )
    return out


OVERRIDES: list[FuriganaOverride] = (
    _day_of_week_overrides() + _date_overrides()
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
    from api.furigana_marker import WordResult as _WordResult

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
