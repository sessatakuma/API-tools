# API-tools

A FastAPI service that marks Japanese pitch accent and furigana on
input text. The accent pipeline is fully local — fugashi + UniDic for
morphology, OJAD's suzukikun phrasing endpoint for per-mora pitch.
No external API keys or `.env` setup required.

> [!WARNING]
> Still under active development. Output shape may shift between
> commits; check the changelog before pinning a client.

## Endpoints

| Endpoint | Description |
|--|--|
| `POST /api/MarkAccent/` | Mark pitch accent + furigana on the whole input, returns one `AccentResponse`. |
| `POST /api/MarkAccent/stream/` | Same pipeline, streams one NDJSON object per `\n`-split sentence as it finishes. |
| `POST /api/UsageQuery/HeadWords/` | Look up Yahoo Realtime/News headwords for a query (delegates to an external HTTP endpoint). |
| `POST /api/UsageQuery/URL/` | Resolve headword references to URLs. |
| `POST /api/DictQuery/` | JMdict dictionary lookup. |
| `POST /api/SentenceQuery/` | Example-sentence search. |

The MarkFurigana endpoint that lived in earlier versions of the
service was removed during the Yahoo MA → local fugashi migration;
callers that need raw tokenisation can use the same `MarkAccent`
response and ignore the `accent` field, or import
`api.accent.tokenizer.tag_local` directly when running in-process.

## `POST /api/MarkAccent/`

Request body:

| Field | Type | Default | Description |
|--|--|--|--|
| `text` | string | required | The Japanese text to mark. Newline-separated chunks are processed in parallel under a small semaphore. |
| `render_english_furigana` | bool | `false` | Show Japanese-style readings on ASCII-letter tokens (`Apple` → `アップル`). |
| `render_katakana_furigana` | bool | `false` | Show hiragana ruby on pure-katakana tokens (`カメラ` → `かめら`). The per-mora pitch list is returned either way. |
| `script` | `"hiragana"` \| `"katakana"` \| `"romaji"` | `"hiragana"` | Output script for every furigana field. Internal alignment stays hiragana — this is a response-shape switch. Romaji uses jaconv's default Hepburn-style table (`おう` → `ou`); no macrons. |

Response body (`AccentResponse`):

```jsonc
{
  "status": 200,
  "result": [
    {
      "surface": "聞き分け",
      "furigana": "ききわけ",
      "accent": [
        {"furigana": "き", "accent_marking_type": 0, "length": 1},
        {"furigana": "き", "accent_marking_type": 1, "length": 1},
        {"furigana": "わ", "accent_marking_type": 1, "length": 1},
        {"furigana": "け", "accent_marking_type": 1, "length": 1}
      ],
      "subword": [
        {"surface": "聞", "furigana": "き"},
        {"surface": "き", "furigana": ""},
        {"surface": "分", "furigana": "わ"},
        {"surface": "け", "furigana": ""}
      ],
      "kernel_absorbed": false
    }
    /* … */
  ],
  "error": null
}
```

Field reference:

- `surface` — the original input fragment.
- `furigana` — full-token reading in the requested `script`. Empty
  for particles (the `に` / `を` family), punctuation, and pure-English
  / pure-katakana tokens when their toggle is off.
- `accent[]` — per-mora pitch list. `accent_marking_type`:
  - `0` = LOW / unmarked.
  - `1` = HIGH plateau.
  - `2` = FALL kernel (高→低 boundary).
  Drawing the curve: pad LOW before the first HIGH, then HIGH up
  through any non-FALL morae, then drop after a `type=2` mora.
- `subword[]` — present when the surface mixes kanji and kana
  (`聞き分け`, `取り組み`, `飲んで` …). Each segment is one
  `WordResult`; kanji runs carry their furigana slice, in-line kana
  carry `furigana=""`. Clients that don't want the segment view can
  ignore this field — the top-level `surface` / `furigana` /
  `accent` carry the whole token regardless.
- `kernel_absorbed` — UniDic says this word has an accent kernel but
  OJAD's contour for its range has no FALL. Usually means the word
  sits inside a longer prosodic phrase whose kernel ended up on a
  neighbouring word; useful as a hint for "this token's pitch may
  be inherited from context".

