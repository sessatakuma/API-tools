"""Smoke-test the local-UniDic accent pipeline by calling the chunk processor
directly (no HTTP server needed).

Verifies: response shape, kernel_absorbed=True on the 忙しい case, PR #49
disambiguation probe still works, lexical_kernel populated on content words.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.accent_marker import _process_accent_chunk  # noqa: E402


def fmt_word(w: dict) -> str:
    """One-line summary of a WordAccentResult dict."""
    accents = "".join(
        ("↓" if a["accent_marking_type"] == 2 else
         "ˉ" if a["accent_marking_type"] == 1 else
         "_") + a["furigana"]
        for a in w.get("accent", [])
    )
    lk = w.get("lexical_kernel")
    lk_alts = w.get("lexical_kernel_alts")
    ka = w.get("kernel_absorbed", False)
    lk_str = f"lk={lk}" if lk is not None else "lk=-"
    if lk_alts:
        lk_str += f" alts={lk_alts}"
    if ka:
        lk_str += " 🔻ABSORBED"
    return f"  {w['surface']:8s} | {accents:30s} | {lk_str}"


PROBES = [
    # PR #49 disambiguation probe — sanity that local pipeline still nails it
    "毎朝コーヒーを飲みます",
    "ラーメンが食べたい",
    "そこまでは行きません",
    "彼を励ます",
    # te-form chains
    "朝起きて、顔を洗って、ご飯を食べて、学校に行きます。",
    # The 忙しい absorption case — should set kernel_absorbed=True
    "皆様お忙しい中、お越しいただきありがとうございます。",
    # Heiban word (学校) — should have no FALL but lexical_kernel=0
    "学校に行きます。",
    # Multi-reading aType case — 山 should give 2 with alts [2, 0]
    "山",
    "今日は天気がいい。",
]


async def main() -> None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        for text in PROBES:
            print(f"\n# `{text}`")
            resp = await _process_accent_chunk(text, client)
            if resp.error or resp.result is None:
                print(f"  ERROR status={resp.status} msg={resp.error.message if resp.error else '-'}")
                continue
            for w in resp.result:
                print(fmt_word(w.model_dump()))


if __name__ == "__main__":
    asyncio.run(main())
