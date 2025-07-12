import json
import re
from typing import Literal, Optional

import jaconv
import requests
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

SITE = {
    "NLB": "https://nlb.ninjal.ac.jp",
    "NLT": "https://tsukubawebcorpus.jp",
}


class HeadWordRequest(BaseModel):
    """Class representing a request object"""

    word: str = Field(description="The word to query")
    site: Literal["NLB", "NLT"] = Field(
        default="NLB", description="The site to query, either 'NLB' or 'NLT'. Default is 'NLB'."
    )


class IdRequest(BaseModel):
    """Class representing a request object for ID"""

    id: str = Field(description="The ID of the word")
    site: Literal["NLB", "NLT"] = Field(
        default="NLB", description="The site to query, either 'NLB' or 'NLT'. Default is 'NLB'."
    )


class ErrorInfo(BaseModel):
    """Class representing an error information"""

    code: int = Field(description="The error code that follows JSON-RPC 2.0")
    message: str = Field(description="The error message that describe the details of an error")


class HeadWord(BaseModel):
    """Class representing a headword object"""

    id: int = Field(description="The id of the word")
    headword_id: str = Field(description="The headword id of the word")
    headword: str = Field(description="The headword of the word in kanji")
    yomi_display: str = Field(description="The yomi display of the word in katakana")
    romaji_display: str = Field(description="The romaji display of the word")
    freq: int = Field(description="The frequency of the word in the corpus")


class IdDetails(BaseModel):
    """Class representing details of a word"""

    base: dict = Field(description="The base form of the word")
    subcorpus: list[dict] = Field(description="The subcorpus of the word")
    shojikei: list[dict] = Field(description="The shojikei of the word")
    subcorpus_shojikei: list[dict] = Field(description="The distribution of shojikei by subcorpus of the word")
    katuyokei: list[dict] = Field(description="The katuyokei of the word")
    setuzoku: list[dict] = Field(description="The subsequent auxiliary verbs of the word")
    patternfreqorder: list[dict] = Field(description="The frequency of the word in different patterns")


class HeadWordResponse(BaseModel):
    """Class representing a response object for headword query"""

    status: int = Field(default=200, description="Status code of response align with RFC 9110")
    result: Optional[list[HeadWord]] = Field(description="A list contains headword results")
    error: Optional[ErrorInfo] = Field(
        default=None, description="An object that describe the details of an error when occur"
    )


class URLResponse(BaseModel):
    """Class representing a response object for URL query"""

    status: int = Field(default=200, description="Status code of response align with RFC 9110")
    result: Optional[list[str]] = Field(description="A list contains URLs for the headwords")
    error: Optional[ErrorInfo] = Field(
        default=None, description="An object that describe the details of an error when occur"
    )


class IdResponse(BaseModel):
    """Class representing a response object for word details"""

    status: int = Field(default=200, description="Status code of response align with RFC 9110")
    result: Optional[IdDetails] = Field(description="Details of the word with the given ID")
    error: Optional[ErrorInfo] = Field(
        default=None, description="An object that describe the details of an error when occur"
    )


router = APIRouter()


def text_type(text: str) -> str | None:
    """Determine the type of text: yomi, romaji, or headword."""

    if not text:
        return None

    if bool(re.fullmatch(r"[\u3040-\u309F\u30A0-\u30FF]+", text)):
        # The string consists only of hiragana or katakana characters
        return "yomi"
    elif bool(re.fullmatch(r"[A-Za-z]+", text)):
        # The string consists only of romaji
        return "romaji"
    else:
        return "headword"


@router.post("/UsageQuery/HeadWords/", tags=["UsageQuery"], response_model=HeadWordResponse)
def get_headwords(request: HeadWordRequest):
    """Get headword_list for the word."""

    match text_type(request.word):
        case "yomi":
            rules = [
                {"field": "yomi1", "op": "eq", "data": jaconv.hira2kata(request.word)},
                {"field": "yomi2", "op": "ew", "data": jaconv.hira2kata(request.word)},
                {"field": "yomi3", "op": "ew", "data": jaconv.hira2kata(request.word)},
            ]
        case "romaji":
            rules = [
                {"field": "romaji1", "op": "eq", "data": request.word},
                {"field": "romaji2", "op": "ew", "data": request.word},
                {"field": "romaji3", "op": "ew", "data": request.word},
            ]
        case "headword":
            rules = [{"field": "headword", "op": "eq", "data": request.word}]

    filter = {"groupOp": "OR", "rules": rules}

    payload = {
        "_search": "true",
        "filters": json.dumps(filter),
    }

    url = f"{SITE[request.site]}/headwordlist_all/"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": f"{SITE[request.site]}/search/",
        "User-Agent": "Mozilla/5.0",
    }

    response = requests.post(url, data=payload, headers=headers)

    if response.status_code == 200:
        try:
            data = response.json()
            result = []
            if data.get("rows") and len(data["rows"]) > 0:
                for row in data["rows"]:
                    id = row.get("id")
                    headword_id = row.get("headword_id")
                    headword = row.get("headword")
                    yomi_display = row.get("yomi_display")
                    romaji_display = row.get("romaji_display")
                    freq = row.get("freq")
                    if id and headword_id and headword and yomi_display and romaji_display and freq:
                        search_result = HeadWord(
                            id=id,
                            headword_id=headword_id,
                            headword=headword,
                            yomi_display=yomi_display,
                            romaji_display=romaji_display,
                            freq=freq,
                        )
                        # print(f"Found result: {search_result}")
                        result.append(search_result)
            return HeadWordResponse(status=200, result=result).model_dump()
        except Exception as e:
            print(f"Error parsing JSON: {e}")
            return HeadWordResponse(
                status=500,
                result=None,
                error=ErrorInfo(code=500, message=f"Error parsing JSON: {e}"),
            ).model_dump()
    else:
        print(f"HTTP error {response.status_code}")
        return HeadWordResponse(
            status=response.status_code,
            result=None,
            error=ErrorInfo(code=response.status_code, message=f"HTTP error {response.status_code}"),
        ).model_dump()


