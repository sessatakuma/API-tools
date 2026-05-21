"""Spike: evaluate fugashi + NINJAL UniDic as a local replacement for
the Yahoo MA-UniDic + OJAD pipeline currently used by accent_marker.

Output: markdown report on stdout. Pipe to `docs/spike-local-unidic.md`.

This script is throwaway — it lives only on branch `spike/local-unidic`.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

# Allow running from the worktree root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import YAHOO_API_KEY  # noqa: E402

try:
    import fugashi
except ImportError:
    print("ERROR: fugashi not installed. Run `uv add fugashi unidic` first.", file=sys.stderr)
    sys.exit(1)

# --------------------------- setup ---------------------------

_TAGGER: fugashi.Tagger | None = None


def get_tagger() -> fugashi.Tagger:
    """Lazy-instantiate the tagger. Will raise if the unidic dict isn't
    downloaded yet — run `uv run python -m unidic download` first.
    """
    global _TAGGER
    if _TAGGER is None:
        _TAGGER = fugashi.Tagger()
    return _TAGGER


_YAHOO_MA_URL = "https://jlp.yahooapis.jp/MAService/V2/parse"
_YAHOO_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": f"Yahoo AppID: {YAHOO_API_KEY}",
}


async def yahoo_ma_parse(text: str, client: httpx.AsyncClient) -> list[list[str]]:
    body = {
        "id": "1",
        "jsonrpc": "2.0",
        "method": "jlp.maservice.parse.unidic",
        "params": {"q": text},
    }
    r = await client.post(_YAHOO_MA_URL, headers=_YAHOO_HEADERS, json=body)
    r.raise_for_status()
    return r.json().get("result", {}).get("tokens", [])


def fugashi_parse(text: str) -> list[dict]:
    """Tokenise with fugashi and return one dict per morpheme exposing every
    field UniDic carries. Field availability depends on the dict version.
    """
    tagger = get_tagger()
    out: list[dict] = []
    for token in tagger(text):
        feat = token.feature
        # Try all known UniDic field names. Some may be None depending on
        # dict version (lite vs full vs csj).
        out.append({
            "surface": token.surface,
            "pos1": getattr(feat, "pos1", None),
            "pos2": getattr(feat, "pos2", None),
            "pos3": getattr(feat, "pos3", None),
            "pos4": getattr(feat, "pos4", None),
            "cType": getattr(feat, "cType", None),
            "cForm": getattr(feat, "cForm", None),
            "lForm": getattr(feat, "lForm", None),
            "lemma": getattr(feat, "lemma", None),
            "orth": getattr(feat, "orth", None),
            "pron": getattr(feat, "pron", None),
            "orthBase": getattr(feat, "orthBase", None),
            "pronBase": getattr(feat, "pronBase", None),
            "goshu": getattr(feat, "goshu", None),
            "iType": getattr(feat, "iType", None),
            "iForm": getattr(feat, "iForm", None),
            "fType": getattr(feat, "fType", None),
            "fForm": getattr(feat, "fForm", None),
            "kana": getattr(feat, "kana", None),
            "kanaBase": getattr(feat, "kanaBase", None),
            "aType": getattr(feat, "aType", None),
            "aConType": getattr(feat, "aConType", None),
            "aModType": getattr(feat, "aModType", None),
        })
    return out


# --------------------------- probe data ---------------------------

PROBE_SET = [
    ("毎朝コーヒーを飲みます", "ます = 助動詞・基本形 (kernel on ま)"),
    ("ラーメンが食べたい", "たい = 助動詞・基本形 (kernel on た)"),
    ("昨日映画を見ました", "ました — kernel on ま of まし"),
    ("そこまでは行きません", "ません — kernel on せ (NOT ま)"),
    ("ご飯を食べませんでした", "ませんでした — kernel on せ"),
    ("彼を励ます", "励ます = 動詞・五段・基本形 (NOT 助動詞)"),
    ("彼に励まされる", "励ます passive form (still 動詞)"),
    ("升は古い容器です", "升 = 名詞 (NOT 助動詞)"),
    ("酒を一升ください", "升 as counter (名詞 or 助数詞)"),
    ("電車に間に合います", "間に合います — straightforward ます"),
    ("怠けたかった", "たかった = たい past"),
    ("泣きたくない", "たくない = たい + ない"),
    ("休みたかったけど", "past たい with conjunctive"),
]


# 50 sample tokens for accent-class comparison. Chosen to span:
# - Known atamadaka (1), nakadaka (2-3), heiban (0) nouns
# - Common verbs in basic form
# - Adjectives
ACCENT_PROBE = [
    "雨", "水", "山", "川", "花", "犬", "猫", "鳥", "魚", "本",
    "猛", "桜", "電車", "学校", "会社", "新聞", "音楽", "言葉", "時間", "問題",
    "食べる", "飲む", "見る", "行く", "来る", "書く", "読む", "話す", "聞く", "歩く",
    "美しい", "楽しい", "高い", "低い", "新しい", "古い", "大きい", "小さい", "速い", "遅い",
    "毎朝", "昨日", "今日", "明日", "今年", "去年", "来年", "私", "彼", "彼女",
]


# --------------------------- report sections ---------------------------


def section_header() -> str:
    from importlib.metadata import version
    info = get_tagger().dictionary_info
    try:
        fugashi_v = version("fugashi")
    except Exception:
        fugashi_v = "(unknown)"
    return f"""# Local UniDic spike report (Issue #50)

