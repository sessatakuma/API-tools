"""
An API that mark accent of given query text
"""

import string
from typing import Optional, List, Any

import requests
from fastapi import APIRouter
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from api.furigana_marker import SingleWordResultObject, mark_furigana
from api.furigana_marker import Response as FuriganaResponse

from requests.exceptions import Timeout, TooManyRedirects, HTTPError, ConnectionError

tags_metadata = [
    {
        "name": "MarkAccent",
        "description": "Mark accent of given text",
    },
]


punctuation_marks = set(["。", "，", "、", "・", "——", "……", "—", "…", "「", "」", "『", "』", "（", "）", "—", "、、、", "、", "————", "—", "？", "！", ".", ",", "：", "；", "(", ")", "\"", "--", "-", "", "/", ":", ";", "！", "＂", "＃", "＄", "％", "＆", "＼", "’", "（", "）", "＊", "＋", "，", "－", "．", "／", "：", "；", "＜", "＝", "＞", "？", "＠", "［", "＼", "］", "︿", "＿", "‵", "｛", "｝", "｜", "～", "“", "”"]).union(set(string.punctuation))
skip_marks = set(string.ascii_lowercase + string.ascii_uppercase)

def clean_query(query):
    """For OJAD, the query text should without punctuations and alphabets for better result"""
    return ''.join(chr for chr in query if chr not in skip_marks)

def is_kana_or_kanji(char):
    """Check whether given character is kana or kanji (ignore half-width kana)"""
    if char == '\u30a0':
        # '゠', which should be regard as punchutation
        return False
    kana = range(0x3040, 0x30FF + 1)
    kanji = range(0x4E00, 0x9FFF + 1)
    if ord(char) in kana or ord(char) in kanji:
        return True
    return False

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

class SingleWordAccentResultObject(BaseModel):
    """Class representing a single word result object"""
    furigana: str = Field(
        description="Furigana of given kana and kanji"
    )
    surface: str = Field(
        description="The (partial of) original query text"
    )
    accent: int = Field(
        description="The accent of givent word, -1 when no accent"
    )

class MultiWordAccentResultObject(SingleWordAccentResultObject):
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
    result: List[SingleWordAccentResultObject | MultiWordAccentResultObject] = Field(
        description="A list contains marked results"
    )
    error: Optional[ErrorInfo] = Field(
        default=None,
        description="An object that describe the details of an error when occur"
    )

router = APIRouter()

def get_ojad_result(query_text: str) -> List:
    """Parse cleaned query_text to OJAD, concate whole result as a list"""

    # URL to suzukikun(すずきくん)
    url = "https://www.gavo.t.u-tokyo.ac.jp/ojad/phrasing/index"

    # Data of the POST method
    data = {
        "data[Phrasing][text]": query_text,
        "data[Phrasing][curve]": "advanced",
        "data[Phrasing][accent]": "advanced",
        "data[Phrasing][accent_mark]": "all",
        "data[Phrasing][estimation]": "crf",
        "data[Phrasing][analyze]": "true",
        "data[Phrasing][phrase_component]": "invisible",
        "data[Phrasing][param]": "invisible",
        "data[Phrasing][subscript]": "visible",
        "data[Phrasing][jeita]": "invisible"
    }

    # Send a POST and receive the website html code
    website = requests.post(url, data, timeout=(3, 5)).text

    # use Beautiful Soup to parse the received html file
    soup = BeautifulSoup(website, "html.parser")

    # Fetch the required tags, which are phrasing_text and phrasing_subscript
    phrasing_texts = soup.find_all("div", attrs={"class": "phrasing_text"})
    phrasing_subscripts = soup.find_all("div", attrs={"class": "phrasing_subscript"})

    ojad_results = []
    for d, s in zip(phrasing_texts, phrasing_subscripts):
        # Fetch subscript text (in the first span tag, final tag is the halt sign)
        phrase = s.find_all("span", recursive= False)
        sentence = ""
        for p in phrase:
            sentence += p.get_text()

        # Fetch processed data
        temp = d.find_all("span", recursive= False)
        for p in temp:
            # Check accent mark (we don't use unvoiced)
            accent = -1
            if p['class'][0] == 'accent_plain':
                accent = 0
            elif p['class'][0] == 'accent_top':
                accent = 1
            ojad_results.append({'text': p.get_text(), 'accent': accent})
    return ojad_results

