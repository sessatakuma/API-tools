"""Align Yahoo Furigana tokens with OJAD per-mora accent entries.

`align_accent()` builds a Needleman-Wunsch-style DP over
(yahoo_token, ojad_entry) pairs: for every Yahoo token we consider letting
it consume k contiguous OJAD entries (k ∈ [0, K_MAX]), with the per-token
cost depending on token shape (punct / numeric / kana) and edit distance
over rendaku-folded strings for kana tokens. This replaces the older
greedy aligner whose +1 fallback path cascaded a single mismatch into
type-0 fallback for every downstream token.

Alignment itself uses `numeric_pattern` and `is_kana_or_kanji`. The
adjacent `punctuation_marks`, `skip_marks`, and `clean_query` are
carried over from the pre-refactor module as part of the accent
domain vocabulary and are kept here for downstream PRs (see #47).
"""

from __future__ import annotations

import logging
import re
import string
from typing import Any

import jaconv

from api.accent.models import AccentInfo, WordAccentResult, WordResult

logger = logging.getLogger("api")

punctuation_marks = set(
    [
        "。",
        "，",
        "、",
        "・",
        "——",
        "……",
        "—",
        "…",
        "「",
        "」",
        "『",
        "』",
        "（",
        "）",
        "—",
        "、、、",
        "、",
        "————",
        "—",
        "？",
        "！",
        ".",
        ",",
        "：",
        "；",
        "(",
        ")",
        '"',
        "--",
        "-",
        "",
        "/",
        ":",
        ";",
        "！",
        "＂",
        "＃",
        "＄",
        "％",
        "＆",
        "＼",
        "’",
        "（",
        "）",
        "＊",
        "＋",
        "，",
        "－",
        "．",
        "／",
        "：",
        "；",
        "＜",
        "＝",
        "＞",
        "？",
        "＠",
        "［",
        "＼",
        "］",
        "︿",
        "＿",
        "‵",
        "｛",
        "｝",
        "｜",
        "～",
        "“",
        "”",
    ]
).union(set(string.punctuation))
skip_marks = set(string.ascii_lowercase + string.ascii_uppercase)

# accept negative integers and decimals
numeric_pattern = re.compile(r"^-?\d+(\.\d+)?$")


def clean_query(query: str) -> str:
    """Strip ASCII letters from `query`.

    OJAD's CRF parser gives better results when Latin alphabet runs are
    removed before submission. Punctuation is intentionally left in
    place — OJAD relies on it for phrase boundaries.
    """
    return "".join(char for char in query if char not in skip_marks)


def is_kana_or_kanji(char: Any) -> bool:
    """Check whether given character is kana or kanji (ignore half-width kana)."""
    exception_symbols = ["゠", "・", "ー", "ヽ", "ヾ", "ヿ"]
    if char in exception_symbols:
        # '゠', '・', 'ー', 'ヽ', 'ヾ', 'ヿ' which should be regarded as punctuation
        return False
    kana = range(0x3040, 0x30FF + 1)
    kanji = range(0x4E00, 0x9FFF + 1)
    if ord(char) in kana or ord(char) in kanji:
        return True
    return False


# Yahoo returns "dictionary form" furigana (no rendaku), while OJAD returns the
# actually pronounced kana (with rendaku/sequential-voicing applied). When
# Yahoo says "ふんかん" and OJAD says "ぷんかん", literal startswith / equality
# checks would never match and the alignment would cascade-fail. We compare
# under a normalisation that folds each voiced/half-voiced kana to its
# voiceless base, so ぷ↔ふ, ば↔は, ご↔こ etc. all alias together.
_VOICING_FOLD: dict[str, str] = {
    "が": "か", "ぎ": "き", "ぐ": "く", "げ": "け", "ご": "こ",
    "ざ": "さ", "じ": "し", "ず": "す", "ぜ": "せ", "ぞ": "そ",
    "だ": "た", "ぢ": "ち", "づ": "つ", "で": "て", "ど": "と",
    "ば": "は", "び": "ひ", "ぶ": "ふ", "べ": "へ", "ぼ": "ほ",
    "ぱ": "は", "ぴ": "ひ", "ぷ": "ふ", "ぺ": "へ", "ぽ": "ほ",
}  # fmt: skip


def _norm(s: str) -> str:
    """Kata→hira plus voicing fold for rendaku-tolerant alignment."""
    hira = jaconv.kata2hira(s)
    return "".join(_VOICING_FOLD.get(c, c) for c in hira)


