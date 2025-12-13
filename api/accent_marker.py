"""
An API that mark accent of given query text
"""

import logging
import string
from typing import Any

import httpx
import jaconv
import neologdn
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import get_http_client
from api.furigana_marker import Request, SingleWordResultObject, mark_furigana

logger = logging.getLogger(__name__)

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


class ErrorInfo(BaseModel):
    """Class representing an error information"""

    code: int = Field(description="The error code that follows JSON-RPC 2.0")
    message: str = Field(
        description="The error message that describe the details of an error"
    )
    length: int = Field(description="Length of the furigana")


class AccentInfo(BaseModel):
    """Class representing an accent information"""

    furigana: str = Field(description="The furigana of given kana and kanji")
    accent_marking_type: int = Field(
        description="The type of accent, including none (0), heiban (1), fall (2)"
    )
    length: int = Field(description="Length of the furigana")


class SingleWordAccentResultObject(BaseModel):
    """Class representing a single word result object"""

    furigana: str = Field(description="Furigana of given kana and kanji")
    surface: str = Field(description="The (partial of) original query text")
    accent: list[AccentInfo] = Field(description="The accent of givent word")


class MultiWordAccentResultObject(SingleWordAccentResultObject):
    """Class representing a multiple word result object"""

    subword: list[SingleWordResultObject] = Field(
        description="""A list contains more details when a \
        word contains both kanji and kana. Each elements in \
        subword is a dict with furigana and surface."""
    )


class Response(BaseModel):
    """Class representing a response object"""

    status: int = Field(
        default=200, description="Status code of response align with RFC 9110"
    )
    result: list[SingleWordAccentResultObject | MultiWordAccentResultObject] | None = (
        Field(description="A list contains marked results")
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


@router.post("/MarkAccent/", tags=["MarkAccent"], response_model=Response)
async def mark_accent(
    request: Request, client: httpx.AsyncClient = Depends(get_http_client)
) -> dict[str, Any]:
    """Receive POST request, return a JSON response"""
    logger.info(f"[API] Received Request Text: {request.text}")
    try:
        query_text = neologdn.normalize(request.text, tilde="normalize")

        # 1. 呼叫 Yahoo Furigana
        furigana_response = await mark_furigana(Request(text=query_text), client)

        # 檢查 Yahoo 回傳
        if not furigana_response or "result" not in furigana_response:
            logger.warning(f"Yahoo Response Empty or Invalid: {furigana_response}")
            # 如果 Yahoo 沒東西，這裡就會報錯或導致後面空值

        furigana_results: list[dict[str, Any]] = furigana_response.get("result", [])
        logger.debug(f"Yahoo Results Count: {len(furigana_results)}")

        # 2. 呼叫 OJAD
        ojad_surface, ojad_results = await get_ojad_result(query_text, client)

        final_response_results = []
        ojad_idx_cnt = 0

        logger.debug(f"🔍 [Type Check] furigana_results type: {type(furigana_results)}")
        logger.debug(
            "[Data Check] furigana_results sample (first item): "
            f"{furigana_results[0] if furigana_results else 'Empty'}"
        )

        for i, furigana_result in enumerate(furigana_results):
            logger.debug(f"  🔍 [Type Check] Item [{i}] type: {type(furigana_result)}")
            logger.debug(f"  🔍 [Data Check] Item keys: {furigana_result.keys()}")
            yahoo_furigana = furigana_result["furigana"]
            yahoo_furigana_hira = jaconv.kata2hira(yahoo_furigana)
            yahoo_surface = furigana_result["surface"]
            accents: list[AccentInfo] = []

            logger.debug(
                f"Processing Yahoo Word [{i}]: {yahoo_surface} ({yahoo_furigana})"
            )

            # If query sub-text contains non-kana and non-kanji words, ignore it
            if "subword" not in furigana_result and any(
                not is_kana_or_kanji(chr) for chr in yahoo_furigana
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
                    SingleWordAccentResultObject(
                        furigana=yahoo_furigana, surface=yahoo_surface, accent=accents
                    )
                )
                continue

            # Remove all mismatching prefix
            ojad_idx = ojad_idx_cnt

            # DEBUG matching logic
            if ojad_idx < len(ojad_results):
                ojad_current_hira = jaconv.kata2hira(ojad_results[ojad_idx]["text"])
                logger.debug(
                    "-> Comparing Yahoo '{yahoo_furigana_hira}'"
                    f" vs OJAD '{ojad_current_hira}'"
                )
            else:
                logger.warning(f" -> OJAD Index Out of Bounds ({ojad_idx})")

            while ojad_idx < len(ojad_results) and not yahoo_furigana_hira.startswith(
                jaconv.kata2hira(ojad_results[ojad_idx]["text"])
            ):
                ojad_idx += 1

            # catch the furigana from Yahoo with OJAD results
            ojad_furigana = ""
            temp_accents = []  # Use temp list to avoid partial data

            # Backup index
            temp_ojad_idx = ojad_idx

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

            # If match success
            if len(ojad_furigana) == len(yahoo_furigana) and jaconv.kata2hira(
                ojad_furigana
            ) == jaconv.kata2hira(yahoo_furigana):
                logger.debug(f" -> MATCHED! OJAD: {ojad_furigana}")
                accents.extend(temp_accents)  # Confirm accents

                # Build accent info list

                accent_info_list = []
                yahoo_furigana_idx = 0
                for idx, accent in enumerate(accents):
                    accent_info_list.append(
                        AccentInfo(
                            furigana=yahoo_furigana[
                                yahoo_furigana_idx : yahoo_furigana_idx + accent.length
                            ],
                            accent_marking_type=accent.accent_marking_type,
                            length=accent.length,
                        )
                    )
                    yahoo_furigana_idx += accent.length

                ojad_idx_cnt = temp_ojad_idx  # Update global index

                if "subword" not in furigana_result:
                    final_response_results.append(
                        SingleWordAccentResultObject(
                            furigana=yahoo_furigana,
                            surface=yahoo_surface,
                            accent=accent_info_list,
                        )
                    )
                else:
                    yahoo_subword = furigana_result["subword"]
                    if len(yahoo_subword) > 0:
                        logger.debug(
                            "[Type Check] yahoo_subword element type: "
                            f"{type(yahoo_subword[0])}"
                        )
                        logger.debug(
                            f"[Data Check] yahoo_subword content: {yahoo_subword}"
                        )
                    final_response_results.append(
                        MultiWordAccentResultObject(
                            furigana=yahoo_furigana,
                            surface=yahoo_surface,
                            accent=accent_info_list,
                            subword=[
                                SingleWordResultObject(
                                    furigana=s["furigana"], surface=s["surface"]
                                )
                                for s in yahoo_subword
                            ],
                        )
                    )
            else:
                # [ERROR BLOCK]
                logger.error(
                    "-> MATCH FAILED."
                    f"Yahoo: {yahoo_furigana} vs OJAD Assembly: {ojad_furigana}"
                )
                # 即使失敗，也要讓迴圈繼續，或者考慮怎麼處理錯誤
                # 目前程式碼遇到錯誤就沒有 append result，所以回傳會少東西

        response = Response(status=200, result=final_response_results)

    except Exception as e:
        import traceback

        traceback.print_exc()  # 把完整錯誤印出來
        response = Response(
            status=500,
            result=None,
            error=ErrorInfo(code=500, message=f"Error: {e}", length=0),
        )
    return response.model_dump()
