from typing import List,Optional

import requests
from fastapi import APIRouter
from pydantic import BaseModel, Field
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs


class Request(BaseModel):
    """Class representing a request object"""

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
    """Class representing an error information"""

    code: int = Field(description="The error code that follows JSON-RPC 2.0")
    message: str = Field(description="The error message that describe the details of an error")

class Response(BaseModel):
    status: int = Field(default=200, description="Status code")
    result: List[WordResult] = Field(description="A list of search results")
    error: Optional[ErrorInfo] = Field(default=None, description="Error message if any")



router = APIRouter()

# 取得所有符合資料的 url
def get_all_url(search_word : str) -> list:
    url = f"https://www.edrdg.org/jmwsgi/srchres.py?s1=1&y1=1&t1={search_word}&src=1&search=Search&svc=jmdict"

    response = requests.get(url)
    response.encoding = response.apparent_encoding

    # 判斷是否因為只有一個結果而直接跳轉
    if "entr.py" in response.url:
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
def get_dict(url_list : list):
    results = []

    for url in url_list:
        response = requests.get(url)
        response.encoding = response.apparent_encoding
        soup = BeautifulSoup(response.text, "html.parser")

        kanji = [k.get_text(strip=True) for k in soup.select("span.kanj")]
        furigana = [k.get_text(strip=True) for k in soup.select("span.rdng")]

        definitions = []
        for sense in soup.select("table.senses"):
            pos = [k.get_text(" ", strip=True) for k in sense.select("span.pos span.abbr")]
            meanings = [k.get_text(" ", strip=True).replace("▶", "").strip() for k in sense.select("span.glossx")]
            definitions.append(Definition(pos=pos, meanings=meanings))

        jmdict_id = soup.select_one('a[href^="srchres.py"]')
        id = int(jmdict_id.get_text(strip=True)) if jmdict_id else 0

        results.append(WordResult(
            kanji=kanji,
            furigana=furigana,
            definitions=definitions,
            id = id
        ))

    return results

@router.post("/DictQuery/", tags=["DictionaryQuery"], response_model=Response)
def dict_query(request: Request):
    """Query weblio dictionary URL for the given word."""

    results = get_dict(get_all_url(request.word))
    return Response(
        status=200,
        result=results,
        error=None
    ).model_dump()


# Test codes
if __name__ == "__main__":
    print(dict_query(Request(word="先生")))
    print(dict_query(Request(word="少女")))
    print(dict_query(Request(word="食べる")))
    print(dict_query(Request(word="タベル")))
    print(dict_query(Request(word="たべる")))
