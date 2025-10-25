# API-tools

> [!WARNING]
> 
> This project is still under development!

This is a repository that implement several API interfaces for Discord BOT and web interface.

Currently we have 2 implemented APIs
1. Mark Accent API
2. Mark Furigana API
3. Usage query API
4. Disctionary query API
5. Sentence Query API

> [!NOTE]
> 
> The following document is only for developer, we will use Swagger UI to generate official API document.

1. Mark Accent
    > Mark accent of given Japanese text

    - **Request URL**

        `https://{TODO}/api/MarkAccent/`
    - **Request Parameter (POST)**
        
        > Note that we only accept POST request

        |    Parameter    |  Type  |  Explanation |
        | --------------- | ------ | ------------ |
        | text (required) | string | The text to query |

        > Sample request

        ```json
        {
            "text": "お金を稼ぐ"
        }
        ```

    - **Respond Parameter**
        
        |    Parameter    |  Type  |  Explanation |
        | --------------- | ------ | ------------ |
        |      status     |  int   | status code [Reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status#server_error_responses) |
        | result  | object | An array contains marked results |
        | surface | string | The original input (partial) text |
        | accent | list | The accent of given word, with furigana and accent type info. 0 for no accent, 1 for plain, 2 for fall down |
        | subword | object | An array contains more details when a word contains both kanji and kana |
        | error | object | An object that describe the details of an error when occur |
        | error/code | integer | [Reference](https://www.jsonrpc.org/specification#error_object)
        | error/message | string | [Reference](https://www.jsonrpc.org/specification#error_object)

        > Sample response

        ```json
        {
            "status": 200,
            "result": [
                {
                    "furigana": "おかね",
                    "surface": "お金",
                    "accent": [
                        {
                            "furigana": "お",
                            "accent_marking_type": 0
                        },
                        {
                            "furigana": "か",
                            "accent_marking_type": 1
                        },
                        {
                            "furigana": "ね",
                            "accent_marking_type": 1
                        }
                    ],
                    "subword": [
                        {
                            "furigana": "お",
                            "surface": "お"
                        },
                        {
                            "furigana": "かね",
                            "surface": "金"
                        }
                    ]
                },
                {
                    "furigana": "を",
                    "surface": "を",
                    "accent": [
                        {
                            "furigana": "を",
                            "accent_marking_type": 1
                        }
                    ]
                },
                {
                    "furigana": "かせぐ",
                    "surface": "稼ぐ",
                    "accent": [
                        {
                            "furigana": "か",
                            "accent_marking_type": 1
                        },
                        {
                            "furigana": "せ",
                            "accent_marking_type": 2
                        },
                        {
                            "furigana": "ぐ",
                            "accent_marking_type": 0
                        }
                    ],
                    "subword": [
                        {
                            "furigana": "かせ",
                            "surface": "稼"
                        },
                        {
                            "furigana": "ぐ",
                            "surface": "ぐ"
                        }
                    ]
                }
            ],
            "error": null
        }
        ```

2. Mark Furigana
    > Mark furigana of given text

    - **Request URL**

        `https://{TODO}/api/MarkFurigana/`
    - **Request Parameter (POST)**
        
        > Note that we only accept POST request

        |    Parameter    |  Type  |  Explanation |
        | --------------- | ------ | ------------ |
        | text (required) | string | The text to query |

        > Sample request

        ```json
        {
            "text": "漢字かな交じり文"
        }
        ```
    
    - **Respond Parameter**

        |    Parameter    |  Type  |  Explanation |
        | --------------- | ------ | ------------ |
        |      status     |  int   | status code [Reference](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status#server_error_responses) |
        | result  | object | An array contains marked results |
        | surface | string | The original input (partial) text |
        | furigana | string | The marked furigana |
        | subword | object | An array contains more details when a word contains both kanji and kana |
        | error | object | An object that describe the details of an error when occur |
        | error/code | integer | [Reference](https://www.jsonrpc.org/specification#error_object)
        | error/message | string | [Reference](https://www.jsonrpc.org/specification#error_object)

        > Sample response

        ```json
        {
            "status": "200",
            "result": [
                {
                    "furigana": "かんじ",
                    "surface": "漢字"
                },
                {
                    "furigana": "かなまじり",
                    "subword": [
                    {
                        "furigana": "かな",
                        "surface": "かな"
                    },
                    {
                        "furigana": "ま",
                        "surface": "交"
                    },
                    {
                        "furigana": "じり",
                        "surface": "じり"
                    }
                    ],
                    "surface": "かな交じり"
                },
                {
                    "furigana": "ぶん",
                    "surface": "文"
                }
            ]
        }
        ```

## Build Environment

Download [uv](https://docs.astral.sh/uv/getting-started/installation/) and run this command:
```bash
uv sync
```

After build the environment, you should also obtain a Yahoo API Client ID from [Yahoo Japan website](https://developer.yahoo.co.jp/sitemap/).

Then add the API key in file `secret.yaml` with the following format

```yaml
Yahoo_API_key: <YOUR_YAHOO_API_CLIENT_ID>
```

## How to run?

```bash
uvicorn main:app --reload
```

To check the functionality, you may send POST request with curl as follows.

```bash
curl -X POST -H "Content-Type: application/json" -d "{\"text\": \"test\"}" 127.0.0.1:8000/api/MarkFurigana/
```

## How to use a shared `httpx.AsyncClient`?
If your router needs to send HTTP requests, you can follow the instructions below to use a shared `httpx.AsyncClient`to enhance performance.

```python
import httpx
from fastapi import APIRouter, Depends
from api.dependencies import get_http_client

router = APIRouter()

@router.post(
    "/Foo/", tags=["Foo"], response_model=FooResponse
)
async def foo(
    request: FooRequest, client: httpx.AsyncClient = Depends(get_http_client)
):
    try:
        response = await client.post(url)
    except httpx.TimeoutException:
        ...
    except httpx.HTTPError as e:
        ...
```