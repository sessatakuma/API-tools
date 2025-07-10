import json

import requests
from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()


class SearchResult(BaseModel):
    sentence: str
    source: str


def get_id(word: str) -> str | None:
    """Get headword_id for the word."""

    filter = {"groupOp": "OR", "rules": [{"field": "headword", "op": "eq", "data": word}]}

    payload = {
        "_search": "true",
        "filters": json.dumps(filter),
    }

    url = "https://nlb.ninjal.ac.jp/headwordlist_all/"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": "https://nlb.ninjal.ac.jp/search/",
        "User-Agent": "Mozilla/5.0",
    }

    response = requests.post(url, data=payload, headers=headers)

    if response.status_code == 200:
        try:
            data = response.json()
            if data.get("rows") and len(data["rows"]) > 0:
                headword_id = data["rows"][0]["headword_id"]
                print(f"Found headword_id: {headword_id} for word '{word}'")
                return headword_id
            else:
                print(f"No results found for word '{word}'")
                return None
        except Exception as e:
            print(f"Error parsing JSON: {e}")
            return None
    else:
        print(f"HTTP error {response.status_code}")
        return None


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


if __name__ == "__main__":
    nlb_search("浴びる")
    nlb_search("ない")