Symbols with spoken readings (`#`, `%`, `@`, `&`, `+`, `=`, `$`,
`¥`, `€`, `℃`, `°`, `*`, `~`, `§`) are auto-vocalised in the
tokeniser. `#` comes back as `surface="#" furigana="しゃーぷ"` plus
the matching per-mora accent; the previous behaviour silently
dropped the mora onto the next kana token.

### `POST /api/MarkAccent/stream/`

Same request schema. Streams `application/x-ndjson` — one JSON object
per chunk, in input order, with two extra fields:

- `chunk` — original `\n`-split line index (blanks reserve their
  position so position 2 was empty).
- `subchunk` — sentence index inside that line.

Each object's other fields mirror `AccentResponse`: `status`,
`result`, `error`.

### Examples

```bash
# Default — hiragana ruby, no English ruby, no katakana ruby
curl -X POST http://127.0.0.1:8000/api/MarkAccent/ \
     -H 'Content-Type: application/json' \
     -d '{"text":"聞き分けは取り組みの基本"}'

# Katakana ruby on katakana words + romaji output
curl -X POST http://127.0.0.1:8000/api/MarkAccent/ \
     -H 'Content-Type: application/json' \
     -d '{"text":"カメラで写真を撮る",
          "render_katakana_furigana":true,
          "script":"romaji"}'
```

The included `test.sh` helper drives the endpoint and pretty-prints
one `(surface|furigana|accent_marking_type)` line per mora — see
`test.sh --help` style usage in the script header.

## Build environment

Download [uv](https://docs.astral.sh/uv/getting-started/installation/)
and sync the project:

```bash
uv sync
```

No environment variables or API keys are required.

## Running

Dev mode with auto-reload:

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Docker:

```bash
docker compose up -d --build
```

Set `API_TOOLS_PORT` in your shell env if you need a different
host-side port (the compose file falls back to `8000`).

Authentication (`X-API-KEY`), CORS, and trusted-host middleware were
intentionally removed; this service is expected to sit behind the
parent backend or on a private network. In
[jpcorrect-backend](https://github.com/sessatakuma/jpcorrect-backend),
the equivalent workflow is `make api-tools`.

### Quick smoke test

```bash
./test.sh "三月五日（土）"
```

### Regression against a corpus

`scripts/run_10_tests.sh` POSTs every fixture in `data/test_*.txt`
to `/api/MarkAccent/` and stores the raw JSON + a per-mora text
view in `output/`. The `data/` and `output/` directories are
gitignored — drop your own fixtures in to use the harness. Override
the set with the `TESTS` env var:

```bash
TESTS="0 15 29" ./scripts/run_10_tests.sh    # 3-file subset
```

## How to use a shared `httpx.AsyncClient`

If your router needs to send HTTP requests, follow this pattern to
reuse the connection pool managed by `api.dependencies`:

```python
import httpx
from fastapi import APIRouter, Depends
from api.dependencies import get_http_client

router = APIRouter()

@router.post("/Foo/", tags=["Foo"], response_model=FooResponse)
async def foo(
    request: FooRequest, client: httpx.AsyncClient = Depends(get_http_client)
):
    try:
        response = await client.post(url)
    except httpx.TimeoutException:
        ...
    except httpx.HTTPError as e:
        ...
```

## Known limitations

- **UniDic-vs-OJAD reading mismatches.** A handful of kanji come
  back from UniDic with one reading (the lemma) but OJAD pronounces
  them with the contextual reading (`世`=せ vs UniDic's `よ`,
  `本当`=ほんとう vs `ほんと`, `他`=ほか vs `た`, `寺`=てら vs `じ`).
  In those cases the extra OJAD mora can leak onto a 1-mora particle
  to its right. The 30-fixture corpus exposes four such tokens; see
  `output/anomalies.md` for the list.
- **Romaji has no macrons.** `script="romaji"` uses jaconv's
  default Hepburn table, so long `おう` / `ええ` come back as
  `ou` / `ee` rather than `ō` / `ē`. Add a macron pass in the
  client if you need that.
- **OJAD scrape dependency.** The accent pipeline POSTs each chunk
  to `https://www.gavo.t.u-tokyo.ac.jp/ojad/phrasing/index`; long
  documents are capped to 4 in-flight requests so we don't get
  rate-limited. If OJAD is unreachable, the chunk's `error` field
  is populated and `status` reflects the failure.
