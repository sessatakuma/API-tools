"""Probe verb conjugations against UniDic aType and OJAD per-mora output,
to see how the two sources behave on inflected forms (te-form, masu-form,
nai-form, passive, causative, te-iru, etc).

Output: markdown table to stdout.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fugashi  # noqa: E402

from api.accent_marker import get_ojad_result  # noqa: E402

_TAGGER = fugashi.Tagger()


def fugashi_morphemes(text: str) -> list[dict]:
    out = []
    for tok in _TAGGER(text):
        feat = tok.feature
        out.append({
            "surface": tok.surface,
            "pos1": getattr(feat, "pos1", None),
            "pos2": getattr(feat, "pos2", None),
            "cType": getattr(feat, "cType", None),
            "cForm": getattr(feat, "cForm", None),
            "lemma": getattr(feat, "lemma", None),
            "pron": getattr(feat, "pron", None),
            "aType": getattr(feat, "aType", None),
            "aConType": getattr(feat, "aConType", None),
        })
    return out


def render_curve(ojad: list[dict]) -> str:
    """LH curve. OJAD convention (from accent_marker.py): 0=LOW, 1=HIGH (plain),
    2=FALL (top — marks the kernel mora).
    """
    out = []
    for e in ojad:
        ch = e.get("text", "")
        code = e.get("accent", 0)
        if code == 2:
            out.append(f"{ch}↓")
        elif code == 1:
            out.append(f"{ch}̄")
        else:
            out.append(ch)
    return "".join(out)


def fall_position(ojad: list[dict]) -> int:
    """Position (1-indexed) of FALL mora, or 0 if no FALL (= heiban)."""
    for i, e in enumerate(ojad, 1):
        if e.get("accent") == 2:
            return i
    return 0


PROBES = [
    # (input, gloss)
    ("食べる", "taberu / 終止形"),
    ("食べます", "taberu + masu"),
    ("食べました", "taberu + mashita"),
    ("食べません", "taberu + masen"),
    ("食べた", "taberu + ta"),
    ("食べて", "taberu + te"),
    ("食べない", "taberu + nai"),
    ("食べたい", "taberu + tai"),
    ("食べられる", "taberu + rareru (passive/potential)"),
    ("食べさせる", "taberu + saseru (causative)"),
    ("食べている", "taberu + te + iru"),
    ("食べれば", "taberu + ba"),
    ("食べよう", "taberu + you (volitional)"),

    ("飲む", "nomu / 終止形"),
    ("飲みます", "nomu + masu"),
    ("飲んだ", "nomu + ta (with sound change)"),
    ("飲んで", "nomu + te"),
    ("飲まない", "nomu + nai"),
    ("飲まれる", "nomu + reru (passive)"),

    ("行く", "iku / 終止形"),
    ("行った", "iku + ta (irregular sound change)"),
    ("行って", "iku + te"),
    ("行きます", "iku + masu"),

    ("来る", "kuru / 終止形"),
    ("来た", "kuru + ta"),
    ("来て", "kuru + te"),
    ("来ます", "kuru + masu"),

    ("する", "suru / 終止形"),
    ("した", "suru + ta"),
    ("して", "suru + te"),
    ("します", "suru + masu"),

    ("勉強する", "benkyou-suru"),
    ("勉強します", "benkyou-suru + masu"),

    ("美しい", "utsukushii / 終止形"),
    ("美しかった", "utsukushii + katta"),
    ("美しくない", "utsukushii + ku + nai"),
]


def fmt_morphemes(morphs: list[dict]) -> str:
    parts = []
    for m in morphs:
        atype = m["aType"] or "-"
        actype = m["aConType"] or "-"
        parts.append(
            f"{m['surface']}({m['pos1'] or '-'}/{m['cForm'] or '-'}, "
            f"a={atype}, conn={actype})"
        )
    return " + ".join(parts)


async def main() -> None:
    rows = [
        "# Verb form probe: UniDic vs OJAD\n",
        "Each row shows: input → fugashi morphemes (with `aType` + `aConType`) → "
        "OJAD per-character output rendered with `↓` marking the FALL mora and "
        "`¯` marking explicit HIGH.\n",
    ]
    async with httpx.AsyncClient(timeout=15.0) as client:
        for sentence, gloss in PROBES:
            morphs = fugashi_morphemes(sentence)
            try:
                _, ojad = await get_ojad_result(sentence, client)
            except Exception as e:
                ojad = []
                ojad_err = str(e)
            else:
                ojad_err = None

            rows.append(f"## `{sentence}` — {gloss}\n")
            rows.append("**UniDic:**")
            rows.append("")
            rows.append(f"  {fmt_morphemes(morphs)}")
            rows.append("")
            if ojad_err:
                rows.append(f"**OJAD:** ERROR — {ojad_err}")
            else:
                fp = fall_position(ojad)
                rows.append(
                    "**OJAD:** `" + render_curve(ojad)
                    + f"` (FALL @ mora {fp if fp else '— (heiban)'})"
                )
            rows.append("")
    print("\n".join(rows))


if __name__ == "__main__":
    asyncio.run(main())
