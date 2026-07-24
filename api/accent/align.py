"""Align local-tokeniser tokens with per-mora accent entries.

The per-mora spans now come from `openjtalk.py` (in-process OpenJTalk); the
"OpenJTalk" naming below is historical — the aligner is intentionally
backend-agnostic and only consumes the shared `{text, accent}` per-mora shape.

The DP aligner replaces an earlier greedy implementation that had two fatal
failure modes: a numeric anchor that over-consumed when the tokeniser and
OpenJTalk disagreed on a phrase boundary, and a fallback path that advanced
OpenJTalk by exactly +1 — so a single mismatch cascaded into type-0 fallback
for every downstream token.

`align_accent()` builds a Needleman-Wunsch-style DP over (token, accent_entry)
pairs. Each cell `dp[i][j]` holds the minimum total cost to explain tokens
[0..i) using OpenJTalk entries [0..j). For every (i, j) we try consuming k OpenJTalk
entries for token i with k ∈ [0, _K_MAX]; the per-token cost depends on
token shape (punctuation / numeric / readable-symbol compound / kana) and
uses edit distance over rendaku-folded strings for kana tokens. A bad
token costs O(1); downstream tokens stay aligned.

This module also owns the per-token classification predicates and the
edit-distance / voicing-fold tables they depend on.
"""

from __future__ import annotations

import asyncio
import logging
import re
import string
import threading
from typing import Any

import jaconv

from api.accent.models import AccentInfo, WordAccentResult, WordResult
from api.accent.preprocess import (
    NUMERIC_PATTERN,
    READABLE_COMPOUND_RE,
    READABLE_SYMBOLS,
)

logger = logging.getLogger("api")
_ALIGN_LOCK = threading.Lock()

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

    OpenJTalk's CRF parser gives better results when Latin alphabet runs are
    removed before submission. Punctuation is intentionally left in
    place — OpenJTalk relies on it for phrase boundaries.
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


# Local tokeniser returns "dictionary form" furigana (no rendaku), while OpenJTalk
# returns the actually pronounced kana (with rendaku/sequential-voicing
# applied). When the tokeniser says "ふんかん" and OpenJTalk says "ぷんかん", the
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


# Hiragana mora = one base kana + an optional small kana (拗音 ゃゅょ, ぁ-ぉ, ゎ).
# `ー` (loanword long-vowel mark, e.g. けーき) and っ / ん stand as their own
# morae, matching how the OpenJTalk frontend counts them.
_HIRA_MORA_RE = re.compile(r".[ゃゅょぁぃぅぇぉゎ]?")


def _split_morae(kana: str) -> list[str]:
    """Split a hiragana reading into morae (small kana attach to the base)."""
    return _HIRA_MORA_RE.findall(kana)


# --- DP aligner constants ------------------------------------------------------

# Max per-mora accent entries a single token may consume in the DP. This is a
# fan-out / cost bound on the inner `k` loop (DP cost is O(n * m * _K_MAX)), not
# a backend-specific limit — it applies to whatever engine fills the per-mora
# `{text, accent}` stream. It must cover the largest span any token legitimately
# wants: the numeric / compound branches accept up to `max(4, len(surface) * 4)`
# morae, so 32 covers numbers up to ~8 digits read as one phrase. A token whose
# reading needs more morae than this spills the remainder onto the next token
# (rare — only very long unbroken numbers); raising this trades that off against
# DP cost, which the per-chunk length cap in `preprocess.MAX_CHUNK_CHARS` bounds.
_K_MAX = 32
_INF = float("inf")
_FALLBACK_COST = 3.0  # cost of giving up on a single token (k=0 for kana/numeric)
_PUNCT_TEXTS = {"、", "。", ",", ".", "?", "!", "！", "？"}


