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


class Request(BaseModel):
    """Class representing a request object"""

    text: str = Field(description="The text to query")


class ErrorInfo(BaseModel):
    """Class representing an error information"""

    code: int = Field(description="The error code that follows JSON-RPC 2.0")
    message: str = Field(description="The error message that describe the details of an error")


class SearchResult(BaseModel):
    """Class representing a search result object"""

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


class Response(BaseModel):
    """Class representing a response object"""

    status: int = Field(default=200, description="Status code of response align with RFC 9110")
    result: list[SearchResult] = Field(description="A list contains search results")
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


def get_id(site: str, word: str) -> list[SearchResult] | None:
    """Get headword_id for the word."""
    match text_type(word):
        case "yomi":
            rules = [
                {"field": "yomi1", "op": "eq", "data": jaconv.hira2kata(word)},
                {"field": "yomi2", "op": "ew", "data": jaconv.hira2kata(word)},
                {"field": "yomi3", "op": "ew", "data": jaconv.hira2kata(word)},
            ]
        case "romaji":
            rules = [
                {"field": "romaji1", "op": "eq", "data": word},
                {"field": "romaji2", "op": "ew", "data": word},
                {"field": "romaji3", "op": "ew", "data": word},
            ]
        case "headword":
            rules = [{"field": "headword", "op": "eq", "data": word}]
        case _:
            print(f"Unknown text type for word '{word}'")
            return None

    filter = {"groupOp": "OR", "rules": rules}

    payload = {
        "_search": "true",
        "filters": json.dumps(filter),
    }

    url = f"{site}/headwordlist_all/"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": f"{site}/search/",
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
                        search_result = SearchResult(
                            id=id,
                            headword_id=headword_id,
                            headword=headword,
                            yomi_display=yomi_display,
                            romaji_display=romaji_display,
                            freq=freq,
                        )
                        print(f"Found result: {search_result}")
                        result.append(search_result)

                return result
            else:
                print(f"No results found for word '{word}'")
                return None
        except Exception as e:
            print(f"Error parsing JSON: {e}")
            return None
    else:
        print(f"HTTP error {response.status_code}")
        return None


def search(site: str, word: str) -> list[WordDetails]:
    """Search for a word in the specified site."""
    headwordlist = get_id(word)
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


@router.get("/nlb_search", response_model=list[SearchResult])
def nlb_search(
    word: str = Query(..., description="Japanese word to search for"),
):
    id = get_id(word)
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
        if word.lower() == "exit":
            break
        get_id(SITE["NLB"], word)
    # nlb_search("浴びる")
    # nlb_search("ない")
