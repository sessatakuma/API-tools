"""Yahoo Furigana HTTP client (data layer).

Wraps the JSON-RPC call to `jlp.furiganaservice.furigana`, parses the
response into a `FuriganaResponse`, and surfaces transport / format
errors via the same envelope (status + ErrorInfo) the endpoint
contract uses. The route handler in `routes.py` is a thin wrapper.
"""

from __future__ import annotations

import httpx

from api.accent.models import ErrorInfo, FuriganaResponse, WordResult
from config.settings import YAHOO_API_KEY

YAHOO_FURIGANA_URL = "https://jlp.yahooapis.jp/FuriganaService/V2/furigana"


async def fetch_furigana(text: str, client: httpx.AsyncClient) -> FuriganaResponse:
    """POST `text` to Yahoo Furigana and return a parsed FuriganaResponse.

    Non-200 upstream / timeout / malformed-payload all return a
    Response with `result=None` and a populated `error`; callers just
    forward to the client.
    """
    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Yahoo AppID: {YAHOO_API_KEY}",
    }

    data = {
        "id": "1234-1",
        "jsonrpc": "2.0",
        "method": "jlp.furiganaservice.furigana",
        "params": {"q": text, "grade": 1},
    }

    try:
        response = await client.post(YAHOO_FURIGANA_URL, headers=headers, json=data)

    except httpx.TimeoutException:
        return FuriganaResponse(
            status=408,
            result=None,
            error=ErrorInfo(code=408, message="Yahoo API request timed out"),
        )

    except httpx.HTTPError as e:
        return FuriganaResponse(
            status=500,
            result=None,
            error=ErrorInfo(code=500, message=f"HTTP error: {str(e)}"),
        )

    if response.status_code != 200:
        return FuriganaResponse(
            status=response.status_code,
            result=None,
            error=ErrorInfo(
                code=response.status_code,
                message=f"Yahoo API request failed with status {response.status_code}",
            ),
        )

    result = response.json()
    if "result" not in result or "word" not in result["result"]:
        return FuriganaResponse(
            status=500,
            result=None,
            error=ErrorInfo(
                code=500, message="Unexpected response format from Yahoo API"
            ),
        )

    words = result["result"]["word"]
    parsed_result: list[WordResult] = []

    for word in words:
        if "subword" in word:
            subword_list = [
                WordResult(furigana=sub["furigana"], surface=sub["surface"])
                for sub in word["subword"]
            ]
            parsed_result.append(
                WordResult(
                    surface=word["surface"],
                    furigana=word["furigana"],
                    subword=subword_list,
                )
            )
        else:
            parsed_result.append(
                WordResult(
                    surface=word["surface"],
                    furigana=word.get("furigana", word["surface"]),
                )
            )

    return FuriganaResponse(status=200, result=parsed_result)
