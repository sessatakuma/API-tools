"""Align local-tokeniser tokens with OJAD per-mora accent entries.

The DP aligner replaces an earlier greedy implementation that had two fatal
failure modes: a numeric anchor that over-consumed when the tokeniser and
OJAD disagreed on a phrase boundary, and a fallback path that advanced
OJAD by exactly +1 — so a single mismatch cascaded into type-0 fallback
for every downstream token.

`align_accent()` builds a Needleman-Wunsch-style DP over (token, ojad_entry)
pairs. Each cell `dp[i][j]` holds the minimum total cost to explain tokens
[0..i) using OJAD entries [0..j). For every (i, j) we try consuming k OJAD
entries for token i with k ∈ [0, _K_MAX]; the per-token cost depends on
token shape (punctuation / numeric / readable-symbol compound / kana) and
uses edit distance over rendaku-folded strings for kana tokens. A bad
token costs O(1); downstream tokens stay aligned.

This module also owns the per-token classification predicates and the
edit-distance / voicing-fold tables they depend on.
"""

from __future__ import annotations

import logging
import string
from typing import Any

import jaconv

from api.accent.models import AccentInfo, WordAccentResult, WordResult
from api.accent.preprocess import (
    NUMERIC_PATTERN,
    READABLE_COMPOUND_RE,
    READABLE_SYMBOLS,
)

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
        "、、、",
        "————",
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
        "＂",
        "＃",
        "＄",
        "％",
        "＆",
        "＼",
        "’",
        "＊",
        "＋",
        "－",
        "．",
        "／",
        "＜",
        "＝",
        "＞",
        "＠",
        "［",
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
        return False
    kana = range(0x3040, 0x30FF + 1)
    kanji = range(0x4E00, 0x9FFF + 1)
    if ord(char) in kana or ord(char) in kanji:
        return True
    return False


# Local tokeniser returns "dictionary form" furigana (no rendaku), while OJAD
# returns the actually pronounced kana (with rendaku/sequential-voicing
# applied). When the tokeniser says "ふんかん" and OJAD says "ぷんかん", the
# literal startswith / equality checks would never match and the alignment
# would cascade-fail. We compare under a normalisation that folds each
# voiced/half-voiced kana to its voiceless base, so ぷ↔ふ, ば↔は, ご↔こ etc.
# all alias together.
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


# --- DP aligner constants ------------------------------------------------------

_K_MAX = 16  # max OJAD entries one token can consume
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
    # Readable symbols (%, ℃ …) syntactically look like punctuation but
    # carry spoken readings — they're handled by the readable-compound
    # path, not the punct DP branch.
    if all(c in READABLE_SYMBOLS for c in furigana):
        return False
    # ASCII-letter tokens (e.g. `iPhone`, `Apple` UniDic doesn't recognise)
    # are foreign words, not punctuation. Without this guard they'd be
    # classified as punct, the DP would refuse their OJAD morae, and
    # those morae would leak onto neighboring tokens. They should flow
    # through the kana path (cost via edit_distance against the OJAD
    # span) so the morae stay anchored — `_apply_furigana_toggles` then
    # wipes them at request time if `render_english_furigana` is False.
    if any("a" <= c <= "z" or "A" <= c <= "Z" for c in furigana):
        return False
    return all(not is_kana_or_kanji(c) for c in furigana)


