"""OpenJTalk in-process pitch accent — the offline accent engine.

Produces the same `(paragraph, [{text, accent}, ...])` per-mora shape the
aligner expects, so it drops straight into `align_accent`. Accent comes from
OpenJTalk's full-context labels (accent-phrase segmentation + per-phrase
nucleus), mapped to the marking convention used throughout the accent package:

    0 = LOW / unaccented
    1 = HIGH plateau
    2 = FALL kernel (the last HIGH mora before the pitch drop)

This needs no network — it runs MeCab + the bundled open_jtalk_dic in-process
(~23 MB dictionary).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
import jaconv
import pyopenjtalk

from api.accent.fullcontext import accent_markings_from_labels

logger = logging.getLogger("api")

# Katakana mora = one base kana + optional small kana (拗音 ゃゅょ, ァ-ォ, ヮ).
_KATA_MORA_RE = re.compile(r".[ャュョゃゅょァィゥェォヮ]?")

# Punctuation / iteration marks that live inside the katakana block
# (U+30A0–U+30FF) but are NOT spoken morae: `゠` (double hyphen), `・`
# (middle dot, common in foreign names like バラク・オバマ), and the
# iteration marks `ヽヾヿ`. `g2p(kana=True)` echoes these but
# `extract_fullcontext` drops them, so they must be filtered to keep the
# two sequences the same length. `ー` (long-vowel mark, U+30FC) IS a mora
# and is intentionally kept. Mirrors `align.is_kana_or_kanji`.
_KATA_BLOCK_PUNCT = frozenset("゠・ヽヾヿ")


def _accent_markings(text: str) -> list[int]:
    """One 0/1/2 marking per mora, in reading order, across all accent phrases."""
    labels = pyopenjtalk.extract_fullcontext(text)
    return accent_markings_from_labels(labels)


def _is_kana_mora(mora: str) -> bool:
    """True for a real kana mora; False for punctuation g2p passes through.

    `g2p(kana=True)` echoes punctuation (`。`, `、`, `？` …) into its output,
    but `extract_fullcontext` skips it (sil/pau). The two sequences are zipped
    by index downstream, so they must be filtered to the same set — otherwise
    every per-mora accent mark after a mid-sentence punctuation shifts by one.
    Spoken katakana (incl. the `ー` length mark and small kana) live in
    U+30A0–U+30FF, but so do a few non-spoken marks (`゠・ヽヾヿ`) which must
    also be excluded — see `_KATA_BLOCK_PUNCT`.
    """
    return (
        bool(mora)
        and 0x30A0 <= ord(mora[0]) <= 0x30FF
        and mora[0] not in _KATA_BLOCK_PUNCT
    )


def _kana_morae(text: str) -> list[str]:
    """Hiragana morae in reading order, punctuation dropped (one per mora)."""
    kata = pyopenjtalk.g2p(text, kana=True)
    return [
        jaconv.kata2hira(m) for m in _KATA_MORA_RE.findall(kata) if _is_kana_mora(m)
    ]


async def get_openjtalk_result(
    query_text: str,
    client: httpx.AsyncClient,  # unused; kept so the call site stays uniform
) -> tuple[str, list[dict[str, Any]]]:
    """In-process, fully offline pitch-accent enrichment.

    Returns `(paragraph, results)` where `results` is a flat list of
    `{"text": <hiragana mora>, "accent": 0|1|2}` for the whole input.
    """
    logger.debug(f"[OpenJTalk] Tagging: {query_text}")
    morae = _kana_morae(query_text)
    markings = _accent_markings(query_text)

    # Both come from the same OpenJTalk frontend, so mora counts normally
    # agree. If they drift (rare kana-vs-phoneme edge cases), align on the
    # shorter list and pad the rest LOW rather than failing.
    n = min(len(morae), len(markings))
    if len(morae) != len(markings):
        logger.warning(
            "[OpenJTalk] mora/accent length mismatch (%d kana vs %d accent) for %r",
            len(morae),
            len(markings),
            query_text,
        )
    results = [{"text": morae[i], "accent": markings[i]} for i in range(n)]
    for i in range(n, len(morae)):
        results.append({"text": morae[i], "accent": 0})

    paragraph = "".join(morae)
    return paragraph, results
