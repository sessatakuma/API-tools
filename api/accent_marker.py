"""
An API that mark accent of given query text
"""

import logging
import re
import string
from typing import Any

import httpx
import jaconv
import neologdn
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import get_http_client
from api.furigana_marker import (
    ErrorInfo,
    Request,
    WordResult,
    mark_furigana,
)
from config.furigana_overrides import apply_accent_overrides

logger = logging.getLogger("api")

tags_metadata = [
    {
        "name": "MarkAccent",
        "description": "Mark accent of given text",
    },
]

punctuation_marks = set(
    [
        "。",
        "，",
        "、",
        "・",
        "——",
        "……",
        "—",
        "…",
        "「",
        "」",
        "『",
        "』",
        "（",
        "）",
        "—",
        "、、、",
        "、",
        "————",
        "—",
        "？",
        "！",
        ".",
        ",",
        "：",
        "；",
        "(",
        ")",
        '"',
        "--",
        "-",
        "",
        "/",
        ":",
        ";",
        "！",
        "＂",
        "＃",
        "＄",
        "％",
        "＆",
        "＼",
        "’",
        "（",
        "）",
        "＊",
        "＋",
        "，",
        "－",
        "．",
        "／",
        "：",
        "；",
        "＜",
        "＝",
        "＞",
        "？",
        "＠",
        "［",
        "＼",
        "］",
        "︿",
        "＿",
        "‵",
        "｛",
        "｝",
        "｜",
        "～",
        "“",
        "”",
    ]
).union(set(string.punctuation))
skip_marks = set(string.ascii_lowercase + string.ascii_uppercase)


def clean_query(query: str) -> str:
    """
    For OJAD, the query text should without punctuations and alphabets for better
    result
    """
    return "".join(chr for chr in query if chr not in skip_marks)


def is_kana_or_kanji(char: Any) -> bool:
    """Check whether given character is kana or kanji (ignore half-width kana)"""
    exception_symbols = ["\u30a0", "\u30fb", "\u30fc", "\u30fd", "\u30fe", "\u30ff"]
    if char in exception_symbols:
        # '゠', '・', 'ー', 'ヽ', 'ヾ', 'ヿ' which should be regard as punchutation
        return False
    kana = range(0x3040, 0x30FF + 1)
    kanji = range(0x4E00, 0x9FFF + 1)
    if ord(char) in kana or ord(char) in kanji:
        return True
    return False


# class Request(BaseModel):
#     """Class representing a request object"""

#     text: str = Field(description="The text to query")


class AccentInfo(BaseModel):
    """Class representing an accent information"""

    furigana: str = Field(description="The furigana of given kana and kanji")
    accent_marking_type: int = Field(
        description="The type of accent, including none (0), heiban (1), fall (2)"
    )
    length: int = Field(description="Length of the furigana")


class WordAccentResult(BaseModel):
    """Class representing a single word result object"""

    furigana: str = Field(description="Furigana of given kana and kanji")
    surface: str = Field(description="The (partial of) original query text")
    accent: list[AccentInfo] = Field(description="The accent of givent word")
    subword: list[WordResult] = Field(
        default_factory=list,
        description="""A list contains more details when a \
        word contains both kanji and kana.""",
    )


class Response(BaseModel):
    """Class representing a response object"""

    status: int = Field(
        default=200, description="Status code of response align with RFC 9110"
    )
    result: list[WordAccentResult] | None = Field(
        description="A list contains marked results"
    )
    error: ErrorInfo | None = Field(
        default=None,
        description="An object that describe the details of an error when occur",
    )


router = APIRouter()


async def get_ojad_result(
    query_text: str,
    client: httpx.AsyncClient,
) -> tuple[str, list[dict[str, Any]]]:
    """Parse cleaned query_text to OJAD, concate whole result as a list"""
    logger.debug(f"[OJAD] Start fetching for: {query_text}")

    # URL to suzukikun(すずきくん)
    url = "https://www.gavo.t.u-tokyo.ac.jp/ojad/phrasing/index"

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
        response = await client.post(url, data=data)
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


# accept negative integers and decimals
numeric_pattern = re.compile(r"^-?\d+(\.\d+)?$")


# Yahoo returns "dictionary form" furigana (no rendaku), while OJAD returns the
# actually pronounced kana (with rendaku/sequential-voicing applied). When
# Yahoo says "ふんかん" and OJAD says "ぷんかん", the literal startswith /
# equality checks below would never match and the alignment would cascade-fail.
# We compare under a normalisation that folds each voiced/half-voiced kana to
# its voiceless base, so ぷ↔ふ, ば↔は, ご↔こ etc. all alias together.
_VOICING_FOLD: dict[str, str] = {
    "が": "か", "ぎ": "き", "ぐ": "く", "げ": "け", "ご": "こ",
    "ざ": "さ", "じ": "し", "ず": "す", "ぜ": "せ", "ぞ": "そ",
    "だ": "た", "ぢ": "ち", "づ": "つ", "で": "て", "ど": "と",
    "ば": "は", "び": "ひ", "ぶ": "ふ", "べ": "へ", "ぼ": "ほ",
    "ぱ": "は", "ぴ": "ひ", "ぷ": "ふ", "ぺ": "へ", "ぽ": "ほ",
}


