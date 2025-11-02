import asyncio
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup, Comment
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.dependencies import get_http_client


class Request(BaseModel):
    """Class representing a request object"""

    word: str = Field(description="The word to query")
    id: int = Field(
        description="The unique ID of a word in JMdict.\n"
        "Must be obtained through DictionaryQuery to fetch example sentences."
    )


class WordSentence(BaseModel):
    jp: str = Field(description="jp sentence")
    en: str = Field(description="en sentence")


class WordResult(BaseModel):
    word: str = Field(description="Word")
    id: int = Field(description="ID")
    sentence: list[WordSentence] = Field(description="A list of sentence")


class Response(BaseModel):
    status: int = Field(default=200, description="Status code")
    result: WordResult | None = Field(description="Results")
    error: str | None = Field(default=None, description="Error message if any")


router = APIRouter()


# 根據接收到的漢字及ID回傳可能的例句
@router.post("/SentenceQuery/", tags=["SentenceQuery"], response_model=Response)
async def sentence_query(
    request: Request,
    client: httpx.AsyncClient = Depends(get_http_client),
) -> dict[str, Any]:
    """
    Example sentences from JMdict.

    - Uses the provided `word`(kanji or furigana) and `id` from DictionaryQuery.
    - Returns a list of sentences containing
    both Japanese text and their English translations.
    """
    url = "https://www.edrdg.org/cgi-bin/wwwjdic/wwwjdic?1E"
    payload = {"dsrchkey": request.word, "dicsel": "1"}

    try:
        response = await client.post(url, data=payload)
        response.encoding = response.charset_encoding or "utf-8"
    except httpx.RequestError as e:
        return Response(
            status=500, result=None, error=f"Network error: {str(e)}"
        ).model_dump()

    try:
        soup = BeautifulSoup(response.text, "html.parser")

        sentences = []
        found_block = False
        for block in soup.select("div[style*=clear]"):
            comments = block.find_all(string=lambda text: isinstance(text, Comment))
            if not any(f"ent_seq={request.id}" in c for c in comments):
                continue
            found_block = True
            for br in block.find_all("br"):
                nxt = br.find_next_sibling("font")

                if nxt and nxt.get("size") == "-1":
                    s = nxt.get_text(" ", strip=True)

                    s = re.sub(r"^\(\d+\)\s*", "", s)
                    jp, en = re.split(r"\t+|\s{2,}", s, maxsplit=1)
                    jp = jp.replace(" ", "")

                    sentences.append(WordSentence(jp=jp.strip(), en=en.strip()))

        if not found_block or not sentences:
            return Response(
                status=404, result=None, error="No results found"
            ).model_dump()

        return Response(
            status=200,
            result=WordResult(word=request.word, id=request.id, sentence=sentences),
            error=None,
        ).model_dump()

    except Exception as e:
        return Response(
            status=500, result=None, error=f"Parse error: {str(e)}"
        ).model_dump()


# Test codes
if __name__ == "__main__":

    async def test() -> None:
        async with httpx.AsyncClient() as client:
            print(await sentence_query(Request(word="先生", id=1387990), client))
            print(await sentence_query(Request(word="せんせい", id=1387990), client))
            print(await sentence_query(Request(word="少女", id=1580290), client))
            print(await sentence_query(Request(word="嗨嗨", id=1580290), client))

    asyncio.run(test())
