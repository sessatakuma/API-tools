# Local UniDic spike — runbook

Worktree path: `/home/torridfish/Code/spike-local-unidic`
Branch: `spike/local-unidic` (off `feat/yahoo-ma-migration`)
Tracks: issue #50.

## What this spike answers

Is local fugashi + NINJAL UniDic a viable replacement for the current
`Yahoo MA-UniDic (HTTP) + OJAD scrape` pipeline? Three sub-questions:

1. **Tokenisation parity**: do local UniDic and Yahoo MA-UniDic produce
   the same morpheme list?
2. **POS parity**: does local UniDic provide the same
   `pos / lemma / cType / cForm` we use in `apply_accent_patches`?
3. **Accent quality**: does UniDic's `aType` (per-morpheme accent class)
   match OJAD's predictions closely enough to replace OJAD?

The accent question is the killer: if `aType` is good, we drop the
fragile OJAD HTML scrape; if not, the spike still saves us one HTTP
round-trip (Yahoo MA) but OJAD stays.

## Steps to run

```bash
cd ~/Code/spike-local-unidic

# 1. Install dependencies (only in this worktree's pyproject.toml).
#    fugashi needs libmecab — on Ubuntu/Debian:
#      sudo apt-get install -y libmecab-dev mecab
#    On macOS:
#      brew install mecab
uv add fugashi unidic

# 2. Download the NINJAL UniDic dictionary (~700 MB, downloads from
#    NINJAL on first run — may take a few minutes).
uv run python -m unidic download

# 3. Sanity check — print the dictionary version and one sample token.
uv run python -c "
import fugashi, json
t = fugashi.Tagger()
print('dict info:', json.dumps(t.dictionary_info, ensure_ascii=False, default=str))
for tok in t('毎朝コーヒーを飲みます'):
    print(tok.surface, tok.feature)
"

# 4. Run the full spike (writes markdown to stdout — pipe to a file).
#    Takes a few minutes — it calls Yahoo MA + OJAD for each comparison.
uv run python scripts/spike_local_unidic.py > docs/spike-local-unidic.md

# 5. Review the report
less docs/spike-local-unidic.md
```

## After the report

Fill in the **Recommendation** section at the bottom of
`docs/spike-local-unidic.md` based on the actual numbers:

- aType vs OJAD agreement ≥ 80% → GO (drop OJAD entirely in #50 PR)
- aType vs OJAD agreement 50-80% → PARTIAL (replace MA but keep OJAD)
- aType vs OJAD agreement < 50% → NO-GO (stay on PR #49 pipeline)

Then commit + push:

```bash
git add docs/spike-local-unidic.md
git commit -m "spike(unidic): record measurement report (refs #50)"
# Optionally push if you want to share the report:
# git push -u origin spike/local-unidic
```

When the spike is done with, drop the worktree:

```bash
cd ~/Code/API-tools
git worktree remove ../spike-local-unidic
git branch -D spike/local-unidic   # or keep it if you opened a PR
```

## Known wrinkles

- `unidic` (full) downloads on-demand from NINJAL's server. If the
  server is slow or down, fall back to `unidic-lite` (~40 MB, bundled
  with the wheel) — but **the lite version lacks `aType`**, so the
  accent comparison won't be meaningful. The script handles missing
  `aType` gracefully (shows `-` and `agree=-`), so you'll see a
  half-useful report.
- Yahoo MA + OJAD calls in the corpus-parity section need
  `YAHOO_API_KEY` set in `.env` — same as the existing pipeline.
- The OJAD comparison hits `gavo.t.u-tokyo.ac.jp` for every word in
  `ACCENT_PROBE` (50 tokens). Don't be surprised if it takes a couple
  minutes.
- The `aType` field in NINJAL UniDic can be a string like `"2,3"` for
  ambiguous cases. The script handles non-int values by skipping the
  comparison for that row.