def _norm(s: str) -> str:
    """Kata→hira plus voicing fold for rendaku-tolerant alignment."""
    hira = jaconv.kata2hira(s)
    return "".join(_VOICING_FOLD.get(c, c) for c in hira)


async def align_accent(
    furigana_results: list[Any], ojad_results: list[dict[str, Any]]
) -> list[WordAccentResult]:
    """Align yahoo furigana with OJAD results, return final accent marked result"""
    final_response_results = []
    ojad_idx_cnt = 0

    logger.debug(f"🔍 [Data Check] First item:{furigana_results[0]}")

    for i, furigana_result in enumerate(furigana_results):
        yahoo_furigana = furigana_result.furigana
        yahoo_surface = furigana_result.surface

        yahoo_furigana_hira = jaconv.kata2hira(yahoo_furigana)
        yahoo_furigana_norm = _norm(yahoo_furigana)
        accents: list[AccentInfo] = []

        logger.debug(f"Processing Yahoo Word [{i}]: {yahoo_surface} ({yahoo_furigana})")

        # Identify if the word is numeric
        is_numeric = bool(numeric_pattern.match(yahoo_surface))

        # Skip tokens whose furigana is entirely non-kana/kanji (pure
        # punctuation, latin letters, etc.). `is_kana_or_kanji` also rejects
        # the long-vowel marker ー by design, so checking `any(not …)` would
        # bail on real katakana words like "データ" / "サッカー" — use `all`
        # to require every char to be non-kana before skipping.
        if (
            not furigana_result.subword
            and all(not is_kana_or_kanji(chr) for chr in yahoo_furigana)
            and not is_numeric
        ):
            logger.debug(" -> Skipped (Not Kana/Kanji)")
            accents.append(
                AccentInfo(
                    furigana=yahoo_surface,
                    accent_marking_type=0,
                    length=len(yahoo_surface),
                )
            )
            final_response_results.append(
                WordAccentResult(
                    furigana=yahoo_furigana, surface=yahoo_surface, accent=accents
                )
            )

            # Move OJAD index if skipped punctuation
            if ojad_idx_cnt < len(ojad_results) and jaconv.kata2hira(
                ojad_results[ojad_idx_cnt]["text"].strip()
            ) in ["、", "。", ",", "."]:
                ojad_idx_cnt += 1
            continue

        # Synchronize OJAD index
        ojad_idx = ojad_idx_cnt

        # Check OJAD boundary
        if ojad_idx >= len(ojad_results):
            logger.warning(f" -> OJAD Index Out of Bounds ({ojad_idx})")
        else:
            logger.debug(
                f"-> Comparing Yahoo '{yahoo_furigana_hira}'"
                f" vs OJAD '{ojad_results[ojad_idx]['text']}'"
            )

        # Move non-numeric OJAD index to the matching position
        if not is_numeric:
            while ojad_idx < len(ojad_results) and not yahoo_furigana_norm.startswith(
                _norm(ojad_results[ojad_idx]["text"])
            ):
                ojad_idx += 1

        # catch the furigana from Yahoo with OJAD results
        ojad_furigana = ""
        temp_accents = []  # Use temp list to avoid partial data

        # Define anchor(next Yahoo furigana) for numeric mode (rendaku-folded so
        # e.g. Yahoo's "ふんかん" still matches OJAD's actual reading "ぷんかん").
        next_yahoo_furigana = None
        if i + 1 < len(furigana_results):
            next_yahoo_furigana = _norm(furigana_results[i + 1].furigana)

        # Backup index
        temp_ojad_idx = ojad_idx

        # Number mode: grab OJAD until the anchor
        if is_numeric:
            while temp_ojad_idx < len(ojad_results):
                raw_text = ojad_results[temp_ojad_idx]["text"].strip()
                ojad_text = jaconv.kata2hira(raw_text)
                ojad_text_norm = _norm(raw_text)

                # Stop if reached the anchor (rendaku-tolerant comparison).
                if (
                    next_yahoo_furigana
                    and ojad_text_norm
                    and next_yahoo_furigana.startswith(ojad_text_norm)
                ):
                    break

                # Stop if consumed too much data
                if len(ojad_furigana) > max(len(yahoo_surface) * 4, 12):
                    logger.warning(
                        f" -> Numeric consumption exceeded limit '{yahoo_surface}'."
                    )
                    break

                ojad_furigana += ojad_text
                temp_accents.append(
                    AccentInfo(
                        furigana=ojad_text,
                        accent_marking_type=ojad_results[temp_ojad_idx]["accent"],
                        length=len(ojad_text),
                    )
                )
                temp_ojad_idx += 1
        # Normal mode: grab OJAD until length match
        else:
            while len(ojad_furigana) < len(yahoo_furigana) and temp_ojad_idx < len(
                ojad_results
            ):
                ojad_text = ojad_results[temp_ojad_idx]["text"]
                ojad_furigana += ojad_text
                temp_accents.append(
                    AccentInfo(
                        furigana=ojad_text,
                        accent_marking_type=ojad_results[temp_ojad_idx]["accent"],
                        length=len(ojad_text),
                    )
                )
                temp_ojad_idx += 1

        # Final matching check
        is_match = False
        if is_numeric:
            # Numeric mode: only check if OJAD has furigana grabbed
            is_match = len(ojad_furigana) > 0
        else:
            # Normal mode: check length and content (rendaku-tolerant).
            is_match = (
                len(ojad_furigana) == len(yahoo_furigana)
                and _norm(ojad_furigana) == _norm(yahoo_furigana)
            )

        if is_match:
            logger.debug(f" -> MATCHED! OJAD: {ojad_furigana}")
            accents.extend(temp_accents)

            # Build final accent info list
            accent_info_list = []
            for idx, accent in enumerate(accents):
                accent_info_list.append(
                    AccentInfo(
                        furigana=accent.furigana,
                        accent_marking_type=accent.accent_marking_type,
                        length=accent.length,
                    )
                )

            ojad_idx_cnt = temp_ojad_idx  # Update global index

            display_furigana = ojad_furigana if is_numeric else yahoo_furigana

            # Build final response
            if furigana_result.subword:
                yahoo_subword = furigana_result.subword
                logger.debug(
                    f"[Type Check] yahoo_subword element type: {type(yahoo_subword[0])}"
                )
                logger.debug(f"[Data Check] yahoo_subword content: {yahoo_subword}")
                final_response_results.append(
                    WordAccentResult(
                        furigana=display_furigana,
                        surface=yahoo_surface,
                        accent=accent_info_list,
                        subword=[
                            WordResult(furigana=s.furigana, surface=s.surface)
                            for s in yahoo_subword
                        ],
                    )
                )
            else:
                final_response_results.append(
                    WordAccentResult(
                        furigana=display_furigana,
                        surface=yahoo_surface,
                        accent=accent_info_list,
                    )
                )
        else:
            # [ERROR BLOCK]
            logger.error(
                "-> MATCH FAILED."
                f"Yahoo: {yahoo_furigana} vs OJAD Assembly: {ojad_furigana}"
            )

            # Fallback to Yahoo furigana with no accent info
            accent_info = AccentInfo(
                furigana=yahoo_furigana,
                accent_marking_type=0,
                length=len(yahoo_furigana),
            )

            final_response_results.append(
                WordAccentResult(
                    furigana=yahoo_furigana,
                    surface=yahoo_surface,
                    accent=[accent_info],
                )
            )

            # Move OJAD index to next item to avoid infinite loop
            if ojad_idx_cnt < len(ojad_results):
                ojad_idx_cnt += 1

    return final_response_results