Spike to evaluate whether `fugashi` + NINJAL UniDic (in-process) can
replace the current `Yahoo MA-UniDic (HTTP) + OJAD scrape` pipeline.

## Library / dictionary info

```
fugashi: {fugashi_v}
dictionary info: {json.dumps(info, ensure_ascii=False, indent=2, default=str)}
```
"""


def section_schema_dump() -> str:
    sample = "毎朝コーヒーを飲みます"
    tokens = fugashi_parse(sample)
    rows = ["## Schema dump (per-morpheme fields)\n",
            f"Input: `{sample}`\n",
            "| surface | pos1 | pos2 | cType | cForm | lemma | aType | aConType |",
            "|---|---|---|---|---|---|---|---|"]
    for t in tokens:
        rows.append(
            f"| {t['surface']} | {t['pos1'] or '-'} | {t['pos2'] or '-'} "
            f"| {t['cType'] or '-'} | {t['cForm'] or '-'} | {t['lemma'] or '-'} "
            f"| {t['aType'] or '-'} | {t['aConType'] or '-'} |"
        )
    rows.append("")
    rows.append("Full first-token dump:")
    rows.append("```python")
    rows.append(json.dumps(tokens[0], ensure_ascii=False, indent=2, default=str))
    rows.append("```")
    return "\n".join(rows)


async def section_probe_set() -> str:
    """Run the PR #49 probe set, show fugashi output side-by-side with Yahoo MA."""
    rows = ["## Disambiguation probe (PR #49 set)\n"]
    async with httpx.AsyncClient(timeout=15.0) as client:
        for sentence, expectation in PROBE_SET:
            rows.append(f"### `{sentence}`\n")
            rows.append(f"Expectation: {expectation}\n")
            # Local
            local_tokens = fugashi_parse(sentence)
            rows.append("**Local fugashi+UniDic:**\n")
            rows.append("| surface | pos1 | pos2 | cType | cForm | lemma | aType |")
            rows.append("|---|---|---|---|---|---|---|")
            for t in local_tokens:
                rows.append(
                    f"| {t['surface']} | {t['pos1'] or '-'} | {t['pos2'] or '-'} "
                    f"| {t['cType'] or '-'} | {t['cForm'] or '-'} | {t['lemma'] or '-'} "
                    f"| {t['aType'] or '-'} |"
                )
            # Yahoo MA-UniDic
            try:
                yahoo_tokens = await yahoo_ma_parse(sentence, client)
            except Exception as e:
                rows.append(f"\n(Yahoo MA error: {e})\n")
                continue
            rows.append("\n**Yahoo MA-UniDic:**\n")
            rows.append("| surface | reading | base | pos | pos1 | cType | cForm |")
            rows.append("|---|---|---|---|---|---|---|")
            for row in yahoo_tokens:
                rows.append(
                    f"| {row[0]} | {row[1]} | {row[2]} | {row[3]} | "
                    f"{row[4] if row[4] != '*' else '-'} | "
                    f"{row[5] if row[5] != '*' else '-'} | "
                    f"{row[6] if row[6] != '*' else '-'} |"
                )
            rows.append("")
    return "\n".join(rows)


