import json
import re
from tkinter import W
from typing import Optional

import jaconv
import requests
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

SITE = {
    "NLB": "https://nlb.ninjal.ac.jp",
    "NLT": "https://tsukubawebcorpus.jp",
}


class WordRequest(BaseModel):
    """Class representing a request object"""

    word: str = Field(description="The word to query")
    site: str = Field(default="NLB", description="The site to query, either 'NLB' or 'NLT'. Default is 'NLB'.")


class IdRequest(BaseModel):
    """Class representing a request object for ID"""

    id: int = Field(description="The ID of the word")
    site: str = Field(default="NLB", description="The site to query, either 'NLB' or 'NLT'. Default is 'NLB'.")


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


class WordDetails(BaseModel):
    """Class representing details of a word"""

    headword: str = Field(description="The headword of the word")
    yomi1: str = Field(description="The first yomi of the word")
    yomi2: Optional[str] = Field(default=None, description="The second yomi of the word")
    yomi3: Optional[str] = Field(default=None, description="The third yomi of the word")
    romaji1: str = Field(description="The first romaji of the word")
    romaji2: Optional[str] = Field(default=None, description="The second romaji of the word")
    romaji3: Optional[str] = Field(default=None, description="The third romaji of the word")


class HeadWordResponse(BaseModel):
    """Class representing a response object for headword query"""

    status: int = Field(default=200, description="Status code of response align with RFC 9110")
    result: list[HeadWord] = Field(description="A list contains headword results")
    error: Optional[ErrorInfo] = Field(
        default=None, description="An object that describe the details of an error when occur"
    )


class WordResponse(BaseModel):
    """Class representing a response object for word details"""

    status: int = Field(default=200, description="Status code of response align with RFC 9110")
    result: list[WordDetails] = Field(description="A list contains word details")
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


@router.post("/QueryHeadWord", tags=["UsageQuery"], response_model=HeadWordResponse)
def get_headword(request: WordRequest) -> list[HeadWord] | None:
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
        case _:
            print(f"Unknown text type for word '{request.word}'")
            return None

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
                        print(f"Found result: {search_result}")
                        result.append(search_result)

                ret = HeadWordResponse(status=200, result=result)
            else:
                print(f"No results found for word '{request.word}'")
                ret = HeadWordResponse(
                    status=404,
                    result=[],
                    error=ErrorInfo(code=404, message=f"No results found for word '{request.word}'"),
                )
        except Exception as e:
            print(f"Error parsing JSON: {e}")
            ret = HeadWordResponse(
                status=500,
                result=[],
                error=ErrorInfo(code=500, message=f"Error parsing JSON: {e}"),
            )
    else:
        print(f"HTTP error {response.status_code}")
        ret = HeadWordResponse(
            status=response.status_code,
            result=[],
            error=ErrorInfo(code=response.status_code, message=f"HTTP error {response.status_code}"),
        )

    print(f"Returning response: {ret}")
    return ret.model_dump()


def search(site: str, word: str) -> list[WordDetails]:
    """Search for a word in the specified site."""
    headwordlist = get_headword(word)
    if not headwordlist:
        return []

    url = f"https://nlb.ninjal.ac.jp/patternfreqorder/{id}/"
    headers = {
        "Content-Type": "application/json",
        "Referer": f"https://nlb.ninjal.ac.jp/headword/{id}/",
    }

    response = requests.post(url, headers=headers)

    if response.status_code == 200:
        try:
            data = response.json()
            if data.get("rows") and len(data["rows"]) > 0:
                results = [(row["name"], row["freq"]) for row in data["rows"]]
                print(results)
                return results
            else:
                print(f"No results found for word '{word}'")
                return None
        except Exception as e:
            print(f"Error parsing JSON: {e}")
            return None
    else:
        print(f"HTTP error {response.status_code}")
        return []


@router.get("/nlb_search", response_model=list[HeadWord])
def nlb_search(
    word: str = Query(..., description="Japanese word to search for"),
):
    id = get_headword(word)
    if not id:
        return []

    url = f"https://nlb.ninjal.ac.jp/patternfreqorder/{id}/"
    headers = {
        "Content-Type": "application/json",
        "Referer": f"https://nlb.ninjal.ac.jp/headword/{id}/",
    }

    response = requests.post(url, headers=headers)

    if response.status_code == 200:
        try:
            data = response.json()
            if data.get("rows") and len(data["rows"]) > 0:
                results = [(row["name"], row["freq"]) for row in data["rows"]]
                print(results)
                return results
            else:
                print(f"No results found for word '{word}'")
                return None
        except Exception as e:
            print(f"Error parsing JSON: {e}")
            return None
    else:
        print(f"HTTP error {response.status_code}")
        return []


# Test code
if __name__ == "__main__":
    while True:
        word = input("Enter a word to search (or 'exit' to quit): ")
        if word.lower() == "q":
            break
        get_headword(WordRequest(word=word, site="NLB"))
    while True:
        word = input("Enter a word to search (or 'exit' to quit): ")
        if word.lower() == "q":
            break
        get_headword(WordRequest(word=word, site="NLT"))
    # nlb_search("浴びる")
    # nlb_search("ない")
