"""OJAD scrape — per-mora pitch accent contour.

POSTs the cleaned query text to OJAD's suzukikun (すずきくん) phrasing
endpoint, parses the returned HTML, and extracts a flat list of
`{text, accent}` per-mora entries plus the surface paragraph OJAD
echoed back.

`accent` values match `AccentInfo.accent_marking_type`:
  - 0 = LOW / unknown
  - 1 = HIGH plateau (CSS class `accent_plain`)
  - 2 = FALL kernel (CSS class `accent_top`)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger("api")

OJAD_URL = "https://www.gavo.t.u-tokyo.ac.jp/ojad/phrasing/index"


async def get_ojad_result(
    query_text: str,
    client: httpx.AsyncClient,
) -> tuple[str, list[dict[str, Any]]]:
    """Parse cleaned query_text to OJAD, concate whole result as a list"""
    logger.debug(f"[OJAD] Start fetching for: {query_text}")

    # Data of the POST method
    data = {
        "data[Phrasing][text]": query_text,
        "data[Phrasing][curve]": "advanced",
        "data[Phrasing][accent]": "advanced",
        "data[Phrasing][accent_mark]": "all",
        "data[Phrasing][estimation]": "crf",
        "data[Phrasing][analyze]": "true",
        "data[Phrasing][phrase_component]": "invisible",
        "data[Phrasing][param]": "invisible",
        "data[Phrasing][subscript]": "visible",
        "data[Phrasing][jeita]": "invisible",
    }

    # Send a POST and receive the website html code
    try:
        response = await client.post(OJAD_URL, data=data)
        response.raise_for_status()
        logger.debug(f"[OJAD] Status Code: {response.status_code}")
    except Exception as e:
        logger.error(f"[OJAD] Request Failed: {e}")
        raise e

    website = response.text

    # use Beautiful Soup to parse the received html file
    soup = BeautifulSoup(website, "html.parser")

    # Fetch the required tags
    phrasing_texts = soup.find_all("div", attrs={"class": "phrasing_text"})
    phrasing_subscripts = soup.find_all("div", attrs={"class": "phrasing_subscript"})

    paragraph = ""
    ojad_results = []

    if not phrasing_texts:
        logger.warning("[OJAD] Warning: No phrasing_texts found in HTML!")

    for furigana, surface in zip(phrasing_texts, phrasing_subscripts):
        # Fetch subscript text
        phrase = surface.find_all("span", recursive=False)
        sentence = ""
        for p in phrase:
            sentence += p.get_text()
        paragraph += sentence

        # Fetch processed data
        mojis = furigana.find_all("span", recursive=False)
        for moji in mojis:
            # Check accent mark (we don't use unvoiced)
            accent = 0
            if moji["class"][0] == "accent_plain":
                accent = 1
            elif moji["class"][0] == "accent_top":
                accent = 2
            ojad_results.append({"text": moji.get_text(), "accent": accent})

    return paragraph, ojad_results
