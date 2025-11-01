"""
An API that mark furigana of given query text
"""

import json
import os
from typing import Any

import requests  # type: ignore
import yaml
from fastapi import APIRouter
from pydantic import BaseModel, Field

tags_metadata = [
    {
        "name": "MarkFurigana",
        "description": "Mark furigana of given text",
    },
]


class Request(BaseModel):
    """Class representing a request object"""

    text: str = Field(description="The text to query")


class ErrorInfo(BaseModel):
    """Class representing an error information"""

    code: int = Field(description="The error code that follows JSON-RPC 2.0")
    message: str = Field(
        description="The error message that describe the details of an error"
    )


class SingleWordResultObject(BaseModel):
    """Class representing a single word result object"""

    furigana: str = Field(description="Furigana of given kana and kanji")
    surface: str = Field(description="The (partial of) original query text")


class MultiWordResultObject(SingleWordResultObject):
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
    result: list[SingleWordResultObject | MultiWordResultObject] = Field(
        description="A list contains marked results"
    )
    error: ErrorInfo | None = Field(
        default=None,
        description="An object that describe the details of an error when occur",
    )


router = APIRouter()

url = "https://jlp.yahooapis.jp/FuriganaService/V2/furigana"

if "Yahoo_API_key" in os.environ:
    clientid = os.environ["Yahoo_API_key"]
else:
    with open("./secret.yaml", encoding="utf-8") as f:
        clientid = yaml.safe_load(f)["Yahoo_API_key"]


@router.post("/MarkFurigana/", tags=["MarkFurigana"], response_model=Response)
def mark_furigana(request: Request) -> dict[str, Any]:
    """Receive POST request, return a JSON response"""
    query_text = request.text

    # 輸入
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Yahoo AppID: {clientid}",
    }

    data = {
        "id": "1234-1",
        "jsonrpc": "2.0",
        "method": "jlp.furiganaservice.furigana",
        "params": {"q": query_text, "grade": 1},
    }

    # 呼叫API
    response = requests.post(url, headers=headers, data=json.dumps(data))
    result = response.json()

    # 輸出結果
    words = result["result"]["word"]
    parsed_result: list[SingleWordResultObject | MultiWordResultObject] = []

    for word in words:
        if "subword" in word:
            subword_list = [
                SingleWordResultObject(furigana=sub["furigana"], surface=sub["surface"])
                for sub in word["subword"]
            ]
            parsed_result.append(
                MultiWordResultObject(
                    surface=word["surface"],
                    furigana=word["furigana"],
                    subword=subword_list,
                )
            )
        else:
            parsed_result.append(
                SingleWordResultObject(
                    surface=word["surface"],
                    furigana=word.get("furigana", word["surface"]),
                )
            )

    return Response(status=200, result=parsed_result).model_dump()