def _numeric_mora_targets(
    tokens: list[WordResult],
    token_kinds: list[tuple[bool, bool, bool, bool]],
    accent_texts: list[str],
) -> list[int | None]:
    targets: list[int | None] = [None] * len(tokens)
    numeric_indexes = [index for index, kinds in enumerate(token_kinds) if kinds[0]]
    if not numeric_indexes:
        return targets
    numeric_surfaces = {tokens[index].surface for index in numeric_indexes}
    if len(numeric_surfaces) > 1:
        return targets

    reserved = 0
    for token, (is_num, is_punct, is_compound, is_english) in zip(tokens, token_kinds):
        if is_num or is_punct:
            continue
        if is_compound or is_english or (token.base is None and token.pos is None):
            return targets
        reserved += len(_split_morae(token.furigana))

    voiced = sum(1 for text in accent_texts if text and text not in _PUNCT_TEXTS)
    budget = voiced - reserved
    if budget < len(numeric_indexes):
        return targets

    weights = [
        max(1, sum(char.isdigit() for char in tokens[index].surface))
        for index in numeric_indexes
    ]
    remaining = budget - len(numeric_indexes)
    total_weight = sum(weights)
    allocations = [1 + (remaining * weight // total_weight) for weight in weights]
    unallocated = budget - sum(allocations)
    remainders = [
        (remaining * weight % total_weight, position)
        for position, weight in enumerate(weights)
    ]
    for _remainder, position in sorted(remainders, reverse=True)[:unallocated]:
        allocations[position] += 1
    for index, allocation in zip(numeric_indexes, allocations):
        targets[index] = allocation
    return targets


# Substitutions are cheaper than insertions/deletions: a substitution keeps
# the token↔engine mora-count alignment intact (the kind of mismatch we
# *expect* — rendaku, reading variants like 等→とう/など), while ins/del
# means the two sources disagree on mora count, which is much less common
# and almost always a worse alignment. With sub<0.5, the DP correctly
# prefers a same-length span with two substitutions (cost 0.8) over a
# shorter span with one deletion (cost 1.0). This breaks the tie that was
# letting OpenJTalk's `う` from `等→とう` leak forward onto the next token.
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
    # classified as punct, the DP would refuse their OpenJTalk morae, and
    # those morae would leak onto neighboring tokens. They should flow
    # through the kana path (cost via edit_distance against the OpenJTalk
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
    and edit-distance against OpenJTalk's kana would push the DP into k=0 /
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
    """Cost of letting `token` consume the given OpenJTalk-text span."""
    k = len(span_texts)
    concat = "".join(span_texts)

    if is_punct:
        if k == 0:
            return 0.0
        if k == 1:
            stripped = span_texts[0].strip()
            yahoo_stripped = token.furigana.strip()
            # Free-consume only if the OpenJTalk entry actually matches this
            # token's punct (or is empty). Without this, two adjacent
            # punct tokens (e.g. "。" then "\n") could both consume the
            # single OpenJTalk "。" at zero cost and DP would arbitrarily give
            # it to the wrong one.
            if not stripped:
                return 0.0
            if stripped == yahoo_stripped:
                return 0.0
        return _INF

    if is_english_compound:
        # Surface is `G2P` / `iPhone7` / `Wifi.7` (merged alphanumeric
        # run) — same situation as readable_compound: no kana to align
        # against, OpenJTalk has spelled the whole acronym out as one phrase.
        # Accept any reasonable span at cost 0 so the morae stay
        # anchored on the merged token; `apply_furigana_toggles` wipes
        # them downstream.
        #
        # Checked BEFORE the OpenJTalk-punct guard because OpenJTalk sometimes
        # injects a `。` mid-stream when it normalises a `.` separator
        # (`Wifi.7` → echoed as `Wifi。7`). That punct entry needs to
        # be absorbed by this same merged token rather than blocked —
        # otherwise the morae after the `。` cascade onto the next
        # Japanese token. `_build_word_result` filters those punct
        # entries back out so they don't surface as ruby when the
        # English toggle is on.
        #
        # k=0 is also free: OpenJTalk often elides English entirely when
        # it's interleaved with kana (Whisper inside `ふりがなWhisper`,
        # satochin inside `深掘りライターsatochin氏`, URLPLACEHOLDER
        # after strip_urls). Charging _FALLBACK_COST for k=0 made the
        # DP steal a mora from the neighbouring kana token to dodge
        # the penalty — the cascade that left ふりがな missing 'な',
        # コメント empty-spanned, ライター missing 'ー', and テスト
        # missing 'テ' in test_0/test_1. Free k=0 keeps spelled-out
        # cases (`G2P` → ジーツーピー) working because forcing those
        # OpenJTalk morae onto a neighbouring kana token still costs more
        # edit-distance than letting the english token take them.
        if k == 0:
            return 0.0
        # Loose upper: ~4 morae per char (covers OpenJTalk's longest letter
        # spellings, e.g. `M` → エム = 2 morae, but headroom for the
        # digit pieces that can spell out to 4 morae like ナナ for 7).
        upper = max(4, len(token.surface) * 4)
        return 0.0 if k <= upper else float(k - upper)

    # Beyond this point the token wants OpenJTalk morae as its reading.
    # OpenJTalk's punct entries (、 。 , . ! ?) carry no spoken kana and must
    # never bleed into a non-punct token — without this guard `、`
    # leaks onto the next numeric / kana token's accent list (the
    # 「2001|、|0」 symptom from the 量的緩和 paragraph).
    if any(t in _PUNCT_TEXTS for t in span_texts):
        return _INF

    if is_readable_compound:
        # Surface is `\d+%` or similar; OpenJTalk pronounces the whole thing
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
        # Tiebreaker: empty backend entries are phrase-break markers. They
        # should be absorbed by an adjacent punct token, not stranded inside
        # a numeric span — without
        # this nudge the DP can split `19×19` into 1-mora-then-7-mora
        # since every k in [1, upper] has cost 0. The penalty is tiny
        # (well below kana _SUB_COST=0.4) so it only breaks ties.
        empty_in_span = sum(1 for t in span_texts if not t)
        return 0.01 * empty_in_span

    # Override-synthesized tokens: the regex layer
    # (`reading_overrides.apply_furigana_overrides`) merges spans like
    # `20歳` into a single WordResult with a prescribed `furigana`
    # (`はたち`) that does NOT match OpenJTalk's reading of the same surface
    # (`にじゅっさい`, 5 morae vs はたち's 3). Without a free-consume
    # branch, the DP gives this token just 3 morae and the leftover
    # `さい` cascades onto the next kana token (the `20歳 → の → 私`
    # leak symptom). Override-merged tokens lose their UniDic backing
    # (`base` and `pos` are both None — `ReplacementToken.build`
    # constructs WordResults without MA metadata), which is the
    # discriminator here. `apply_accent_overrides` rewrites the accent
    # post-align so whatever marks DP picked up from OpenJTalk are discarded.
    if getattr(token, "base", None) is None and getattr(token, "pos", None) is None:
        if k == 0:
            return _FALLBACK_COST
        upper = max(4, len(token.surface) * 4 + 4)
        return 0.0 if k <= upper else float(k - upper)

    # Kana / kanji token: compare under rendaku fold. The OpenJTalk-punct
    # guard above already kicks in for any non-punct token, so by the
    # time we reach the kana branch the span is guaranteed punct-free.
    if k == 0:
        return _FALLBACK_COST
    y_norm = _norm(token.furigana)
    o_norm = _norm(concat)
    # Cheap length pre-filter — keeps the DP fast and prevents pathological
    # "consume 12 OpenJTalk entries to match a 2-mora token" alignments.
    if abs(len(y_norm) - len(o_norm)) > 3:
        return _INF
    return _edit_distance(y_norm, o_norm)


def _build_word_result(
    token: WordResult, accent_span: list[dict[str, Any]]
) -> WordAccentResult:
    """Wrap an aligned (token, OpenJTalk-span) pair into a WordAccentResult."""
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
    # otherwise look like punct here — the OpenJTalk-driven path below
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

    # Fallback accent payload for paths with no usable OpenJTalk info. Single
    # type-0 entry covering the whole token so downstream overrides and
    # callers still see one AccentInfo per token.
    fallback_accent = [
        AccentInfo(
            furigana=token_furigana,
            accent_marking_type=0,
            length=len(token_furigana),
        )
    ]

    if not accent_span:
        # k=0 path. `kernel_absorbed` stays False — no OpenJTalk span means
        # "no OpenJTalk info", not "OpenJTalk absorbed the kernel".
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

    # Drop OpenJTalk entries with empty text (phrase-boundary sentinels) and,
    # for english-compound tokens, the OpenJTalk-punct entries we let
    # `_match_cost` absorb at cost 0. Those punct entries are OpenJTalk
    # artefacts from normalising `.` → `。` mid-acronym (`Wifi.7`); they
    # carry no spoken mora and would surface as a stray `。` in the
    # ruby when `render_english_furigana=True`.
    is_english_compound = not is_readable_compound and _is_english_compound_surface(
        token_surface
    )
    if is_english_compound:
        voiced_span = [
            e for e in accent_span if e["text"] and e["text"] not in _PUNCT_TEXTS
        ]
    else:
        voiced_span = [e for e in accent_span if e["text"]]
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
    # Per-mora ruby comes from the tokeniser's reading, not the engine's g2p
    # output: g2p spells long vowels phonetically (きょう→きょー, えいご→えーご),
    # whereas the UniDic reading carries the correct orthography for native
    # (きょう / えいご) and loanword (けーき, ー kept) words alike. The accent
    # mark stays from the OpenJTalk span. Both describe the same word, so the
    # mora counts normally match; if they drift (or for numeric / readable /
    # English-compound tokens whose furigana isn't the spoken reading) we keep
    # the engine's mora text rather than mis-zip the two sequences.
    token_morae = _split_morae(token_furigana)
    if not (is_numeric or is_readable_compound or is_english_compound) and len(
        token_morae
    ) == len(voiced_span):
        accents = [
            AccentInfo(
                furigana=token_morae[idx],
                accent_marking_type=voiced_span[idx]["accent"],
                length=len(token_morae[idx]),
            )
            for idx in range(len(voiced_span))
        ]
    else:
        accents = [
            AccentInfo(
                furigana=e["text"],
                accent_marking_type=e["accent"],
                length=len(e["text"]),
            )
            for e in voiced_span
        ]
    # `kernel_absorbed`: UniDic says this word has a kernel (lexical_kernel
    # >= 1) but OpenJTalk's per-mora output for its range carries no FALL. This
    # typically happens when the word sits in the medial position of a long
    # prosodic phrase and OpenJTalk's CRF collapses its kernel into the
    # surrounding contour (the 忙しい-inside-お忙しい中 case).
    kernel_absorbed = (
        isinstance(lexical_kernel, int)
        and lexical_kernel >= 1
        and not any(a.accent_marking_type == 2 for a in accents)
    )
    # Numerics and readable-symbol compounds (e.g. `2%`) carry no kana
    # furigana of their own — surface OpenJTalk's reading instead.
    display = (
        "".join(e["text"] for e in voiced_span)
        if (is_numeric or is_readable_compound or is_english_compound)
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


def _align_accent(
    furigana_results: list[WordResult],
    accent_results: list[dict[str, Any]],
    expected_mora_counts: list[int | None] | None = None,
) -> list[WordAccentResult]:
    """Align tokens with OpenJTalk per-mora entries via global DP.

    Returns one WordAccentResult per input token. Each token consumes a
    (possibly empty) contiguous span of OpenJTalk entries; the assignment that
    minimises total mismatch cost wins.
    """
    n = len(furigana_results)
    m = len(accent_results)

    if n == 0:
        return []
    if m == 0:
        return [_fallback_word(t) for t in furigana_results]

    # Pre-compute per-token classification and OpenJTalk texts.
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
    accent_texts = [e["text"] for e in accent_results]
    numeric_targets = (
        expected_mora_counts
        if expected_mora_counts is not None
        else _numeric_mora_targets(furigana_results, token_kinds, accent_texts)
    )

    # dp[i][j] = best cost aligning tokens [0..i) to accent entries [0..j).
    dp: list[list[float]] = [[_INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[int]] = [[-1] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0

    for i in range(n):
        token = furigana_results[i]
        is_num, is_pct, is_compound, is_eng = token_kinds[i]
        numeric_target = numeric_targets[i]
        for j in range(m + 1):
            base = dp[i][j]
            if base == _INF:
                continue
            token_limit = _K_MAX
            if is_num:
                token_limit = max(token_limit, len(token.surface) * 4)
                if numeric_target is not None:
                    token_limit = max(token_limit, numeric_target)
            elif is_pct:
                token_limit = 1
            elif is_compound:
                token_limit = max(token_limit, len(token.surface) * 4 + 8)
            elif is_eng:
                token_limit = max(token_limit, len(token.surface) * 4)
            elif token.base is None and token.pos is None:
                token_limit = max(token_limit, len(token.surface) * 4 + 4)
            else:
                # The kana match rejects spans whose normalized text length
                # differs by more than three characters. Since each OpenJTalk
                # entry contributes one mora, larger spans cannot produce a
                # finite cost and only multiply the DP search space.
                token_limit = min(_K_MAX, len(_split_morae(token.furigana)) + 3)
            k_limit = min(token_limit, m - j)
            for k in range(0, k_limit + 1):
                cost = _match_cost(
                    token,
                    accent_texts[j : j + k],
                    is_num,
                    is_pct,
                    is_compound,
                    is_eng,
                )
                if cost == _INF:
                    continue
                if (is_num or is_compound or is_eng) and numeric_target is not None:
                    cost += 0.01 * abs(k - numeric_target)
                new_cost = base + cost
                if new_cost < dp[i + 1][j + k]:
                    dp[i + 1][j + k] = new_cost
                    back[i + 1][j + k] = j

    best_j = m
    best_cost = dp[n][m]

    if best_cost == _INF:
        logger.error(
            "DP alignment found no valid path (n=%d, m=%d); falling back per token.",
            n,
            m,
        )
        return [_fallback_word(t) for t in furigana_results]

    # Backtrack to recover the OpenJTalk span each token consumed.
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
        _build_word_result(furigana_results[i], accent_results[s:e])
        for i, (s, e) in enumerate(spans)
    ]


async def align_accent(
    furigana_results: list[WordResult],
    accent_results: list[dict[str, Any]],
    expected_mora_counts: list[int | None] | None = None,
) -> list[WordAccentResult]:
    def run() -> list[WordAccentResult]:
        with _ALIGN_LOCK:
            return _align_accent(
                furigana_results,
                accent_results,
                expected_mora_counts,
            )

    return await asyncio.to_thread(run)
