from fastapi import APIRouter, Query
from pydantic import BaseModel
import requests

router = APIRouter()


class SearchResult(BaseModel):
    sentence: str
    source: str


@router.get("/nlb_search", response_model=list[SearchResult])
def nlb_search(
    word: str = Query(..., description="要查詢的日文單詞"),
    page: int = Query(1, ge=1, description="頁碼"),
    per_page: int = Query(10, ge=1, le=50, description="每頁筆數"),
):
    url = "https://nlb.ninjal.ac.jp/api/v1/search/"
    payload = {"query": word, "searchType": "word", "corpus": "BCCWJ", "pos": [], "page": page, "perPage": per_page}
    headers = {"Content-Type": "application/json", "Referer": "https://nlb.ninjal.ac.jp/", "User-Agent": "Mozilla/5.0"}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for hit in data.get("results", []):
            results.append(SearchResult(sentence=hit["sentence"], source=hit["source"]))
        return results
    except Exception as e:
        print(f'Exception happened with error {e}')
        return []