@router.post("/UsageQuery/URL/", tags=["UsageQuery"], response_model=URLResponse)
def get_urls(request: HeadWordRequest):
    """Get URL for the word with the given word."""
    response = get_headwords(request)

    if response["status"] != 200:
        return URLResponse(
            status=response["status"],
            result=None,
            error=response["error"],
        ).model_dump()

    result = [f'{SITE[request.site]}/headword/{headword["headword_id"]}/' for headword in response["result"]]
    print(f"Generated URLs: {result}")

    return URLResponse(status=200, result=result).model_dump()


@router.post("/UsageQuery/IdDetails/", tags=["UsageQuery"], response_model=IdResponse)
def get_id_details(request: IdRequest):
    """Get details for the word with the given ID."""

    def fetch_data(mode: Literal["get", "post"], endpoint: str, target: str = ""):
        """Helper function to fetch and parse JSON data"""
        match mode:
            case "get":
                headers = {
                    "Referer": f"{SITE[request.site]}/headword/{request.id}/",
                    "User-Agent": "Mozilla/5.0",
                }
                response = requests.get(f"{SITE[request.site]}/{endpoint}/{request.id}/", headers=headers)
            case "post":
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Referer": f"{SITE[request.site]}/headword/{request.id}/",
                    "User-Agent": "Mozilla/5.0",
                }
                response = requests.post(f"{SITE[request.site]}/{endpoint}/{request.id}/", headers=headers)

        if response.status_code == 200:
            try:
                data = response.json()
                ret = data[target] if target and data.get(target) else data
                # print(f"Fetched {target}: {ret}")
                return ret
            except Exception as e:
                print(f"Error parsing JSON while fetching {endpoint}: {e}")
                return IdResponse(
                    status=500,
                    result=None,
                    error=ErrorInfo(code=500, message=f"Error parsing JSON while fetching {endpoint}: {e}"),
                )
        else:
            print(f"HTTP error {response.status_code} while fetching {endpoint}")
            return IdResponse(
                status=response.status_code,
                result=None,
                error=ErrorInfo(
                    code=response.status_code, message=f"HTTP error {response.status_code} while fetching {endpoint}"
                ),
            )

    # IdResponse.base
    base = fetch_data("get", "basicinfob")
    if isinstance(base, IdResponse):
        return base.model_dump()
    # IdResponse.subcorpus
    subcorpus = [] if request.site == "NLT" else fetch_data("get", "basicinfosc", "subcorpus")
    if isinstance(subcorpus, IdResponse):
        return subcorpus.model_dump()
    # IdResponse.shojikei
    shojikei = fetch_data("get", "basicinfosj", "shojikei")
    if isinstance(shojikei, IdResponse):
        return shojikei.model_dump()
    # IdResponse.subcorpus_shojikei
    subcorpus_shojikei = [] if request.site == "NLT" else fetch_data("post", "basicinfoss", "subcorpus")
    if isinstance(subcorpus_shojikei, IdResponse):
        return subcorpus_shojikei.model_dump()
    # IdResponse.katuyokei
    katuyokei = [] if request.site == "NLT" else fetch_data("get", "basicinfoky", "katuyokei")
    if isinstance(katuyokei, IdResponse):
        return katuyokei.model_dump()
    # IdResponse.setuzoku
    setuzoku = fetch_data("get", "basicinfojs", "setuzoku")
    if isinstance(setuzoku, IdResponse):
        return setuzoku.model_dump()
    # IdResponse.patternfreqorder
    patternfreqorder = fetch_data("post", "patternfreqorder", "rows")
    if isinstance(patternfreqorder, IdResponse):
        return patternfreqorder.model_dump()

    print(patternfreqorder)

    return IdResponse(
        status=200,
        result=IdDetails(
            base=base,
            subcorpus=subcorpus,
            shojikei=shojikei,
            subcorpus_shojikei=subcorpus_shojikei,
            katuyokei=katuyokei,
            setuzoku=setuzoku,
            patternfreqorder=patternfreqorder,
        ),
    ).model_dump()


# Test code
if __name__ == "__main__":
    get_urls(HeadWordRequest(word="走る", site="NLB"))
    get_urls(HeadWordRequest(word="はしる", site="NLB"))
    get_urls(HeadWordRequest(word="hashiru", site="NLB"))
    get_urls(HeadWordRequest(word="走る", site="NLT"))
    get_urls(HeadWordRequest(word="はしる", site="NLT"))
    get_urls(HeadWordRequest(word="hashiru", site="NLT"))
    # print("==== Testing NLB ====")
    # get_headwords(HeadWordRequest(word="走る", site="NLB"))
    # print("=" * 10)
    # get_headwords(HeadWordRequest(word="はしる", site="NLB"))
    # print("=" * 10)
    # get_headwords(HeadWordRequest(word="hashiru", site="NLB"))
    # print("\n==== Testing NLT ====")
    # get_headwords(HeadWordRequest(word="走る", site="NLT"))
    # print("=" * 10)
    # get_headwords(HeadWordRequest(word="はしる", site="NLT"))
    # print("=" * 10)
    # get_headwords(HeadWordRequest(word="hashiru", site="NLT"))
    # print("=" * 100)
    # print("\n==== Testing NLB ====")
    # get_id_details(IdRequest(id="V.00093", site="NLB"))
    # print("\n==== Testing NLT ====")
    # get_id_details(IdRequest(id="V.00128", site="NLT"))
