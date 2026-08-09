"""MarkAccent orchestrator.

Threads the layers together:

  1. `preprocess.strip_x_between_digits / strip_urls / strip_number_commas`
     — pre-tokenisation surface rewrites with bookkeeping for restoration.
  2. `tokenizer.tag_local` — fugashi + UniDic in-process tokenisation;
     numeric + readable-symbol pairs such as `2℃` become one compound,
     while standalone symbols get readings from `preprocess.SYMBOL_READINGS`.
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
  9. `preprocess.restore_number_commas / restore_urls /
      restore_x_between_digits` — undo the pre-tokenisation surface rewrites.
 10. `postprocess.convert_furigana_script` — last pass, rewrites every
     furigana field into the requested output script.

The pitch-accent backend is the in-process OpenJTalk frontend
(`openjtalk.py`), which fills the per-mora `{text, accent}` contract the
aligner consumes.

`chunking.build_chunks` + `chunking.schedule_chunks` are shared between the regular
`/MarkAccent/` (collected) and `/MarkAccent/stream/` (yielded) endpoints
so both emit byte-identical per-chunk results; only the delivery shape
differs.
"""

from __future__ import annotations

import asyncio
import logging

from api.accent.align import align_accent
from api.accent.chunking import build_chunks, cancel_pending, schedule_chunks
from api.accent.models import AccentResponse, ErrorInfo, WordAccentResult
from api.accent.openjtalk import get_openjtalk_mora_counts, get_openjtalk_result
from api.accent.postprocess import (
    apply_furigana_toggles,
    convert_furigana_script,
    flatten_heiban_particle_accent,
    split_okurigana,
    suppress_particle_furigana,
    suppress_punct_furigana,
)
from api.accent.preprocess import (
    NUMERIC_PATTERN,
    NUMERIC_UNIT_RE,
    READABLE_COMPOUND_RE,
    SYMBOL_READINGS,
    clean_hidden_english,
    has_japanese,
    normalize_and_strip_x_between_digits,
    restore_number_commas,
    restore_urls,
    restore_x_between_digits,
    strip_acronym_dots,
    strip_number_commas,
    strip_urls,
)
from api.accent.reading_overrides import (
    apply_accent_overrides,
    apply_accent_patches,
    apply_furigana_overrides,
)
from api.accent.tokenizer import tag_local

logger = logging.getLogger("api")

__all__ = [
    "build_chunks",
    "cancel_pending",
    "process_accent_chunk",
    "schedule_chunks",
]


async def process_accent_chunk(
    text: str,
    render_english_furigana: bool = False,
    render_katakana_furigana: bool = False,
    script: str = "hiragana",
) -> AccentResponse:
    """Run the full accent pipeline on a single chunk of text.

    Shared by `/api/MarkAccent/` (whole input fanned across chunks) and
    `/api/MarkAccent/stream/` (one call per `\\n`-split sentence).
    """
    try:
        query_text, x_rewrites = normalize_and_strip_x_between_digits(text)

        # Strip URLs first so a pure-URL line is detected as non-Japanese
        # by the language check below and short-circuits the pipeline.
        stripped_text, urls = strip_urls(query_text)

        # Strip thousands-grouping commas (`1,234` → `1234`) so fugashi
        # sees one numeric token and OpenJTalk reads the whole integer as one
        # phrase; the original comma-formatted surface is reinstated
        # after alignment via `restore_number_commas`.
        stripped_text, number_strips = strip_number_commas(stripped_text)

        # Skip the expensive engines only when the chunk has no Japanese,
        # digits, spoken symbols, or requested English reading.
        has_spoken_symbol = any(char in SYMBOL_READINGS for char in stripped_text)
        has_digit = any(char.isdigit() for char in stripped_text)
        has_english = any(
            "a" <= char <= "z" or "A" <= char <= "Z" for char in stripped_text
        )
        needs_pipeline = (
            has_japanese(stripped_text)
            or has_spoken_symbol
            or has_digit
            or (render_english_furigana and has_english)
        )
        if not needs_pipeline:
            return AccentResponse(
                status=200,
                result=[
                    WordAccentResult(
                        surface=query_text,
                        furigana="",
                        accent=[],
                        subword=[],
                    )
                ],
                error=None,
            )

        # Apply furigana overrides BEFORE alignment: many of the overrides
        # (e.g. "4日"→"よっか", "27日"→"にじゅうしちにち") merge a numeric
        # surface with the counter into one token whose furigana matches what
        # OpenJTalk reads as a single phrase. align_accent's numeric branch
        # otherwise cascades-fails on these inputs because numeric tokens lack
        # any furigana for OpenJTalk to align against.
        # Offload the tokeniser to a worker thread: `tag_local` runs
        # fugashi's CPU-bound C parse against the 1.3 GB UniDic dict. On the
        # event-loop thread it would block concurrent chunks and serialise
        # the four-task process-wide window (same rationale as the OpenJTalk offload
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

        expected_mora_counts: list[int | None] = [None] * len(furigana_results)
        count_indexes: list[int] = []
        count_queries: list[str] = []
        for index, token in enumerate(furigana_results):
            is_numeric_unit = bool(NUMERIC_UNIT_RE.match(token.surface))
            is_english = (
                bool(token.surface)
                and any(
                    "a" <= char <= "z" or "A" <= char <= "Z" for char in token.surface
                )
                and all(
                    "a" <= char <= "z"
                    or "A" <= char <= "Z"
                    or "0" <= char <= "9"
                    or char in ("-", "_", ".")
                    for char in token.surface
                )
            )
            if (
                NUMERIC_PATTERN.match(token.surface)
                or READABLE_COMPOUND_RE.match(token.surface)
                or is_numeric_unit
            ):
                count_indexes.append(index)
                count_queries.append(token.surface)
            elif is_english:
                if render_english_furigana:
                    count_indexes.append(index)
                    count_queries.append(strip_acronym_dots(token.surface))
                else:
                    expected_mora_counts[index] = 0
        counts = await get_openjtalk_mora_counts(count_queries)
        for index, count in zip(count_indexes, counts):
            expected_mora_counts[index] = count

        # Acronym `.` strip: `Wifi.7` would otherwise be normalised to
        # `Wifi。7`, whose injected `。` collapses the prosody on the rest of
        # the sentence (downstream accents come back all-zero). Strip the `.`
        # for the accent query so the frontend sees `Wifi7`; fugashi keeps the
        # original surface so the acronym-merge preserves `Wifi.7` for display.
        accent_query_text = strip_acronym_dots(stripped_text)
        if not render_english_furigana:
            accent_query_text = clean_hidden_english(accent_query_text)

        # Pitch-accent enrichment from the in-process OpenJTalk frontend.
        # Fully offline — no network call, so there is no unavailable path.
        _surface, accent_results = await get_openjtalk_result(accent_query_text)

        final_results = await align_accent(
            furigana_results, accent_results, expected_mora_counts
        )
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
        final_results = restore_urls(final_results, urls)
        final_results = restore_x_between_digits(final_results, x_rewrites)
        # Output-script switch runs last so every furigana field
        # (top-level + per-mora + subword) lands in the requested
        # script before serialisation. Hiragana is the no-op default.
        final_results = convert_furigana_script(final_results, script)

        return AccentResponse(status=200, result=final_results)

    except Exception:
        logger.exception("Unexpected accent error chars=%d", len(text))
        return AccentResponse(
            status=500,
            result=None,
            error=ErrorInfo(code=500, message="Accent processing failed"),
        )
