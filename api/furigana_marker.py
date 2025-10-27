"""
An API that mark furigana of given query text
"""
import asyncio

import aiohttp
import osc
from typing import Optional, List
from fastapi import APIRouter
from pydantic import BaseModel, Field
import json
import yaml

tags_metadata = [
    {
        "name": "MarkFurigana",
        "description": "Mark furigana of given text",
    },
]

class Request(BaseModel):
    """Class representing a request object"""
    text: str = Field(
        description="The text to query"
    )

class ErrorInfo(BaseModel):
    """Class representing an error information"""
    code: int = Field(
        description="The error code that follows JSON-RPC 2.0"
    )
    message: str = Field(
        description="The error message that describe the details of an error"
    )

class SingleWordResultObject(BaseModel):
    """Class representing a single word result object"""
    furigana: str = Field(
        description="Furigana of given kana and kanji"
    )
    surface: str = Field(
        description="The (partial of) original query text"
    )

class MultiWordResultObject(SingleWordResultObject):
    """Class representing a multiple word result object"""
    subword: List[SingleWordResultObject] = Field(
        description="""A list contains more details when a \
        word contains both kanji and kana. Each elements in \
        subword is a dict with furigana and surface."""
    )

class Response(BaseModel):
    """Class representing a response object"""
    status: int = Field(
        default=200,
        description="Status code of response align with RFC 9110"
    )
    result: List[SingleWordResultObject | MultiWordResultObject] = Field(
        description="A list contains marked results"
    )
    error: Optional[ErrorInfo] = Field(
        default=None,
        description="An object that describe the details of an error when occur"
    )

router = APIRouter()

url = "https://jlp.yahooapis.jp/FuriganaService/V2/furigana"

try:
    if 'Yahoo_API_key' in os.environ:
        clientid = os.environ['Yahoo_API_key']
    else:
        with open("./secret.yaml", encoding="utf-8") as f:
            clientid = yaml.safe_load(f)['Yahoo_API_key']
except (FileNotFoundError, KeyError, yaml.YAMLError) as e:
    raise RuntimeError(f"Failed to load Yahoo_API_key: {e}")

@router.post("/MarkFurigana/", tags=["MarkFurigana"], response_model=Response)
async def mark_furigana(request: Request):
    """Receive POST request, return a JSON response"""
    query_text = request.text

    # 輸入
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Yahoo AppID: {clientid}"
    }

    data = {
        "id": "1234-1",
        "jsonrpc": "2.0",
        "method": "jlp.furiganaservice.furigana",
        "params": {
            "q": query_text,
            "grade": 1
        }
    }

    # call API
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status != 200:
                    return Response(
                        status=resp.status,
                        result=[],
                        error=ErrorInfo(code=resp.status, message=f"HTTP error: {resp.reason}")
                    ).model_dump()

                try:
                    result = await resp.json()
                except aiohttp.ContentTypeError:
                    text = await resp.text()
                    return Response(
                        status=502,
                        result=[],
                        error=ErrorInfo(code=502, message=f"Invalid JSON response: {text[:200]}")
                    ).model_dump()

    except asyncio.TimeoutError:
        return Response(
            status=504,
            result=[],
            error=ErrorInfo(code=504, message="Request to Yahoo API timed out"),
        ).model_dump()
    except aiohttp.ClientConnectionError:
        return Response(
            status=503,
            result=[],
            error=ErrorInfo(code=503, message="Failed to connect to Yahoo API"),
        ).model_dump()
    except Exception as e:
        return Response(
            status=500,
            result=[],
            error=ErrorInfo(code=500, message=f"Unexpected error: {str(e)}"),
        ).model_dump()

    if not isinstance(result, dict) or "result" not in result or "word" not in result["result"]:
        return Response(
            status=502,
            result=[],
            error=ErrorInfo(code=502, message="Unexpected response structure from Yahoo API")
        ).model_dump()

    # if return format error
    if "error" in result:
        err = result["error"]
        return Response(
            status=400,
            result=[],
            error=ErrorInfo(
                code=err.get("code"),
                message=err.get("message")
            )
        ).model_dump()
    
    # result output
    words = result["result"]["word"]
    parsed_result=[]

    for word in words:
        if "subword" in word:
            subword_list = [
                SingleWordResultObject(
                    furigana=sub["furigana"],
                    surface=sub["surface"]
                )
                for sub in word["subword"]
            ]
            parsed_result.append(MultiWordResultObject(
                surface=word["surface"],
                furigana=word["furigana"],
                subword=subword_list
            ))
        else:
            parsed_result.append(SingleWordResultObject(
                surface=word["surface"],
                furigana=word.get("furigana", word["surface"])
            ))

    return Response(
        status=200,
        result=parsed_result
    ).model_dump()

