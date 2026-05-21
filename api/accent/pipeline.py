"""MarkAccent orchestrator.

Threads the data-layer modules together and applies the surface-level
regex override layer on either side of the OJAD alignment:

  1. `furigana.fetch_furigana` — tokenise + read with Yahoo Furigana
  2. `reading_overrides.apply_furigana_overrides` — fix date / weekday-
     bracket readings BEFORE alignment so OJAD's numeric-anchor logic
     doesn't cascade-fail on overridden spans
  3. `ojad.get_ojad_result` — pull per-mora pitch contour from OJAD
  4. `align.align_accent` — DP-match tokens ↔ OJAD spans
  5. `reading_overrides.apply_accent_overrides` — re-apply overrides
     over (furigana, accent) so the final response stays consistent

Plus pre-/post-processing helpers for URL stripping, non-Japanese
short-circuit, sentence splitting (used by the streaming endpoint),
and the streaming chunk-fanout itself.

The route handlers in `routes.py` wrap this with FastAPI request handling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, AsyncIterator

import httpx
import neologdn

from api.accent.align import align_accent
from api.accent.furigana import fetch_furigana
from api.accent.models import (
    AccentResponse,
    ErrorInfo,
    Request,
    WordAccentResult,
)
from api.accent.ojad import get_ojad_result
from api.accent.reading_overrides import (
    apply_accent_overrides,
    apply_furigana_overrides,
)

logger = logging.getLogger("api")


# Hiragana / katakana / CJK Unified Ideographs (incl. Extension A). A
# chunk with no chars in this set is treated as pure English / code /
# markdown / URL — pipeline is skipped entirely and the line is echoed
# back verbatim so document reconstruction still works.
_CJK_RE = re.compile(
    "["
    "぀-ゟ"  # Hiragana
    "゠-ヿ"  # Katakana
    "㐀-䶿"  # CJK Unified Ideographs Extension A
    "一-鿿"  # CJK Unified Ideographs
    "]"
)

# Sentence terminators that close a Japanese clause: kuten (。), full-width
# question (？), full-width exclamation (！), and full-width period (．).
# ASCII `.!?` are intentionally excluded — they appear in abbreviations,
# decimals, and code/identifier fragments that we don't want to split on.
# A zero-width split (lookbehind) keeps the terminator attached to the
# preceding sentence so accent prediction still sees the clause boundary.
_SENTENCE_SPLIT_RE = re.compile("(?<=[。！？．])")

# URLs are stripped before the pipeline runs. OJAD's phrasing scraper
# produces only noise for Latin punctuation runs, and Yahoo's tokenizer
# can fragment a URL across several alphabet/symbol tokens — both drag
# the alignment DP off-rail for the surrounding Japanese. We swap each
# URL for one fixed-string placeholder (which Yahoo keeps as a single
# "alphabet" word), run the pipeline, then walk the result and restore
# the originals in order.
# URL body stops at whitespace, any Japanese char (so `…はhttps://x.jp/aです`
# strips just the URL, leaving `です` to be processed), or common quoting
# punctuation `,()<>[]"'` (so `(https://x.jp)` strips just the URL).
_URL_RE = re.compile(r"https?://[^\s　-鿿,()<>\[\]\"']+")
_URL_PLACEHOLDER = "URLPLACEHOLDER"


def _has_japanese(text: str) -> bool:
    """True if `text` contains any hiragana, katakana, or CJK ideograph."""
    return bool(_CJK_RE.search(text))


def _split_sentences(line: str) -> list[str]:
    """Split a line into sentence-sized chunks for parallel processing.

    OJAD's phrasing module degrades badly on long inputs (a single
    misaligned mora can cascade across the whole paragraph), and the
    streaming endpoint can't parallelise within a `\\n`-delimited chunk.
    Splitting on full-width sentence terminators fixes both: each sentence
    is short enough for OJAD to handle reliably, and they fan out across
    the in-flight Semaphore.
    """
    return [s for s in _SENTENCE_SPLIT_RE.split(line) if s.strip()]


def _strip_urls(text: str) -> tuple[str, list[str]]:
    """Replace each URL with `_URL_PLACEHOLDER`, returning URLs in order."""
    urls: list[str] = []

    def repl(m: re.Match[str]) -> str:
        urls.append(m.group(0))
        return _URL_PLACEHOLDER

    return _URL_RE.sub(repl, text), urls


def _restore_urls(
    result: list[WordAccentResult], urls: list[str]
) -> list[WordAccentResult]:
    """Swap placeholder tokens in `result` back to their original URLs."""
    if not urls:
        return result
    it = iter(urls)
    out: list[WordAccentResult] = []
    for w in result:
        if w.surface == _URL_PLACEHOLDER:
            url = next(it, None)
            if url is None:
                # Placeholder count exceeded URL count: leave the token
                # untouched. Indicates a Yahoo tokenization surprise; the
                # output is still readable.
                out.append(w)
                continue
            out.append(
                WordAccentResult(surface=url, furigana=url, accent=[], subword=[])
            )
        else:
            out.append(w)
    return out


async def process_accent_chunk(text: str, client: httpx.AsyncClient) -> AccentResponse:
    """Run the full MarkAccent pipeline on a single chunk of text.

    Shared by `/api/MarkAccent/` (whole input as one chunk) and
    `/api/MarkAccent/stream/` (one call per `\\n`/sentence-split piece).
    """
    try:
        query_text = neologdn.normalize(text, tilde="normalize")

        # Strip URLs first so a pure-URL line is detected as non-Japanese
        # by the language check below and short-circuits the pipeline.
        stripped_text, urls = _strip_urls(query_text)

        # No hiragana/katakana/kanji outside URLs — passthrough the line
        # as a single token. Callers reconstructing the document still
        # see the chunk in the stream; we just skip the Yahoo + OJAD
        # round-trips entirely.
        if not _has_japanese(stripped_text):
            return AccentResponse(
                status=200,
                result=[
                    WordAccentResult(
                        surface=query_text,
                        furigana=query_text,
                        accent=[],
                        subword=[],
                    )
                ],
                error=None,
            )

        # Apply furigana overrides BEFORE alignment: many of the overrides
        # (e.g. "4日"→"よっか", "27日"→"にじゅうしちにち") merge a numeric
        # surface with the counter into one token whose furigana matches what
        # OJAD reads as a single phrase. align_accent's numeric-anchor logic
        # otherwise cascades-fails on these inputs because numeric tokens lack
        # any Yahoo furigana for OJAD to align against.
        furigana_response = await fetch_furigana(stripped_text, client)

        # Check yahoo furigana response
        if furigana_response.status != 200 or not furigana_response.result:
            logger.warning(f"Yahoo Response Empty or Invalid: {furigana_response}")
            return AccentResponse(
                status=furigana_response.status,
                result=None,
                error=furigana_response.error,
            )

        furigana_results = apply_furigana_overrides(furigana_response.result)
        logger.debug(f"Yahoo Results Count: {len(furigana_results)}")

        _ojad_surface, ojad_results = await get_ojad_result(stripped_text, client)

        final_results = await align_accent(furigana_results, ojad_results)
        final_results = apply_accent_overrides(final_results)
        final_results = _restore_urls(final_results, urls)

        return AccentResponse(status=200, result=final_results)

    except Exception as e:
        logger.exception(f"Unexpected error occurred: {text}")
        # Some httpx exceptions (PoolTimeout, ReadTimeout) have empty
        # str(); fall back to the type name so the client sees something.
        detail = str(e) or repr(e) or type(e).__name__
        return AccentResponse(
            status=500,
            result=None,
            error=ErrorInfo(code=500, message=f"Error: {detail}"),
        )


# Streaming endpoint: OJAD's u-tokyo backend and (to a lesser extent) Yahoo's
# furigana API both fall over when hit with 30+ parallel scrapes — the symptom
# was most chunks of a long document returning empty-string httpx errors. Cap
# in-flight work so well-behaved inputs still parallelise (a 4-chunk
# paragraph fans out fully) without hammering the upstream services.
_STREAM_CONCURRENCY = 4


async def stream_accent_chunks(
    request: Request, client: httpx.AsyncClient
) -> AsyncIterator[bytes]:
    """Yield one NDJSON line per (line_idx, sub_idx) chunk in input order.

    Each emitted object carries `{"chunk": line_idx, "subchunk": sub_idx}`:
    `line_idx` is the original `\\n`-split index (blank lines are dropped from
    the stream); `sub_idx` distinguishes sentences inside one line. A line
    with no terminator yields one subchunk with `sub_idx=0`.
    """
    # (line_idx, sub_idx, text). Long paragraphs are split into sentence-
    # sized chunks because OJAD's phrasing predictor degrades on long
    # inputs and a single misalignment used to cascade across the whole
    # paragraph. Splitting also fans the work out under the semaphore.
    chunks: list[tuple[int, int, str]] = []
    for line_idx, line in enumerate(request.text.split("\n")):
        if not line.strip():
            continue
        for sub_idx, sentence in enumerate(_split_sentences(line)):
            chunks.append((line_idx, sub_idx, sentence))

    if not chunks:
        return

    semaphore = asyncio.Semaphore(_STREAM_CONCURRENCY)

    async def run_chunk(line: str) -> AccentResponse:
        async with semaphore:
            return await process_accent_chunk(line, client)

    tasks = [asyncio.create_task(run_chunk(text)) for _, _, text in chunks]
    # Yield in input order so the client renders chunks monotonically.
    for (chunk_idx, sub_idx, _text), task in zip(chunks, tasks):
        try:
            resp = await task
            payload: dict[str, Any] = {
                "chunk": chunk_idx,
                "subchunk": sub_idx,
                **resp.model_dump(),
            }
        except Exception as exc:
            logger.exception(f"Streaming chunk {chunk_idx}.{sub_idx} failed")
            detail = str(exc) or repr(exc) or type(exc).__name__
            payload = {
                "chunk": chunk_idx,
                "subchunk": sub_idx,
                "status": 500,
                "result": None,
                "error": {"code": 500, "message": f"Error: {detail}"},
            }
        yield (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