def _is_english_compound_surface(surface: str) -> bool:
    """Acronym / model-code surface containing at least one ASCII letter.

    Matches acronym tokens fused by `tokenizer.tag_local` — both the
    pure-alphanumeric variant (`G2P`, `iPhone7`, `H2O`) and the
    bridge-separator variant (`PSP-1000`, `Wi-Fi`, `RTX-4090`,
    `foo_bar1`, `Wifi.7`, `Python3.11`). All such surfaces get the
    same free-consume treatment as numerics in `_match_cost`. Without
    this branch the fused surface would fall through to the kana path
    and edit-distance against OJAD's kana would push the DP into k=0 /
    partial-consume splits, leaking morae onto the next Japanese token.
    Mirrors the rule in `postprocess._is_pure_english_surface` so the
    toggle wipe and the aligner agree on which surfaces qualify.
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


def _match_cost(
    token: WordResult,
    span_texts: list[str],
    is_numeric: bool,
    is_punct: bool,
    is_readable_compound: bool = False,
    is_english_compound: bool = False,
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

    if is_english_compound:
        # Surface is `G2P` / `iPhone7` / `Wifi.7` (merged alphanumeric
        # run) — same situation as readable_compound: no kana to align
        # against, OJAD has spelled the whole acronym out as one phrase.
        # Accept any reasonable span at cost 0 so the morae stay
        # anchored on the merged token; `apply_furigana_toggles` wipes
        # them downstream.
        #
        # Checked BEFORE the OJAD-punct guard because OJAD sometimes
        # injects a `。` mid-stream when it normalises a `.` separator
        # (`Wifi.7` → echoed as `Wifi。7`). That punct entry needs to
        # be absorbed by this same merged token rather than blocked —
        # otherwise the morae after the `。` cascade onto the next
        # Japanese token. `_build_word_result` filters those punct
        # entries back out so they don't surface as ruby when the
        # English toggle is on.
        #
        # k=0 is also free: OJAD often elides English entirely when
        # it's interleaved with kana (Whisper inside `ふりがなWhisper`,
        # satochin inside `深掘りライターsatochin氏`, URLPLACEHOLDER
        # after strip_urls). Charging _FALLBACK_COST for k=0 made the
        # DP steal a mora from the neighbouring kana token to dodge
        # the penalty — the cascade that left ふりがな missing 'な',
        # コメント empty-spanned, ライター missing 'ー', and テスト
        # missing 'テ' in test_0/test_1. Free k=0 keeps spelled-out
        # cases (`G2P` → ジーツーピー) working because forcing those
        # OJAD morae onto a neighbouring kana token still costs more
        # edit-distance than letting the english token take them.
        if k == 0:
            return 0.0
        # Loose upper: ~4 morae per char (covers OJAD's longest letter
        # spellings, e.g. `M` → エム = 2 morae, but headroom for the
        # digit pieces that can spell out to 4 morae like ナナ for 7).
        upper = max(4, len(token.surface) * 4)
        return 0.0 if k <= upper else float(k - upper)

    # Beyond this point the token wants OJAD morae as its reading.
    # OJAD's punct entries (、 。 , . ! ?) carry no spoken kana and must
    # never bleed into a non-punct token — without this guard `、`
    # leaks onto the next numeric / kana token's accent list (the
    # 「2001|、|0」 symptom from the 量的緩和 paragraph).
    if any(t in _OJAD_PUNCT_TEXTS for t in span_texts):
        return _INF

    if is_readable_compound:
        # Surface is `\d+%` or similar; OJAD pronounces the whole thing
        # as one phrase whose mora count depends on the digit reading
        # plus the symbol's kana. We have no kana to compare against,
        # so accept any plausibly-sized span at cost 0 and only punish
        # blatantly long ones.
        if k == 0:
            return _FALLBACK_COST
        # Loose upper: per-digit ratio (max 4) + 8 morae of symbol kana.
        upper = max(4, len(token.surface) * 4) + 8
        return 0.0 if k <= upper else float(k - upper)

    if is_numeric:
        if k == 0:
            return _FALLBACK_COST
        # Numerics have no token furigana to compare against. Accept any
        # reasonable count of morae; only penalise blatantly over-long spans.
        upper = max(4, len(token.surface) * 4)
        if k > upper:
            return float(k - upper)
        # Tiebreaker: empty OJAD entries are phrase-break markers
        # (notably the ones our preprocessing inserts when swapping
        # `\d×\d` → `\d/\d`). They should be absorbed by the adjacent
        # punct token, not stranded inside a numeric span — without
        # this nudge the DP can split `19×19` into 1-mora-then-7-mora
        # since every k in [1, upper] has cost 0. The penalty is tiny
        # (well below kana _SUB_COST=0.4) so it only breaks ties.
        empty_in_span = sum(1 for t in span_texts if not t)
        return 0.01 * empty_in_span

    # Override-synthesized tokens: the regex layer
    # (`reading_overrides.apply_furigana_overrides`) merges spans like
    # `20歳` into a single WordResult with a prescribed `furigana`
    # (`はたち`) that does NOT match OJAD's reading of the same surface
    # (`にじゅっさい`, 5 morae vs はたち's 3). Without a free-consume
    # branch, the DP gives this token just 3 morae and the leftover
    # `さい` cascades onto the next kana token (the `20歳 → の → 私`
    # leak symptom). Override-merged tokens lose their UniDic backing
    # (`base` and `pos` are both None — `ReplacementToken.build`
    # constructs WordResults without MA metadata), which is the
    # discriminator here. `apply_accent_overrides` rewrites the accent
    # post-align so whatever marks DP picked up from OJAD are discarded.
    if getattr(token, "base", None) is None and getattr(token, "pos", None) is None:
        if k == 0:
            return _FALLBACK_COST
        upper = max(4, len(token.surface) * 4 + 4)
        return 0.0 if k <= upper else float(k - upper)

    # Kana / kanji token: compare under rendaku fold. The OJAD-punct
    # guard above already kicks in for any non-punct token, so by the
    # time we reach the kana branch the span is guaranteed punct-free.
    if k == 0:
        return _FALLBACK_COST
    y_norm = _norm(token.furigana)
    o_norm = _norm(concat)
    # Cheap length pre-filter — keeps the DP fast and prevents pathological
    # "consume 12 OJAD entries to match a 2-mora token" alignments.
    if abs(len(y_norm) - len(o_norm)) > 3:
        return _INF
    return _edit_distance(y_norm, o_norm)


def _build_word_result(
    token: WordResult, ojad_span: list[dict[str, Any]]
) -> WordAccentResult:
    """Wrap an aligned (token, OJAD-span) pair into a WordAccentResult."""
    token_surface = token.surface
    token_furigana = token.furigana
    is_numeric = bool(NUMERIC_PATTERN.match(token_surface))
    is_readable_compound = bool(READABLE_COMPOUND_RE.match(token_surface))
    subword = (
        [WordResult(furigana=s.furigana, surface=s.surface) for s in token.subword]
        if token.subword
        else []
    )
    # Carry tokeniser metadata through alignment so downstream patches can
    # branch on it. Tokens constructed by override replacements (no MA
    # backing) will have these as None — that's fine.
    lexical_kernel = getattr(token, "lexical_kernel", None)
    lexical_kernel_alts = getattr(token, "lexical_kernel_alts", None)
    base = getattr(token, "base", None)
    pos = getattr(token, "pos", None)
    pos1 = getattr(token, "pos1", None)
    conjugation_type = getattr(token, "conjugation_type", None)
    conjugation_form = getattr(token, "conjugation_form", None)

    # Pure-punctuation tokens (`「`, `」`, `、`, `!`, `?` …) carry no reading.
    # Echoing the surface as furigana made clients render ruby on top of
    # the punctuation char itself, which looks wrong. Emit an empty
    # furigana + empty accent — the same "skip ruby" signal used by
    # `restore_urls`. Readable compounds (`2%`) skip this exit because
    # their `furigana` mirrors the surface (e.g. "2%") and would
    # otherwise look like punct here — the OJAD-driven path below
    # rewrites them to the spoken reading.
    if not is_readable_compound and _is_punct_token(token_furigana, is_numeric):
        return WordAccentResult(
            surface=token_surface,
            furigana="",
            accent=[],
            subword=subword,
            base=base,
            pos=pos,
            pos1=pos1,
            conjugation_type=conjugation_type,
            conjugation_form=conjugation_form,
            lexical_kernel=lexical_kernel,
            lexical_kernel_alts=lexical_kernel_alts,
        )

    # Fallback accent payload for paths with no usable OJAD info. Single
    # type-0 entry covering the whole token so downstream overrides and
    # callers still see one AccentInfo per token.
    fallback_accent = [
        AccentInfo(
            furigana=token_furigana,
            accent_marking_type=0,
            length=len(token_furigana),
        )
    ]

    if not ojad_span:
        # k=0 path. `kernel_absorbed` stays False — no OJAD span means
        # "no OJAD info", not "OJAD absorbed the kernel".
        return WordAccentResult(
            surface=token_surface,
            furigana=token_furigana,
            accent=fallback_accent,
            subword=subword,
            base=base,
            pos=pos,
            pos1=pos1,
            conjugation_type=conjugation_type,
            conjugation_form=conjugation_form,
            lexical_kernel=lexical_kernel,
            lexical_kernel_alts=lexical_kernel_alts,
        )

    # Drop OJAD entries with empty text (phrase-boundary sentinels) and,
    # for english-compound tokens, the OJAD-punct entries we let
    # `_match_cost` absorb at cost 0. Those punct entries are OJAD
    # artefacts from normalising `.` → `。` mid-acronym (`Wifi.7`); they
    # carry no spoken mora and would surface as a stray `。` in the
    # ruby when `render_english_furigana=True`.
    is_english_compound = not is_readable_compound and _is_english_compound_surface(
        token_surface
    )
    if is_english_compound:
        voiced_span = [
            e for e in ojad_span if e["text"] and e["text"] not in _OJAD_PUNCT_TEXTS
        ]
    else:
        voiced_span = [e for e in ojad_span if e["text"]]
    if not voiced_span:
        return WordAccentResult(
            surface=token_surface,
            furigana=token_furigana,
            accent=fallback_accent,
            subword=subword,
            base=base,
            pos=pos,
            pos1=pos1,
            conjugation_type=conjugation_type,
            conjugation_form=conjugation_form,
            lexical_kernel=lexical_kernel,
            lexical_kernel_alts=lexical_kernel_alts,
        )
    accents = [
        AccentInfo(
            furigana=e["text"],
            accent_marking_type=e["accent"],
            length=len(e["text"]),
        )
        for e in voiced_span
    ]
    # `kernel_absorbed`: UniDic says this word has a kernel (lexical_kernel
    # >= 1) but OJAD's per-mora output for its range carries no FALL. This
    # typically happens when the word sits in the medial position of a long
    # prosodic phrase and OJAD's CRF collapses its kernel into the
    # surrounding contour (the 忙しい-inside-お忙しい中 case).
    kernel_absorbed = (
        isinstance(lexical_kernel, int)
        and lexical_kernel >= 1
        and not any(a.accent_marking_type == 2 for a in accents)
    )
    # Numerics and readable-symbol compounds (e.g. `2%`) carry no kana
    # furigana of their own — surface OJAD's reading instead.
    display = (
        "".join(e["text"] for e in voiced_span)
        if (is_numeric or is_readable_compound)
        else token_furigana
    )
    return WordAccentResult(
        surface=token_surface,
        furigana=display,
        accent=accents,
        subword=subword,
        base=base,
        pos=pos,
        pos1=pos1,
        conjugation_type=conjugation_type,
        conjugation_form=conjugation_form,
        lexical_kernel=lexical_kernel,
        lexical_kernel_alts=lexical_kernel_alts,
        kernel_absorbed=kernel_absorbed,
    )


def _fallback_word(token: WordResult) -> WordAccentResult:
    return _build_word_result(token, [])


async def align_accent(
    furigana_results: list[WordResult], ojad_results: list[dict[str, Any]]
) -> list[WordAccentResult]:
    """Align tokens with OJAD per-moji entries via global DP.

    Returns one WordAccentResult per input token. Each token consumes a
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
    token_kinds: list[tuple[bool, bool, bool, bool]] = []
    for t in furigana_results:
        is_num = bool(NUMERIC_PATTERN.match(t.surface))
        is_compound = bool(READABLE_COMPOUND_RE.match(t.surface))
        # English-compound takes precedence over numeric for surfaces like
        # `G2P`: NUMERIC_PATTERN does not match (letters present), but a
        # one-char numeric inside the run shouldn't reclassify the merged
        # token. is_num stays False here because the merged surface has
        # letters; the check is for clarity.
        is_eng = (
            (not is_compound)
            and (not is_num)
            and _is_english_compound_surface(t.surface)
        )
        is_pct = (
            (not is_compound) and (not is_eng) and _is_punct_token(t.furigana, is_num)
        )
        token_kinds.append((is_num, is_pct, is_compound, is_eng))
    ojad_texts = [e["text"] for e in ojad_results]

    # dp[i][j] = best cost aligning tokens [0..i) to ojad entries [0..j).
    dp: list[list[float]] = [[_INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[int]] = [[-1] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0

    for i in range(n):
        token = furigana_results[i]
        is_num, is_pct, is_compound, is_eng = token_kinds[i]
        for j in range(m + 1):
            base = dp[i][j]
            if base == _INF:
                continue
            k_limit = min(_K_MAX, m - j)
            for k in range(0, k_limit + 1):
                cost = _match_cost(
                    token,
                    ojad_texts[j : j + k],
                    is_num,
                    is_pct,
                    is_compound,
                    is_eng,
                )
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