async def section_corpus_parity() -> str:
    """Process test_0.txt and test_1.txt line-by-line, computing parity stats."""
    rows = ["## Corpus parity (test_0.txt + test_1.txt)\n"]
    files = [Path("data/test_0.txt"), Path("data/test_1.txt")]
    async with httpx.AsyncClient(timeout=20.0) as client:
        for f in files:
            if not f.exists():
                rows.append(f"### {f.name}: NOT FOUND\n")
                continue
            text = f.read_text(encoding="utf-8")
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            local_total = yahoo_total = 0
            surface_ok = surface_mismatch = 0
            timings_local: list[float] = []
            timings_yahoo: list[float] = []
            mismatches: list[tuple[str, str, str]] = []

            for line in lines:
                if len(line.encode("utf-8")) > 3500:
                    continue
                # local
                t0 = time.perf_counter()
                local = fugashi_parse(line)
                timings_local.append((time.perf_counter() - t0) * 1000)
                # yahoo
                t0 = time.perf_counter()
                try:
                    yahoo = await yahoo_ma_parse(line, client)
                except Exception:
                    continue
                timings_yahoo.append((time.perf_counter() - t0) * 1000)

                local_concat = "".join(t["surface"] for t in local)
                yahoo_concat = "".join(t[0] for t in yahoo)
                if local_concat == yahoo_concat:
                    surface_ok += 1
                else:
                    surface_mismatch += 1
                    if len(mismatches) < 3:
                        mismatches.append((line[:40], local_concat[:40], yahoo_concat[:40]))
                local_total += len(local)
                yahoo_total += len(yahoo)

            rows.append(f"### {f.name}\n")
            rows.append(f"- non-empty lines processed: {surface_ok + surface_mismatch}")
            rows.append(f"- surface concat match: {surface_ok}, mismatch: {surface_mismatch}")
            rows.append(f"- token total: local={local_total}, yahoo={yahoo_total} "
                        f"(ratio: {local_total/max(yahoo_total,1):.3f})")
            if timings_local and timings_yahoo:
                rows.append(f"- timing per line (median): "
                            f"local={statistics.median(timings_local):.2f}ms, "
                            f"yahoo={statistics.median(timings_yahoo):.1f}ms "
                            f"(speedup: {statistics.median(timings_yahoo)/max(statistics.median(timings_local),0.01):.0f}x)")
            for orig, lc, yc in mismatches:
                rows.append(f"  - mismatch sample:")
                rows.append(f"    - input: {orig}")
                rows.append(f"    - local: {lc}")
                rows.append(f"    - yahoo: {yc}")
            rows.append("")
    return "\n".join(rows)


