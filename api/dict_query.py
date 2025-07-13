from typing import Optional

import requests
from fastapi import APIRouter
from pydantic import BaseModel, Field


class Request(BaseModel):
    """Class representing a request object"""

    word: str = Field(description="The word to query")


class ErrorInfo(BaseModel):
    """Class representing an error information"""

    code: int = Field(description="The error code that follows JSON-RPC 2.0")
    message: str = Field(description="The error message that describe the details of an error")


class Response(BaseModel):
    """Class representing a response object for word details"""

    status: int = Field(default=200, description="Status code of response align with RFC 9110")
    result: Optional[str] = Field(description="A list contains URLs for the headwords")
    error: Optional[ErrorInfo] = Field(
        default=None, description="An object that describe the details of an error when occur"
    )


router = APIRouter()


@router.post("/DictQuery/", tags=["DictionaryQuery"], response_model=Response)
def dict_query(request: Request):
    """Query weblio dictionary URL for the given word."""

    return Response(status=200, result=f"https://www.weblio.jp/content/{request.word}").model_dump()


# Test codes
if __name__ == "__main__":
    print(dict_query(Request(word="食べる")))
    print(dict_query(Request(word="タベル")))
    print(dict_query(Request(word="たべる")))
    print(dict_query(Request(word="taberu")))
