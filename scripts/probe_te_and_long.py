"""Probe (1) te-form chains and (2) long-sentence behaviour of OJAD vs UniDic.

The intuition we want to verify is the user's anecdotal observation: in long
sentences OJAD seems to "omit" accent marks. We measure:

  - density: # of FALL markers (accent=2) per Japanese mora in the OJAD output
  - density: # of content-word morphemes UniDic flags as having a concrete
    (single-int) `aType >= 1` per Japanese mora
  - whether the FALL markers OJAD does emit are aligned with UniDic content
    words

Also dump the per-character OJAD output for a handful of te-form chains and
long sentences so we can eyeball.
"""

from __future__ import annotations

import asyncio
import re
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
            "cType": getattr(feat, "cType", None),
            "cForm": getattr(feat, "cForm", None),
            "lemma": getattr(feat, "lemma", None),
            "aType": getattr(feat, "aType", None),
            "aConType": getattr(feat, "aConType", None),
        })
    return out


def render_curve(ojad: list[dict]) -> str:
    out = []
    for e in ojad:
        ch = e.get("text", "")
        code = e.get("accent", 0)
        if code == 2:
            out.append(f"{ch}↓")
        elif code == 1:
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


_KANA = re.compile(r"[ぁ-んァ-ンー]")


def kana_count(s: str) -> int:
    return len(_KANA.findall(s))


def count_falls(ojad: list[dict]) -> int:
    return sum(1 for e in ojad if e.get("accent") == 2)


def count_high_plains(ojad: list[dict]) -> int:
    return sum(1 for e in ojad if e.get("accent") == 1)


def fall_positions(ojad: list[dict]) -> list[int]:
    return [i + 1 for i, e in enumerate(ojad) if e.get("accent") == 2]


def is_content_morph(m: dict) -> bool:
    """A morpheme we'd EXPECT OJAD to mark an accent on (kernel or heiban-tag).
    Excludes particles, auxiliaries, punctuation."""
    if not m["pos1"]:
        return False
    return m["pos1"] in {"名詞", "動詞", "形容詞", "形状詞", "副詞", "代名詞", "連体詞", "感動詞"}


def content_words_with_concrete_atype(morphs: list[dict]) -> list[dict]:
    """Content words whose UniDic aType is a single int (not '*', not 'X,Y')."""
    out = []
    for m in morphs:
        if not is_content_morph(m):
            continue
        a = m["aType"]
        if a in (None, "*"):
            continue
        try:
            int(a)
            out.append(m)
        except ValueError:
            continue
    return out


# ----- probes -----

TE_CHAINS = [
    "朝起きて、顔を洗って、ご飯を食べて、学校に行きます。",
    "本を読んで、音楽を聞いて、寝ます。",
    "雨が降って、風が吹いて、寒くなってきた。",
    "走って、転んで、泣いて、また走った。",
    # te-iru / te-aru / te-shimau / te-iku / te-kuru
    "雨が降っている。",
    "ドアが開いてある。",
    "全部食べてしまった。",
    "だんだん寒くなっていく。",
    "桜が咲いてきた。",
]

LONG_SENTENCES = [
    # short baseline
    "今日は天気がいい。",
    # medium
    "明日、友達と新宿で映画を見てから、夕食を食べる予定です。",
    # long, multi-clause
    "去年の夏、家族と一緒に北海道へ旅行に行って、美味しい海鮮料理を食べたり、温泉に入ったり、雄大な自然を楽しんだりして、本当に素晴らしい思い出を作ることができました。",
    # very long, formal register
    "本日は、皆様お忙しい中、私どものイベントにお越しいただき、誠にありがとうございます。これから、新製品の特徴と、開発に至った経緯、そして今後の展望について、順を追ってご説明させていただきます。",
    # technical / containing katakana
    "コンピューターサイエンスの分野では、機械学習や深層学習といった技術が急速に発展しており、自然言語処理や画像認識の精度も年々向上しています。",
]


async def main() -> None:
    rows = ["# Te-form chains and long-sentence OJAD probe\n"]

    rows.append("## Part 1 — te-form chains\n")
    rows.append("Looking for: does OJAD mark each clause's kernel, or do the "
                "kernels of medial clauses get dropped?\n")
    async with httpx.AsyncClient(timeout=30.0) as client:
        for sentence in TE_CHAINS:
            morphs = fugashi_morphemes(sentence)
            try:
                _, ojad = await get_ojad_result(sentence, client)
            except Exception as e:
                rows.append(f"### `{sentence}`\nERROR: {e}\n")
                continue

            content_with_atype = content_words_with_concrete_atype(morphs)
            falls = count_falls(ojad)
            highs = count_high_plains(ojad)
            kana = kana_count("".join(e.get("text", "") for e in ojad))

            rows.append(f"### `{sentence}`\n")
            rows.append(f"**OJAD curve:** `{render_curve(ojad)}`\n")
            rows.append(
                f"- OJAD output length: {len(ojad)} chars "
                f"({kana} kana-mora), "
                f"FALL markers: {falls}, HIGH-plain markers: {highs}"
            )
            rows.append(
                f"- UniDic content words with concrete aType: "
                f"{len(content_with_atype)} "
                f"({', '.join(m['surface']+'@'+m['aType'] for m in content_with_atype)})"
            )
            rows.append(f"- FALL positions (1-indexed): {fall_positions(ojad)}")
            rows.append("")

        rows.append("\n## Part 2 — increasing sentence length\n")
        rows.append("Looking for: does the FALL-marker density drop as the "
                    "sentence gets longer?\n")
        rows.append(
            "| len (chars) | OJAD chars | kana | FALLs | HIGHs | "
            "UniDic content / concrete-aType | FALLs / 100 mora |"
        )
        rows.append("|---|---|---|---|---|---|---|")

        long_details: list[tuple[str, list[dict], list[dict]]] = []
        for sentence in LONG_SENTENCES:
            morphs = fugashi_morphemes(sentence)
            try:
                _, ojad = await get_ojad_result(sentence, client)
            except Exception as e:
                rows.append(f"| {len(sentence)} | ERROR: {e} | | | | | |")
                continue

            falls = count_falls(ojad)
            highs = count_high_plains(ojad)
            kana = kana_count("".join(e.get("text", "") for e in ojad))
            content = [m for m in morphs if is_content_morph(m)]
            concrete = content_words_with_concrete_atype(morphs)
            density = (falls / kana * 100) if kana else 0
            rows.append(
                f"| {len(sentence)} | {len(ojad)} | {kana} | {falls} | {highs} "
                f"| {len(content)} / {len(concrete)} | {density:.1f} |"
            )
            long_details.append((sentence, morphs, ojad))

        rows.append("")
        rows.append("### Per-sentence dump\n")
        for sentence, morphs, ojad in long_details:
            rows.append(f"**Input:** `{sentence}`\n")
            rows.append(f"**OJAD:** `{render_curve(ojad)}`\n")
            content = [m for m in morphs if is_content_morph(m)]
            concrete = content_words_with_concrete_atype(morphs)
            rows.append(
                f"UniDic content words ({len(content)}): "
                + ", ".join(f"{m['surface']}({m['pos1']},a={m['aType']})"
                            for m in content)
            )
            rows.append(
                f"\nUniDic-content kernels claimed: "
                f"{len(concrete)} / {len(content)}; "
                f"OJAD FALL marks emitted: {count_falls(ojad)}; "
                f"FALL positions: {fall_positions(ojad)}"
            )
            rows.append("")

    print("\n".join(rows))


if __name__ == "__main__":
    asyncio.run(main())
