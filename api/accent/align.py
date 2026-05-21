"""Align Yahoo Furigana tokens with OJAD per-mora accent entries.

`align_accent()` walks the Yahoo token list and consumes OJAD entries
in order, emitting one `WordAccentResult` per Yahoo token. Numeric
tokens have no Yahoo furigana to compare against so they use a
length-anchor heuristic (stop when OJAD reaches the next Yahoo
token's reading); kana / kanji tokens use literal length + content
matching under kata2hira folding.

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
        # '゠', '・', 'ー', 'ヽ', 'ヾ', 'ヿ' which should be regard as punchutation
        return False
    kana = range(0x3040, 0x30FF + 1)
    kanji = range(0x4E00, 0x9FFF + 1)
    if ord(char) in kana or ord(char) in kanji:
        return True
    return False


async def align_accent(
    furigana_results: list[Any], ojad_results: list[dict[str, Any]]
) -> list[WordAccentResult]:
    """Align yahoo furigana with OJAD results, return final accent marked result."""
    final_response_results = []
    ojad_idx_cnt = 0

    logger.debug(f"🔍 [Data Check] First item:{furigana_results[0]}")

    for i, furigana_result in enumerate(furigana_results):
        yahoo_furigana = furigana_result.furigana
        yahoo_surface = furigana_result.surface

        yahoo_furigana_hira = jaconv.kata2hira(yahoo_furigana)
        accents: list[AccentInfo] = []

        logger.debug(f"Processing Yahoo Word [{i}]: {yahoo_surface} ({yahoo_furigana})")

        # Identify if the word is numeric
        is_numeric = bool(numeric_pattern.match(yahoo_surface))

        # ignore non-kana/kanji and non-numeric words
        if (
            not furigana_result.subword
            and any(not is_kana_or_kanji(chr) for chr in yahoo_furigana)
            and not is_numeric
        ):
            logger.debug(" -> Skipped (Not Kana/Kanji)")
            accents.append(
                AccentInfo(
                    furigana=yahoo_surface,
                    accent_marking_type=0,
                    length=len(yahoo_surface),
                )
            )
            final_response_results.append(
                WordAccentResult(
                    furigana=yahoo_furigana, surface=yahoo_surface, accent=accents
                )
            )

            # Move OJAD index if skipped punctuation
            if ojad_idx_cnt < len(ojad_results) and jaconv.kata2hira(
                ojad_results[ojad_idx_cnt]["text"].strip()
            ) in ["、", "。", ",", "."]:
                ojad_idx_cnt += 1
            continue

        # Synchronize OJAD index
        ojad_idx = ojad_idx_cnt

        # Check OJAD boundary
        if ojad_idx >= len(ojad_results):
            logger.warning(f" -> OJAD Index Out of Bounds ({ojad_idx})")
        else:
            logger.debug(
                f"-> Comparing Yahoo '{yahoo_furigana_hira}'"
                f" vs OJAD '{ojad_results[ojad_idx]['text']}'"
            )

        # Move non-numeric OJAD index to the matching position
        if not is_numeric:
            while ojad_idx < len(ojad_results) and not yahoo_furigana_hira.startswith(
                jaconv.kata2hira(ojad_results[ojad_idx]["text"])
            ):
                ojad_idx += 1

        # catch the furigana from Yahoo with OJAD results
        ojad_furigana = ""
        temp_accents = []  # Use temp list to avoid partial data

        # Define anchor(next Yahoo furigana) for numeric mode
        next_yahoo_furigana = None
        if i + 1 < len(furigana_results):
            next_yahoo_furigana = jaconv.kata2hira(furigana_results[i + 1].furigana)

        # Backup index
        temp_ojad_idx = ojad_idx

        # Number mode: grab OJAD until the anchor
        if is_numeric:
            while temp_ojad_idx < len(ojad_results):
                raw_text = ojad_results[temp_ojad_idx]["text"].strip()
                ojad_text = jaconv.kata2hira(raw_text)

                # Stop if reached the anchor
                if next_yahoo_furigana and next_yahoo_furigana.startswith(ojad_text):
                    break

                # Stop if consumed too much data
                if len(ojad_furigana) > max(len(yahoo_surface) * 4, 12):
                    logger.warning(
                        f" -> Numeric consumption exceeded limit '{yahoo_surface}'."
                    )
                    break

                ojad_furigana += ojad_text
                temp_accents.append(
                    AccentInfo(
                        furigana=ojad_text,
                        accent_marking_type=ojad_results[temp_ojad_idx]["accent"],
                        length=len(ojad_text),
                    )
                )
                temp_ojad_idx += 1
        # Normal mode: grab OJAD until length match
        else:
            while len(ojad_furigana) < len(yahoo_furigana) and temp_ojad_idx < len(
                ojad_results
            ):
                ojad_text = ojad_results[temp_ojad_idx]["text"]
                ojad_furigana += ojad_text
                temp_accents.append(
                    AccentInfo(
                        furigana=ojad_text,
                        accent_marking_type=ojad_results[temp_ojad_idx]["accent"],
                        length=len(ojad_text),
                    )
                )
                temp_ojad_idx += 1

        # Final matching check
        is_match = False
        if is_numeric:
            # Numeric mode: only check if OJAD has furigana grabbed
            is_match = len(ojad_furigana) > 0
        else:
            # Normal mode: check length and content
            is_match = len(ojad_furigana) == len(yahoo_furigana) and jaconv.kata2hira(
                ojad_furigana
            ) == jaconv.kata2hira(yahoo_furigana)

        if is_match:
            logger.debug(f" -> MATCHED! OJAD: {ojad_furigana}")
            accents.extend(temp_accents)

            # Build final accent info list
            accent_info_list = []
            for idx, accent in enumerate(accents):
                accent_info_list.append(
                    AccentInfo(
                        furigana=accent.furigana,
                        accent_marking_type=accent.accent_marking_type,
                        length=accent.length,
                    )
                )

            ojad_idx_cnt = temp_ojad_idx  # Update global index

            display_furigana = ojad_furigana if is_numeric else yahoo_furigana

            # Build final response
            if furigana_result.subword:
                yahoo_subword = furigana_result.subword
                logger.debug(
                    f"[Type Check] yahoo_subword element type: {type(yahoo_subword[0])}"
                )
                logger.debug(f"[Data Check] yahoo_subword content: {yahoo_subword}")
                final_response_results.append(
                    WordAccentResult(
                        furigana=display_furigana,
                        surface=yahoo_surface,
                        accent=accent_info_list,
                        subword=[
                            WordResult(furigana=s.furigana, surface=s.surface)
                            for s in yahoo_subword
                        ],
                    )
                )
            else:
                final_response_results.append(
                    WordAccentResult(
                        furigana=display_furigana,
                        surface=yahoo_surface,
                        accent=accent_info_list,
                    )
                )
        else:
            # [ERROR BLOCK]
            logger.error(
                "-> MATCH FAILED."
                f"Yahoo: {yahoo_furigana} vs OJAD Assembly: {ojad_furigana}"
            )

            # Fallback to Yahoo furigana with no accent info
            accent_info = AccentInfo(
                furigana=yahoo_furigana,
                accent_marking_type=0,
                length=len(yahoo_furigana),
            )

            final_response_results.append(
                WordAccentResult(
                    furigana=yahoo_furigana,
                    surface=yahoo_surface,
                    accent=[accent_info],
                )
            )

            # Move OJAD index to next item to avoid infinite loop
            if ojad_idx_cnt < len(ojad_results):
                ojad_idx_cnt += 1

    return final_response_results
