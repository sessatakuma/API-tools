"""
An API that mark accent of given query text
"""

import string
from typing import Optional, List, Any

import jaconv
import neologdn
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
    exception_symbols = ['\u30a0', '\u30fb', '\u30fc', '\u30fd', '\u30fe', '\u30ff']
    if char in exception_symbols:
        # '゠', '・', 'ー', 'ヽ', 'ヾ', 'ヿ' which should be regard as punchutation
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

class AccentInfo(BaseModel):
    """Class representing an accent information"""
    furigana: str = Field(
        description="The furigana of given kana and kanji"
    )
    accent_marking_type: int = Field(
        description="The type of accent, including none (0), heiban (1), fall (2)"
    )

class SingleWordAccentResultObject(BaseModel):
    """Class representing a single word result object"""
    furigana: str = Field(
        description="Furigana of given kana and kanji"
    )
    surface: str = Field(
        description="The (partial of) original query text"
    )
    accent: List[AccentInfo] = Field(
        description="The accent of givent word"
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

    paragraph = ""
    ojad_results = []
    for furigana, surface in zip(phrasing_texts, phrasing_subscripts):
        # Fetch subscript text (in the first span tag, final tag is the halt sign)
        phrase = surface.find_all("span", recursive=False)
        sentence = ""
        for p in phrase:
            sentence += p.get_text()
        paragraph += sentence

        # Fetch processed data
        mojis = furigana.find_all("span", recursive=False)
        for moji in mojis:
            # Check accent mark (we don't use unvoiced)
            accent = 0
            if moji['class'][0] == 'accent_plain':
                accent = 1
            elif moji['class'][0] == 'accent_top':
                accent = 2
            ojad_results.append({'text': moji.get_text(), 'accent': accent})
    return paragraph, ojad_results

@router.post("/MarkAccent/", tags=["MarkAccent"], response_model=Response)
def mark_accent(request: Request) -> dict[str, Any]:
    """Receive POST request, return a JSON response"""
    try:
        query_text = neologdn.normalize(request.text, tilde="normalize")
        furigana_results: List = mark_furigana(Request(text=query_text))['result']
        ojad_surface, ojad_results = get_ojad_result(query_text)

        # For debug use
        # print(furigana_results)
        # print('='*20)
        # print(ojad_results)

        final_response_results = []
        ojad_idx_cnt = 0
        for furigana_result in furigana_results:
            yahoo_furigana = furigana_result['furigana']
            yahoo_furigana_hira = jaconv.kata2hira(yahoo_furigana)
            yahoo_surface = furigana_result['surface']
            accents = []

            # If query sub-text contains non-kana and non-kanji words, we should ignore it
            # Including alphabet and punchutation and others
            # For punctuation marks, since Yahoo will hold the original query text
            # While OJAD may replace or remove the punctuation marks
            # Therefore we only reserve the punctuation marks from Yahoo
            if 'subword' not in furigana_result and \
                any(not is_kana_or_kanji(chr) for chr in yahoo_furigana):
                print(f"Successfully processing {yahoo_furigana} \t with {yahoo_furigana}")
                accent.append(AccentInfo(
                    furigana=yahoo_surface,
                    accent_marking_type=0
                ))
                final_response_results.append(
                    SingleWordAccentResultObject(
                        furigana=yahoo_furigana,
                        surface=yahoo_surface,
                        accent=accent
                    )
                )
                continue

            # Remove all mismatching prefix
            # Note that sometimes OJAD will transform katagana to hiragana, so make sure we're matching with same type
            ojad_idx = ojad_idx_cnt
            while ojad_idx < len(ojad_results) and \
                not yahoo_furigana_hira.startswith(jaconv.kata2hira(ojad_results[ojad_idx]['text'])):
                ojad_idx += 1

            # Match the furigana from Yahoo with OJAD results
            ojad_furigana = ""
            while len(ojad_furigana) < len(yahoo_furigana) and ojad_idx < len(ojad_results):
                ojad_text = ojad_results[ojad_idx]['text']
                ojad_furigana += ojad_text
                accents.append(ojad_results[ojad_idx]['accent'])
                ojad_idx += 1

            # If we successfully match the furigana from Yahoo with OJAD results
            if len(ojad_furigana) == len(yahoo_furigana) and jaconv.kata2hira(ojad_furigana) == jaconv.kata2hira(yahoo_furigana):
                print(f"Successfully processing {ojad_furigana} \t with {yahoo_furigana}")
                # Build accent info list
                accent_info_list = []
                for idx, accent_value in enumerate(accents):
                    accent_info_list.append(AccentInfo(
                        furigana=yahoo_furigana[idx],
                        accent_marking_type=accent_value
                    ))
                # Update ojad_idx_cnt
                ojad_idx_cnt = ojad_idx
                # Build final response result
                if 'subword' not in furigana_result:
                    final_response_results.append(
                        SingleWordAccentResultObject(
                            furigana=yahoo_furigana,
                            surface=yahoo_surface,
                            accent=accent_info_list
                        )
                    )
                else:
                    yahoo_subword = furigana_result['subword']
                    final_response_results.append(
                        MultiWordAccentResultObject(
                            furigana=yahoo_furigana,
                            surface=yahoo_surface,
                            accent=accent_info_list,
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
    except Exception as e:
        response = Response(
            status=500,
            result=[],
            error=ErrorInfo(
                code=500,
                message=f"Non-usual error occurs: {e}"
            )
        )
    return response