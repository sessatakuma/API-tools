from typing import List, Optional
import asyncio
import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
from api.dependencies import get_http_client

class Request(BaseModel):
    word: str = Field(description="The word to query")

class Definition(BaseModel):
    pos: List[str] = Field(description="pos list")
    meanings: List[str] = Field(description="Meanings of the word")

class WordResult(BaseModel):
    kanji: List[str] = Field(description="Kanji")
    furigana: List[str] = Field(description="Furigana")
    definitions: List[Definition] = Field(description="Definitions of the word")
    id: int = Field(description="ID")

class ErrorInfo(BaseModel):
    code: int = Field(description="Error code (similar to HTTP status)")
    message: str = Field(description="Details of the error")

class Response(BaseModel):
    status: int = Field(default=200, description="Status code")
    result: Optional[List[WordResult]] = Field(default=None, description="List of results")
    error: Optional[ErrorInfo] = Field(default=None, description="Error details")

router = APIRouter()

# 取得所有符合資料的 url
async def get_all_url(search_word: str, client: httpx.AsyncClient) -> list:
    url = f"https://www.edrdg.org/jmwsgi/srchres.py?s1=1&y1=1&t1={search_word}&src=1&search=Search&svc=jmdict"

    try:
        response = await client.get(url)
        response.encoding = response.charset_encoding or "utf-8"
    except httpx.RequestError as e:
        raise RuntimeError(f"Network error: {str(e)}")

    # 判斷是否因為只有一個結果而直接跳轉
    if "entr.py" in str(response.url):
        entry_id = parse_qs(urlparse(response.url).query).get("e", [None])[0]
        return [f"https://www.edrdg.org/jmwsgi/entr.py?svc=jmdict&e={entry_id}"] if entry_id else []
    
    soup = BeautifulSoup(response.text, "html.parser")
    rows = soup.find_all("tr", class_="resrow")

    url_list = []
    for row in rows:
        inp = row.find("input", {"name": "e"})
        if inp and inp.has_attr("value"):
            url_list.append(f"https://www.edrdg.org/jmwsgi/entr.py?svc=jmdict&e={inp['value']}")

    return url_list

# 根據 url 清單回傳查詢結果
async def get_dict(url_list: list, client: httpx.AsyncClient) -> list:
    results = []
    for url in url_list:
        try:
            response = await client.get(url)
            response.encoding = response.charset_encoding or "utf-8"
        except httpx.RequestError  as e:
            raise RuntimeError(f"Network error: {str(e)}")

        soup = BeautifulSoup(response.text, "html.parser")

        try:
            kanji = [k.get_text(strip=True) for k in soup.select("span.kanj")]
            furigana = [k.get_text(strip=True) for k in soup.select("span.rdng")]

            definitions = []
            for sense in soup.select("tr.sense"):
                pos = [k.get_text(" ", strip=True) for k in sense.select("span.pos span.abbr")]
                meanings = [k.get_text(" ", strip=True).replace("▶", "").strip() for k in sense.select("span.glossx")]
                definitions.append(Definition(pos=pos, meanings=meanings))

            jmdict_id = soup.select_one('a[href^="srchres.py"]')
            id = int(jmdict_id.get_text(strip=True)) if jmdict_id else 0

            results.append(WordResult(
                kanji=kanji,
                furigana=furigana,
                definitions=definitions,
                id=id
            ))
        except Exception as e:
            raise RuntimeError(f"Parse error: {str(e)}")

    return results

@router.post("/DictQuery/", tags=["DictionaryQuery"], response_model=Response)
async def dict_query(request: Request, client: httpx.AsyncClient = Depends(get_http_client)):
    """Query JMdict dictionary for the given word."""

    try:
        url_list = await get_all_url(request.word, client)
        if not url_list:
            return Response(
                status=404,
                result=None,
                error=ErrorInfo(code=404, message="No results found")
            ).model_dump()

        results = await get_dict(url_list, client)
        return Response(
            status=200,
            result=results,
            error=None
        ).model_dump()

    except RuntimeError as e:
        return Response(
            status=500,
            result=None,
            error=ErrorInfo(code=500, message=str(e))
        ).model_dump()


# Test codes
if __name__ == "__main__":
    async def test():
        async with httpx.AsyncClient() as client:
            print(await dict_query(Request(word="先生"), client))
            print(await dict_query(Request(word="少女"), client))
            print(await dict_query(Request(word="食べる"), client))
            print(await dict_query(Request(word="嗨嗨"), client))

    asyncio.run(test())