# --- DP aligner ----------------------------------------------------------------
#
# The greedy aligner this replaced had two fatal failure modes: a numeric
# anchor that over-consumed when Yahoo and OJAD disagreed on a phrase
# boundary, and a fallback path that advanced OJAD by exactly +1 — so a
# single mismatch cascaded into type-0 fallback for every downstream token.
#
# Instead we now build a Needleman-Wunsch-style DP over (yahoo_token,
# ojad_entry) pairs. Each cell dp[i][j] holds the minimum total cost to
# explain Yahoo tokens [0..i) using OJAD entries [0..j). For every (i, j)
# we try consuming k OJAD entries for token i with k ∈ [0, K_MAX]; the
# per-token cost depends on token shape (punctuation / numeric / kana) and
# uses edit distance over rendaku-folded strings for kana tokens. A bad
# token costs O(1); downstream tokens stay aligned.

_K_MAX = 16  # max OJAD entries one Yahoo token can consume
_INF = float("inf")
_FALLBACK_COST = 3.0  # cost of giving up on a single token (k=0 for kana/numeric)
_OJAD_PUNCT_TEXTS = {"、", "。", ",", ".", "?", "!", "！", "？"}


# Substitutions are cheaper than insertions/deletions: a substitution keeps
# the yahoo↔ojad mora-count alignment intact (the kind of mismatch we
# *expect* — rendaku, reading variants like 等→とう/など), while ins/del
# means the two sources disagree on mora count, which is much less common
# and almost always a worse alignment. With sub<0.5, the DP correctly
# prefers a same-length span with two substitutions (cost 0.8) over a
# shorter span with one deletion (cost 1.0). This breaks the tie that was
# letting OJAD's `う` from `等→とう` leak forward onto the next token.
_SUB_COST = 0.4


def _edit_distance(a: str, b: str) -> float:
    """Weighted Levenshtein with sub_cost=0.4, ins/del=1.0.
    Used over rendaku-folded strings only."""
    if a == b:
        return 0.0
    if not a:
        return float(len(b))
    if not b:
        return float(len(a))
    prev: list[float] = [float(j) for j in range(len(b) + 1)]
    for i, ca in enumerate(a, 1):
        curr: list[float] = [float(i)] + [0.0] * len(b)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                curr[j] = prev[j - 1]
            else:
                curr[j] = min(
                    prev[j - 1] + _SUB_COST,  # substitute
                    prev[j] + 1.0,  # delete from a
                    curr[j - 1] + 1.0,  # insert into a
                )
        prev = curr
    return prev[-1]


def _is_punct_token(furigana: str, is_numeric: bool) -> bool:
    if is_numeric or not furigana:
        return False
    return all(not is_kana_or_kanji(c) for c in furigana)


def _match_cost(
    token: Any, span_texts: list[str], is_numeric: bool, is_punct: bool
) -> float:
    """Cost of letting `token` consume the given OJAD-text span."""
    k = len(span_texts)
    concat = "".join(span_texts)

    if is_punct:
        if k == 0:
            return 0.0
        if k == 1:
            stripped = span_texts[0].strip()
            yahoo_stripped = token.furigana.strip()
            # Free-consume only if the OJAD entry actually matches this
            # token's punct (or is empty). Without this, two adjacent
            # punct tokens (e.g. "。" then "\n") could both consume the
            # single OJAD "。" at zero cost and DP would arbitrarily give
            # it to the wrong one.
            if not stripped:
                return 0.0
            if stripped == yahoo_stripped:
                return 0.0
        return _INF

    if is_numeric:
        if k == 0:
            return _FALLBACK_COST
        # Numerics have no Yahoo furigana to compare against. Accept any
        # reasonable count of morae; only penalise blatantly over-long spans.
        upper = max(4, len(token.surface) * 4)
        return 0.0 if k <= upper else float(k - upper)

    # Kana / kanji token: compare under rendaku fold.
    if k == 0:
        return _FALLBACK_COST
    y_norm = _norm(token.furigana)
    o_norm = _norm(concat)
    # Cheap length pre-filter — keeps the DP fast and prevents pathological
    # "consume 12 OJAD entries to match a 2-mora Yahoo token" alignments.
    if abs(len(y_norm) - len(o_norm)) > 3:
        return _INF
    return _edit_distance(y_norm, o_norm)


