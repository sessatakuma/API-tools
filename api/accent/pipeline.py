"""MarkAccent orchestrator.

Threads the three data-layer modules together:
  1. `furigana.fetch_furigana` — tokenise + read with Yahoo Furigana
  2. `ojad.get_ojad_result` — pull per-mora pitch contour from OJAD
  3. `align.align_accent` — match tokens ↔ OJAD spans → WordAccentResult

The route handler in `routes.py` wraps this with FastAPI request handling.
"""

from __future__ import annotations

import logging

import httpx
import neologdn

from api.accent.align import align_accent
from api.accent.furigana import fetch_furigana
from api.accent.models import AccentResponse, ErrorInfo
from api.accent.ojad import OJADUnavailableError, get_ojad_result

logger = logging.getLogger("api")


async def process_accent_chunk(text: str, client: httpx.AsyncClient) -> AccentResponse:
    """Run the full MarkAccent pipeline on a single chunk of text."""
    try:
        query_text = neologdn.normalize(text, tilde="normalize")

        furigana_response = await fetch_furigana(query_text, client)

        # Check yahoo furigana response
        if furigana_response.status != 200 or not furigana_response.result:
            logger.warning(f"Yahoo Response Empty or Invalid: {furigana_response}")
            return AccentResponse(
                status=furigana_response.status,
                result=None,
                error=furigana_response.error,
            )

        furigana_results = furigana_response.result
        logger.debug(f"Yahoo Results Count: {len(furigana_results)}")

        # OJAD only enriches the result with pitch accent. If it is down, fall
        # back to furigana-only output (align_accent emits accent_marking_type=0
        # for every mora when given an empty list) rather than failing the
        # request — see api/accent/align.py "MATCH FAILED" branch.
        warning = None
        try:
            _ojad_surface, ojad_results = await get_ojad_result(query_text, client)
        except OJADUnavailableError as e:
            logger.warning(f"OJAD unavailable, degrading to furigana-only: {e}")
            ojad_results = []
            warning = (
                "OJAD pitch-accent service is unavailable; "
                "returning furigana without pitch accent."
            )

        final_results = await align_accent(furigana_results, ojad_results)

        return AccentResponse(status=200, result=final_results, warning=warning)

    except Exception as e:
        logger.exception(f"Unexpected error occurred: {text}")
        return AccentResponse(
            status=500,
            result=None,
            error=ErrorInfo(code=500, message=f"Error: {e}"),
        )