async def section_accent_vs_ojad() -> str:
    """Compare fugashi's aType against OJAD's per-mora prediction for each
    sample word. Calls into the existing get_ojad_result so we exercise
    the real OJAD path.
    """
    from api.accent_marker import get_ojad_result

    rows = ["## Accent class: fugashi `aType` vs OJAD prediction\n"]
    rows.append("`aType` is UniDic's kernel-position annotation (0 = heiban, "
                "1 = atamadaka, 2 = nakadaka with kernel on mora 2, etc.).")
    rows.append("For OJAD we find the position of the moji marked `accent=2` "
                "(FALL) in the per-moji output; absence of FALL means heiban.\n")
    rows.append("| surface | fugashi aType | OJAD kernel pos | agree? |")
    rows.append("|---|---|---|---|")

    agree = disagree = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for word in ACCENT_PROBE:
            local = fugashi_parse(word)
            atype = local[0].get("aType") if local else None
            # OJAD on the bare word
            try:
                _, ojad = await get_ojad_result(word, client)
            except Exception as e:
                rows.append(f"| {word} | {atype} | (OJAD error: {e}) | - |")
                continue
            kernel_pos = next((i + 1 for i, e in enumerate(ojad) if e.get("accent") == 2), 0)
            try:
                atype_int = int(atype) if atype not in (None, "*") else None
            except (TypeError, ValueError):
                atype_int = None
            ok = atype_int == kernel_pos if atype_int is not None else None
            mark = "✓" if ok else ("✗" if ok is False else "-")
            if ok is True:
                agree += 1
            elif ok is False:
                disagree += 1
            rows.append(f"| {word} | {atype or '-'} | {kernel_pos} | {mark} |")

    total = agree + disagree
    if total:
        rows.append(f"\n**Summary**: agree={agree}/{total} ({100*agree/total:.0f}%), "
                    f"disagree={disagree}/{total}")
    return "\n".join(rows)


def section_disk() -> str:
    """`du -sh` on the unidic dict path."""
    import shutil
    rows = ["## Disk impact\n"]
    try:
        import unidic
        dicdir = Path(unidic.DICDIR)
        if dicdir.exists():
            total = sum(f.stat().st_size for f in dicdir.rglob("*") if f.is_file())
            rows.append(f"- dict location: `{dicdir}`")
            rows.append(f"- dict size: {total / (1024**2):.0f} MB")
        else:
            rows.append(f"- unidic.DICDIR `{dicdir}` does not exist — run `uv run python -m unidic download` first.")
    except ImportError:
        rows.append("- `unidic` package not installed.")
    rows.append("- pyproject.toml additions: `fugashi`, `unidic`.")
    return "\n".join(rows)


def section_recommendation() -> str:
    return """## Recommendation

(Fill in after reviewing the data above. Suggested decision rule:)

- **GO** for issue #50 (replace Yahoo MA + OJAD with local UniDic) if:
  - Surface concat parity ≥ 95% on test_{0,1}.txt.
  - POS / cType / lemma agreement with Yahoo MA-UniDic ≥ 90% on the
    probe set (especially the ます/たい disambiguation).
  - `aType` agreement with OJAD ≥ 80% on the accent probe (this is the
    blocker for de-risking the OJAD scrape).
  - Local latency ≥ 10× faster than Yahoo MA per chunk.

- **NO-GO** (keep Yahoo MA + OJAD as in PR #49) if:
  - `aType` agreement is < 70% — too noisy to replace OJAD.
  - Disk impact (~700 MB) is unacceptable for the deploy target.
  - Significant POS divergence breaks `apply_accent_patches`.

- **PARTIAL** (use local UniDic for tokens, keep OJAD for accents) if:
  - POS quality is good but `aType` is noisy.
  - Saves the Yahoo MA round-trip but not the OJAD scrape.

The full data dump above should make the call obvious.
"""


# --------------------------- main ---------------------------


async def main() -> None:
    parts: list[str] = []
    parts.append(section_header())
    parts.append(section_schema_dump())
    parts.append(await section_probe_set())
    parts.append(await section_corpus_parity())
    parts.append(await section_accent_vs_ojad())
    parts.append(section_disk())
    parts.append(section_recommendation())
    print("\n\n".join(parts))


if __name__ == "__main__":
    asyncio.run(main())