def _build_word_result(token: Any, ojad_span: list[dict[str, Any]]) -> WordAccentResult:
    """Wrap an aligned (token, OJAD-span) pair into a WordAccentResult."""
    yahoo_surface = token.surface
    yahoo_furigana = token.furigana
    is_numeric = bool(numeric_pattern.match(yahoo_surface))
    subword = (
        [WordResult(furigana=s.furigana, surface=s.surface) for s in token.subword]
        if token.subword
        else []
    )

    if not ojad_span:
        # k=0 path: emit type-0 fallback so the downstream override pass and
        # callers still see one AccentInfo per token.
        return WordAccentResult(
            surface=yahoo_surface,
            furigana=yahoo_furigana,
            accent=[
                AccentInfo(
                    furigana=yahoo_furigana,
                    accent_marking_type=0,
                    length=len(yahoo_furigana),
                )
            ],
            subword=subword,
        )

    # Drop OJAD entries with empty text (phrase-boundary sentinels). They
    # carry no audible mora and would surface as a stray "(…||0)" row.
    voiced_span = [e for e in ojad_span if e["text"]]
    if not voiced_span:
        return WordAccentResult(
            surface=yahoo_surface,
            furigana=yahoo_furigana,
            accent=[
                AccentInfo(
                    furigana=yahoo_furigana,
                    accent_marking_type=0,
                    length=len(yahoo_furigana),
                )
            ],
            subword=subword,
        )
    accents = [
        AccentInfo(
            furigana=e["text"],
            accent_marking_type=e["accent"],
            length=len(e["text"]),
        )
        for e in voiced_span
    ]
    # Numerics had no Yahoo furigana to begin with — surface the OJAD reading.
    display = "".join(e["text"] for e in voiced_span) if is_numeric else yahoo_furigana
    return WordAccentResult(
        surface=yahoo_surface,
        furigana=display,
        accent=accents,
        subword=subword,
    )


def _fallback_word(token: Any) -> WordAccentResult:
    return _build_word_result(token, [])


async def align_accent(
    furigana_results: list[Any], ojad_results: list[dict[str, Any]]
) -> list[WordAccentResult]:
    """Align yahoo furigana with OJAD per-moji entries via global DP.

    Returns one WordAccentResult per Yahoo token. Each token consumes a
    (possibly empty) contiguous span of OJAD entries; the assignment that
    minimises total mismatch cost wins.
    """
    n = len(furigana_results)
    m = len(ojad_results)

    if n == 0:
        return []
    if m == 0:
        return [_fallback_word(t) for t in furigana_results]

    # Pre-compute per-token classification and OJAD texts.
    token_kinds: list[tuple[bool, bool]] = []
    for t in furigana_results:
        is_num = bool(numeric_pattern.match(t.surface))
        is_pct = _is_punct_token(t.furigana, is_num)
        token_kinds.append((is_num, is_pct))
    ojad_texts = [e["text"] for e in ojad_results]

    # dp[i][j] = best cost aligning tokens [0..i) to ojad entries [0..j).
    dp: list[list[float]] = [[_INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[int]] = [[-1] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0

    for i in range(n):
        token = furigana_results[i]
        is_num, is_pct = token_kinds[i]
        for j in range(m + 1):
            base = dp[i][j]
            if base == _INF:
                continue
            k_limit = min(_K_MAX, m - j)
            for k in range(0, k_limit + 1):
                cost = _match_cost(token, ojad_texts[j : j + k], is_num, is_pct)
                if cost == _INF:
                    continue
                new_cost = base + cost
                if new_cost < dp[i + 1][j + k]:
                    dp[i + 1][j + k] = new_cost
                    back[i + 1][j + k] = j

    # Pick the best terminal state. Prefer fully consuming OJAD; otherwise
    # take the cheapest end (trailing empty entries get inherited for free
    # by the previous token's span since their text contributes nothing to
    # edit distance).
    best_j = m
    best_cost = dp[n][m]
    if best_cost == _INF:
        for j in range(m + 1):
            if dp[n][j] < best_cost:
                best_cost = dp[n][j]
                best_j = j

    if best_cost == _INF:
        logger.error(
            "DP alignment found no valid path (n=%d, m=%d); falling back per token.",
            n,
            m,
        )
        return [_fallback_word(t) for t in furigana_results]

    # Backtrack to recover the OJAD span each token consumed.
    spans: list[tuple[int, int]] = [(0, 0)] * n
    cur_j = best_j
    for i in range(n, 0, -1):
        prev_j = back[i][cur_j]
        if prev_j < 0:
            logger.error("DP backtrace broken at i=%d, j=%d", i, cur_j)
            return [_fallback_word(t) for t in furigana_results]
        spans[i - 1] = (prev_j, cur_j)
        cur_j = prev_j

    logger.debug("DP alignment cost=%.2f spans=%s", best_cost, spans)
    return [
        _build_word_result(furigana_results[i], ojad_results[s:e])
        for i, (s, e) in enumerate(spans)
    ]