@router.post("/MarkAccent/", tags=["MarkAccent"], response_model=Response)
def mark_accent(request: Request) -> dict[str, Any]:
    """Receive POST request, return a JSON response"""
    try:
        query_text = request.text
        furigana_results: List = FuriganaResponse(**mark_furigana(Request(text=query_text))).result
        ojad_results = get_ojad_result(query_text)

        final_response_results = []
        ojad_idx_cnt = 0
        for furigana_result in furigana_results:
            yahoo_furigana = furigana_result.furigana
            yahoo_surface = furigana_result.surface
            accent = -1

            # If query sub-text contains non-kana and non-kanji words, we should ignore it
            # Including alphabet and punchutation and others
            # For punctuation marks, since Yahoo will hold the original query text
            # While OJAD may replace or remove the punctuation marks
            # Therefore we only reserve the punctuation marks from Yahoo
            if isinstance(furigana_result, SingleWordResultObject) and \
                any(not is_kana_or_kanji(chr) for chr in yahoo_furigana):
                final_response_results.append(
                    SingleWordAccentResultObject(
                        furigana=yahoo_furigana,
                        surface=yahoo_surface,
                        accent=accent
                    )
                )
                continue

            # Remove all mismatching prefix
            tmp_ojad_idx = ojad_idx_cnt
            while tmp_ojad_idx < len(ojad_results) and \
                not yahoo_furigana.startswith(ojad_results[tmp_ojad_idx]['text']):
                tmp_ojad_idx += 1

            ojad_moji_count = 0
            ojad_furigana = ""
            has_zero_accent = has_one_accent = False

            while ojad_moji_count < len(yahoo_furigana) and tmp_ojad_idx < len(ojad_results):
                ojad_text = ojad_results[tmp_ojad_idx]['text']
                ojad_moji_count += len(ojad_text)
                ojad_furigana += ojad_text

                accent_value = ojad_results[tmp_ojad_idx]['accent']
                if accent_value == 0:
                    has_zero_accent = True
                elif accent_value == 1:
                    has_one_accent = True
                    accent = tmp_ojad_idx - ojad_idx_cnt + 1

                tmp_ojad_idx += 1

            if has_zero_accent and not has_one_accent:
                accent = 0

            if ojad_moji_count == len(yahoo_furigana) and ojad_furigana == yahoo_furigana:
                print(f"Successfully processing {ojad_furigana} \t with {yahoo_furigana}")
                ojad_idx_cnt = tmp_ojad_idx
                if isinstance(furigana_result, SingleWordResultObject):
                    final_response_results.append(
                        SingleWordAccentResultObject(
                            furigana=yahoo_furigana,
                            surface=yahoo_surface,
                            accent=accent
                        )
                    )
                else:
                    yahoo_subword = furigana_result.subword
                    final_response_results.append(
                        MultiWordAccentResultObject(
                            furigana=yahoo_furigana,
                            surface=yahoo_surface,
                            accent=accent,
                            subword=yahoo_subword
                        )
                    )
            else:
                # [TODO] Do our best to give correct result
                # If we cannot make it, we should return some error message
                print(f"[ERROR] Some error occured when processing {ojad_furigana} \t with {yahoo_furigana}")

        response = Response(
            status=200,
            result=final_response_results
        )
    except Timeout as time_err:
        response = Response(
            status=504,
            result=[],
            error=ErrorInfo(
                code=504,
                message=f"Request Timeout: {time_err}"
            )
        )
    except TooManyRedirects as redirect_err:
        response = Response(
            status=500,
            result=[],
            error=ErrorInfo(
                code=500,
                message=f"Too many redirects: {redirect_err}"
            )
        )
    except HTTPError as http_err:
        response = Response(
            status=int(http_err.response.status_code),
            result=[],
            error=ErrorInfo(
                code=int(http_err.response.status_code),
                message=str(http_err)
            )
        )
    except ConnectionError as conn_err:
        response = Response(
            status=500,
            result=[],
            error=ErrorInfo(
                code=500,
                message=f"Connection error: {conn_err}"
            )
        )
    return response.model_dump()
