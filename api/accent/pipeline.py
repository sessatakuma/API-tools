"""MarkAccent orchestrator.

Threads the layers together:

  1. `preprocess.strip_urls / strip_number_commas / strip_x_between_digits`
     — pre-tokenisation surface rewrites with bookkeeping for restoration.
  2. `tokenizer.tag_local` — fugashi + UniDic in-process tokenisation
     (also fills in furigana for standalone symbols `#`, `%`, … via
     `preprocess.SYMBOL_READINGS`, so digits and symbols stay separate
     tokens with the symbol's reading anchored to itself).
  3. `reading_overrides.apply_furigana_overrides` — regex date/duration
     overrides applied before accent so the engine sees normalised surfaces.
  4. `openjtalk.get_openjtalk_result` — per-mora pitch contour from the
     in-process OpenJTalk frontend (offline; no network).
  5. `align.align_accent` — DP align tokens ↔ accent spans → WordAccentResult.
  6. `reading_overrides.apply_accent_overrides` — re-run the same regex
     overrides on aligned results to rewrite furigana + accent in one go.
  7. `reading_overrides.apply_accent_patches` — POS-driven ます / たい
     first-mora-FALL patches.
  8. `postprocess.flatten_heiban_particle_accent / suppress_punct_furigana
     / apply_furigana_toggles / suppress_particle_furigana /
     split_okurigana` — rendering polish (see `postprocess.py`).
  9. `preprocess.restore_number_commas / restore_x_between_digits /
     restore_urls` — undo the pre-tokenisation surface rewrites.
 10. `postprocess.convert_furigana_script` — last pass, rewrites every
     furigana field into the requested output script.

The pitch-accent backend is now the in-process OpenJTalk frontend
(`openjtalk.py`); any remaining "OJAD" in comments/identifiers below is
historical (the former backend was an OJAD scrape) — the shared per-mora
`{text, accent}` contract is unchanged, so the naming was left in place.

`_build_chunks` + `_schedule_chunks` are shared between the regular
`/MarkAccent/` (collected) and `/MarkAccent/stream/` (yielded) endpoints
so both emit byte-identical per-chunk results; only the delivery shape
differs.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import neologdn

from api.accent.align import align_accent
from api.accent.models import AccentResponse, ErrorInfo, WordAccentResult
from api.accent.openjtalk import get_openjtalk_result
from api.accent.postprocess import (
    apply_furigana_toggles,
    convert_furigana_script,
    flatten_heiban_particle_accent,
    split_okurigana,
    suppress_particle_furigana,
    suppress_punct_furigana,
)
from api.accent.preprocess import (
    has_japanese,
    restore_number_commas,
    restore_urls,
    restore_x_between_digits,
    split_sentences,
    strip_acronym_dots_for_ojad,
    strip_number_commas,
    strip_urls,
    strip_x_between_digits,
)
from api.accent.reading_overrides import (
    apply_accent_overrides,
    apply_accent_patches,
    apply_furigana_overrides,
)
from api.accent.tokenizer import tag_local

logger = logging.getLogger("api")


async def process_accent_chunk(
    text: str,
    client: httpx.AsyncClient,
    render_english_furigana: bool = False,
    render_katakana_furigana: bool = False,
    script: str = "hiragana",
) -> AccentResponse:
    """Run the full accent pipeline on a single chunk of text.

    Shared by `/api/MarkAccent/` (whole input fanned across chunks) and
    `/api/MarkAccent/stream/` (one call per `\\n`-split sentence).
    """
    try:
        query_text = neologdn.normalize(text, tilde="normalize")

        # Strip URLs first so a pure-URL line is detected as non-Japanese
        # by the language check below and short-circuits the pipeline.
        stripped_text, urls = strip_urls(query_text)

        # Strip thousands-grouping commas (`1,234` → `1234`) so fugashi
        # sees one numeric token and OJAD reads the whole integer as one
        # phrase; the original comma-formatted surface is reinstated
        # after alignment via `restore_number_commas`.
        stripped_text, number_strips = strip_number_commas(stripped_text)

        # Swap `\d×\d` → `\d/\d` so OJAD reads each number separately
        # instead of merging '19×19' into '1919' (千九百十九). `×` is
        # restored on the surface after alignment via
        # `restore_x_between_digits`.
        stripped_text, x_count = strip_x_between_digits(stripped_text)

        # No hiragana/katakana/kanji outside URLs — passthrough the line
        # as a single token. Callers reconstructing the document still
        # see the chunk in the stream; we just skip the tokeniser + OJAD
        # round-trips entirely.
        if not has_japanese(stripped_text):
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
        # OJAD reads as a single phrase. align_accent's numeric branch
        # otherwise cascades-fails on these inputs because numeric tokens lack
        # any furigana for OJAD to align against.
        # Offload the tokeniser to a worker thread: `tag_local` runs
        # fugashi's CPU-bound C parse against the 1.3 GB UniDic dict. On the
        # event-loop thread it would block concurrent chunks and serialise
        # the Semaphore(4) fan-out (same rationale as the OpenJTalk offload
        # in `get_openjtalk_result`). The shared MeCab Tagger is serialised
        # behind `tokenizer._TAGGER_LOCK` so concurrent workers can't corrupt
        # its C state.
        raw = await asyncio.to_thread(tag_local, stripped_text)
        furigana_results = apply_furigana_overrides(raw)
        if not furigana_results:
            logger.warning("Local tokeniser returned empty token list")
            return AccentResponse(
                status=500,
                result=None,
                error=ErrorInfo(
                    code=500, message="Tokeniser returned empty token list"
                ),
            )
        logger.debug("Tokeniser Results Count: %d", len(furigana_results))

        # Acronym `.` strip: `Wifi.7` would otherwise be normalised to
        # `Wifi。7`, whose injected `。` collapses the prosody on the rest of
        # the sentence (downstream accents come back all-zero). Strip the `.`
        # for the accent query so the frontend sees `Wifi7`; fugashi keeps the
        # original surface so the acronym-merge preserves `Wifi.7` for display.
        accent_query_text = strip_acronym_dots_for_ojad(stripped_text)

        # Pitch-accent enrichment from the in-process OpenJTalk frontend.
        # Fully offline — no network call, so (unlike the old OJAD scrape) it
        # has no unavailable path. `warning` stays None; it is kept on the
        # response below for schema parity with clients that handled the old
        # OJAD-degradation warning.
        warning = None
        _surface, accent_results = await get_openjtalk_result(accent_query_text, client)

        final_results = await align_accent(furigana_results, accent_results)
        final_results = apply_accent_overrides(final_results)
        # POS-driven suffix patches run after the full-span overrides so
        # that tokens replaced by overrides (pos=None) are skipped by the
        # patch predicates.
        final_results = apply_accent_patches(final_results)
        # Flatten の/な after heiban so the trailing HIGH plateau doesn't
        # paint a noisy overlay across the noun→particle boundary.
        final_results = flatten_heiban_particle_accent(final_results)
        # `apply_accent_overrides` rebuilds bracket-style tokens (e.g.
        # `(土)`) with a fallback type-0 accent that re-introduces the
        # ruby-on-punctuation problem `_build_word_result` already
        # suppressed. Re-suppress at the end so #2 holds uniformly.
        final_results = suppress_punct_furigana(final_results)
        final_results = apply_furigana_toggles(
            final_results, render_english_furigana, render_katakana_furigana
        )
        # Particle suppression runs last (after toggles): drops the
        # redundant top-level furigana on 助詞 tokens so the per-mora
        # pitch overlay isn't crowded out by duplicated ruby.
        final_results = suppress_particle_furigana(final_results)
        # Split mixed kanji+kana surfaces (`聞き分け`) into per-segment
        # subwords so clients can render furigana only on the kanji
        # portions. Top-level surface/furigana/accent stay intact.
        final_results = split_okurigana(final_results)
        final_results = restore_number_commas(final_results, number_strips)
        final_results = restore_x_between_digits(final_results, x_count)
        final_results = restore_urls(final_results, urls)
        # Output-script switch runs last so every furigana field
        # (top-level + per-mora + subword) lands in the requested
        # script before serialisation. Hiragana is the no-op default.
        final_results = convert_furigana_script(final_results, script)

        return AccentResponse(status=200, result=final_results, warning=warning)

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


def build_chunks(text: str) -> list[tuple[int, int, str]]:
    """Split `text` into (line_idx, sub_idx, sentence) chunks.

    Long paragraphs are split into sentence-sized chunks because a single
    misalignment used to cascade across the whole paragraph, and shorter
    inputs give the OpenJTalk frontend a cleaner prosodic phrase to work
    with. Splitting also fans the work out under the semaphore.

    Shared by `/MarkAccent/` (collected) and `/MarkAccent/stream/`
    (yielded) so both endpoints emit byte-identical per-chunk results;
    only the delivery shape differs.
    """
    chunks: list[tuple[int, int, str]] = []
    for line_idx, line in enumerate(text.split("\n")):
        if not line.strip():
            continue
        for sub_idx, sentence in enumerate(split_sentences(line)):
            chunks.append((line_idx, sub_idx, sentence))
    return chunks


def schedule_chunks(
    chunks: list[tuple[int, int, str]],
    client: httpx.AsyncClient,
    render_english_furigana: bool,
    render_katakana_furigana: bool,
    script: str = "hiragana",
) -> list[asyncio.Task[AccentResponse]]:
    """Schedule one `process_accent_chunk` task per chunk under a
    shared semaphore.

    The semaphore bounds CPU concurrency for the in-process OpenJTalk
    frontend: each chunk's accent pass runs the C-extension work in a
    worker thread (see `openjtalk.get_openjtalk_result`), so capping
    in-flight chunks at 4 keeps a long document from spawning dozens of
    threads while well-behaved inputs still parallelise (a 4-chunk
    paragraph fans out fully).

    Tasks run detached on the event loop; the caller owns their lifetime
    and MUST drain them through `cancel_pending` in a `finally` so a client
    disconnect doesn't leave them doing work for a response nobody reads.
    (A `TaskGroup` would tie the lifetime up automatically, but `async with
    TaskGroup()` inside the streaming async generator wraps the `aclose()`
    `GeneratorExit` into a `BaseExceptionGroup` — so explicit cancellation is
    the only shape that closes the stream cleanly.)
    """
    semaphore = asyncio.Semaphore(4)

    async def run_chunk(line: str) -> AccentResponse:
        async with semaphore:
            return await process_accent_chunk(
                line,
                client,
                render_english_furigana=render_english_furigana,
                render_katakana_furigana=render_katakana_furigana,
                script=script,
            )

    return [asyncio.create_task(run_chunk(text)) for _, _, text in chunks]


async def cancel_pending(tasks: list[asyncio.Task[AccentResponse]]) -> None:
    """Cancel any not-yet-finished chunk tasks and await their teardown.

    Called from both endpoints' `finally` so a client disconnect — the
    `GeneratorExit` thrown into the streaming response, or the collected
    request handler being cancelled — stops in-flight chunk work instead
    of orphaning them. On normal completion every task is already done, so
    this is a no-op `gather` over finished tasks.
    """
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