@router.post("/MarkAccent/", tags=["MarkAccent"], response_model=Response)
async def mark_accent(
    request: Request, client: httpx.AsyncClient = Depends(get_http_client)
) -> Response:
    """Receive POST request, return a Response object"""
    logger.info(f"[API] Received Request Text: {request.text}")
    try:
        query_text = neologdn.normalize(request.text, tilde="normalize")

        # Apply furigana overrides BEFORE alignment: many of the overrides
        # (e.g. "4日"→"よっか", "27日"→"にじゅうしちにち") merge a numeric
        # surface with the counter into one token whose furigana matches what
        # OJAD reads as a single phrase. align_accent's numeric-anchor logic
        # otherwise cascades-fails on these inputs because numeric tokens lack
        # any Yahoo furigana for OJAD to align against.
        furigana_response = await mark_furigana(Request(text=query_text), client)
        if furigana_response.status != 200 or not furigana_response.result:
            logger.warning(f"Yahoo Response Empty or Invalid: {furigana_response}")
            return Response(
                status=furigana_response.status,
                result=None,
                error=furigana_response.error,
            )

        furigana_results = furigana_response.result
        logger.debug(f"Yahoo Results Count: {len(furigana_results)}")

        ojad_surface, ojad_results = await get_ojad_result(query_text, client)

        final_results = await align_accent(furigana_results, ojad_results)
        final_results = apply_accent_overrides(final_results)

        return Response(status=200, result=final_results)

    except Exception as e:
        logger.exception(f"Unexpected error occurred: {request.text}")
        return Response(
            status=500,
            result=None,
            error=ErrorInfo(code=500, message=f"Error: {e}"),
        )
