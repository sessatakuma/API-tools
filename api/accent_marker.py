"""
An API that mark accent of given query text
"""

import asyncio
import json
import logging
import re
import string
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx
import jaconv
import neologdn
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.dependencies import get_http_client
from api.reading_overrides import (
    apply_accent_overrides,
    apply_furigana_overrides,
)
from config.settings import YAHOO_API_KEY

logger = logging.getLogger("api")

tags_metadata = [
    {
        "name": "MarkAccent",
        "description": "Mark accent of given text",
    },
]

punctuation_marks = set(
    [
        "。",
        "，",
        "、",
        "・",
        "——",
        "……",
        "—",
        "…",
        "「",
        "」",
        "『",
        "』",
        "（",
        "）",
        "—",
        "、、、",
        "、",
        "————",
        "—",
        "？",
        "！",
        ".",
        ",",
        "：",
        "；",
        "(",
        ")",
        '"',
        "--",
        "-",
        "",
        "/",
        ":",
        ";",
        "！",
        "＂",
        "＃",
        "＄",
        "％",
        "＆",
        "＼",
        "’",
        "（",
        "）",
        "＊",
        "＋",
        "，",
        "－",
        "．",
        "／",
        "：",
        "；",
        "＜",
        "＝",
        "＞",
        "？",
        "＠",
        "［",
        "＼",
        "］",
        "︿",
        "＿",
        "‵",
        "｛",
        "｝",
        "｜",
        "～",
        "“",
        "”",
    ]
).union(set(string.punctuation))
skip_marks = set(string.ascii_lowercase + string.ascii_uppercase)


def clean_query(query: str) -> str:
    """
    For OJAD, the query text should without punctuations and alphabets for better
    result
    """
    return "".join(chr for chr in query if chr not in skip_marks)


def is_kana_or_kanji(char: Any) -> bool:
    """Check whether given character is kana or kanji (ignore half-width kana)"""
    exception_symbols = ["\u30a0", "\u30fb", "\u30fc", "\u30fd", "\u30fe", "\u30ff"]
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

    text: str = Field(description="The text to query")


class ErrorInfo(BaseModel):
    """Class representing an error information"""

    code: int = Field(description="The error code that follows JSON-RPC 2.0")
    message: str = Field(
        description="The error message that describe the details of an error"
    )


class WordResult(BaseModel):
    """Class representing a single word from Yahoo's furigana service.

    Defined here (and not in a separate module) because the only consumer of
    the raw Yahoo tokenisation is the accent pipeline below — there is no
    public furigana endpoint.
    """

    furigana: str = Field(description="Furigana of given kana and kanji")
    surface: str = Field(description="The (partial of) original query text")
    subword: list["WordResult"] = Field(
        default_factory=list,
        description=(
            "A list contains more details when a word contains both kanji "
            "and kana. Each elements in subword follow the same schema as "
            "this parent object (containing `furigana`, `surface`, and "
            "`subword`)."
        ),
    )


class AccentInfo(BaseModel):
    """Class representing an accent information"""

    furigana: str = Field(description="The furigana of given kana and kanji")
    accent_marking_type: int = Field(
        description="The type of accent, including none (0), heiban (1), fall (2)"
    )
    length: int = Field(description="Length of the furigana")


class WordAccentResult(BaseModel):
    """Class representing a single word result object"""

    furigana: str = Field(description="Furigana of given kana and kanji")
    surface: str = Field(description="The (partial of) original query text")
    accent: list[AccentInfo] = Field(description="The accent of givent word")
    subword: list[WordResult] = Field(
        default_factory=list,
        description="""A list contains more details when a \
        word contains both kanji and kana.""",
    )


class Response(BaseModel):
    """Class representing a response object"""

    status: int = Field(
        default=200, description="Status code of response align with RFC 9110"
    )
    result: list[WordAccentResult] | None = Field(
        description="A list contains marked results"
    )
    error: ErrorInfo | None = Field(
        default=None,
        description="An object that describe the details of an error when occur",
    )


router = APIRouter()


_YAHOO_FURIGANA_URL = "https://jlp.yahooapis.jp/FuriganaService/V2/furigana"


@dataclass(frozen=True)
class _YahooFetchError:
    """Sentinel returned by `_fetch_yahoo_raw` on failure paths.

    Carries a status code + ErrorInfo the caller can lift directly into a
    `Response`. Modelled as a return type (rather than an exception) so the
    outer `try/except Exception` in `_process_accent_chunk` can't swallow it
    and turn a 408 into an opaque 500.
    """

    status: int
    error: ErrorInfo


async def _fetch_yahoo_raw(
    text: str, client: httpx.AsyncClient
) -> list[WordResult] | _YahooFetchError:
    """Call Yahoo Furigana API and parse the response into WordResult tokens.

    Returns the token list on success, or a `_YahooFetchError` on any failure
    path. The accent pipeline applies `apply_furigana_overrides` to the raw
    tokens before alignment (see `_process_accent_chunk`).
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
        response = await client.post(
            _YAHOO_FURIGANA_URL, headers=headers, json=data
        )
    except httpx.TimeoutException:
        return _YahooFetchError(
            status=408,
            error=ErrorInfo(code=408, message="Yahoo API request timed out"),
        )
    except httpx.HTTPError as e:
        return _YahooFetchError(
            status=500,
            error=ErrorInfo(code=500, message=f"HTTP error: {str(e)}"),
        )

    if response.status_code != 200:
        return _YahooFetchError(
            status=response.status_code,
            error=ErrorInfo(
                code=response.status_code,
                message=(
                    f"Yahoo API request failed with status "
                    f"{response.status_code}"
                ),
            ),
        )

    payload = response.json()
    if "result" not in payload or "word" not in payload["result"]:
        return _YahooFetchError(
            status=500,
            error=ErrorInfo(
                code=500, message="Unexpected response format from Yahoo API"
            ),
        )

    parsed: list[WordResult] = []
    for word in payload["result"]["word"]:
        if "subword" in word:
            subword_list = [
                WordResult(furigana=sub["furigana"], surface=sub["surface"])
                for sub in word["subword"]
            ]
            parsed.append(
                WordResult(
                    surface=word["surface"],
                    furigana=word["furigana"],
                    subword=subword_list,
                )
            )
        else:
            parsed.append(
                WordResult(
                    surface=word["surface"],
                    furigana=word.get("furigana", word["surface"]),
                )
            )
    return parsed


async def get_ojad_result(
    query_text: str,
    client: httpx.AsyncClient,
) -> tuple[str, list[dict[str, Any]]]:
    """Parse cleaned query_text to OJAD, concate whole result as a list"""
    logger.debug(f"[OJAD] Start fetching for: {query_text}")

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
        "data[Phrasing][jeita]": "invisible",
    }

    # Send a POST and receive the website html code
    try:
        response = await client.post(url, data=data)
        response.raise_for_status()
        logger.debug(f"[OJAD] Status Code: {response.status_code}")
    except Exception as e:
        logger.error(f"[OJAD] Request Failed: {e}")
        raise e

    website = response.text

    # use Beautiful Soup to parse the received html file
    soup = BeautifulSoup(website, "html.parser")

    # Fetch the required tags
    phrasing_texts = soup.find_all("div", attrs={"class": "phrasing_text"})
    phrasing_subscripts = soup.find_all("div", attrs={"class": "phrasing_subscript"})

    paragraph = ""
    ojad_results = []

    if not phrasing_texts:
        logger.warning("[OJAD] Warning: No phrasing_texts found in HTML!")

    for furigana, surface in zip(phrasing_texts, phrasing_subscripts):
        # Fetch subscript text
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
            if moji["class"][0] == "accent_plain":
                accent = 1
            elif moji["class"][0] == "accent_top":
                accent = 2
            ojad_results.append({"text": moji.get_text(), "accent": accent})

    return paragraph, ojad_results


# accept negative integers and decimals
numeric_pattern = re.compile(r"^-?\d+(\.\d+)?$")


# Yahoo returns "dictionary form" furigana (no rendaku), while OJAD returns the
# actually pronounced kana (with rendaku/sequential-voicing applied). When
# Yahoo says "ふんかん" and OJAD says "ぷんかん", the literal startswith /
# equality checks below would never match and the alignment would cascade-fail.
# We compare under a normalisation that folds each voiced/half-voiced kana to
# its voiceless base, so ぷ↔ふ, ば↔は, ご↔こ etc. all alias together.
_VOICING_FOLD: dict[str, str] = {
    "が": "か", "ぎ": "き", "ぐ": "く", "げ": "け", "ご": "こ",
    "ざ": "さ", "じ": "し", "ず": "す", "ぜ": "せ", "ぞ": "そ",
    "だ": "た", "ぢ": "ち", "づ": "つ", "で": "て", "ど": "と",
    "ば": "は", "び": "ひ", "ぶ": "ふ", "べ": "へ", "ぼ": "ほ",
    "ぱ": "は", "ぴ": "ひ", "ぷ": "ふ", "ぺ": "へ", "ぽ": "ほ",
}


def _norm(s: str) -> str:
    """Kata→hira plus voicing fold for rendaku-tolerant alignment."""
    hira = jaconv.kata2hira(s)
    return "".join(_VOICING_FOLD.get(c, c) for c in hira)


# --- DP aligner ----------------------------------------------------------------
#
# The greedy aligner this replaced had two fatal failure modes: a numeric
# anchor that over-consumed when Yahoo and OJAD disagreed on a phrase
# boundary, and a fallback path that advanced OJAD by exactly +1 — so a
# single mismatch cascaded into type-0 fallback for every downstream token.
#
# Instead we now build a Needleman-Wunsch-style DP over (yahoo_token,
# ojad_entry) pairs. Each cell dp[i][j] holds the minimum total cost to
# explain Yahoo tokens [0..i) using OJAD entries [0..j). For every (i, j)
# we try consuming k OJAD entries for token i with k ∈ [0, K_MAX]; the
# per-token cost depends on token shape (punctuation / numeric / kana) and
# uses edit distance over rendaku-folded strings for kana tokens. A bad
# token costs O(1); downstream tokens stay aligned.

_K_MAX = 16  # max OJAD entries one Yahoo token can consume
_INF = float("inf")
_FALLBACK_COST = 3.0  # cost of giving up on a single token (k=0 for kana/numeric)
_OJAD_PUNCT_TEXTS = {"、", "。", ",", ".", "?", "!", "！", "？"}


# Substitutions are cheaper than insertions/deletions: a substitution keeps
# the yahoo↔ojad mora-count alignment intact (the kind of mismatch we
# *expect* — rendaku, reading variants like 等→とう/など), while ins/del
# means the two sources disagree on mora count, which is much less common
# and almost always a worse alignment. With sub<0.5, the DP correctly
# prefers a same-length span with two substitutions (cost 0.8) over a
# shorter span with one deletion (cost 1.0). This breaks the tie that was
# letting OJAD's `う` from `等→とう` leak forward onto the next token.
_SUB_COST = 0.4


def _edit_distance(a: str, b: str) -> float:
    """Weighted Levenshtein with sub_cost=0.4, ins/del=1.0.
    Used over rendaku-folded strings only."""
    if a == b:
        return 0.0
    if not a:
        return float(len(b))
    if not b:
        return float(len(a))
    prev: list[float] = [float(j) for j in range(len(b) + 1)]
    for i, ca in enumerate(a, 1):
        curr: list[float] = [float(i)] + [0.0] * len(b)
        for j, cb in enumerate(b, 1):
            if ca == cb:
                curr[j] = prev[j - 1]
            else:
                curr[j] = min(
                    prev[j - 1] + _SUB_COST,  # substitute
                    prev[j] + 1.0,            # delete from a
                    curr[j - 1] + 1.0,        # insert into a
                )
        prev = curr
    return prev[-1]


def _is_punct_token(furigana: str, is_numeric: bool) -> bool:
    if is_numeric or not furigana:
        return False
    return all(not is_kana_or_kanji(c) for c in furigana)


def _match_cost(
    token: Any, span_texts: list[str], is_numeric: bool, is_punct: bool
) -> float:
    """Cost of letting `token` consume the given OJAD-text span."""
    k = len(span_texts)
    concat = "".join(span_texts)

    if is_punct:
        if k == 0:
            return 0.0
        if k == 1:
            stripped = span_texts[0].strip()
            yahoo_stripped = token.furigana.strip()
            # Free-consume only if the OJAD entry actually matches this
            # token's punct (or is empty). Without this, two adjacent
            # punct tokens (e.g. "。" then "\n") could both consume the
            # single OJAD "。" at zero cost and DP would arbitrarily give
            # it to the wrong one.
            if not stripped:
                return 0.0
            if stripped == yahoo_stripped:
                return 0.0
        return _INF

    if is_numeric:
        if k == 0:
            return _FALLBACK_COST
        # Numerics have no Yahoo furigana to compare against. Accept any
        # reasonable count of morae; only penalise blatantly over-long spans.
        upper = max(4, len(token.surface) * 4)
        return 0.0 if k <= upper else float(k - upper)

    # Kana / kanji token: compare under rendaku fold.
    if k == 0:
        return _FALLBACK_COST
    y_norm = _norm(token.furigana)
    o_norm = _norm(concat)
    # Cheap length pre-filter — keeps the DP fast and prevents pathological
    # "consume 12 OJAD entries to match a 2-mora Yahoo token" alignments.
    if abs(len(y_norm) - len(o_norm)) > 3:
        return _INF
    return _edit_distance(y_norm, o_norm)


def _build_word_result(token: Any, ojad_span: list[dict[str, Any]]) -> WordAccentResult:
    """Wrap an aligned (token, OJAD-span) pair into a WordAccentResult."""
    yahoo_surface = token.surface
    yahoo_furigana = token.furigana
    is_numeric = bool(numeric_pattern.match(yahoo_surface))
    subword = (
        [WordResult(furigana=s.furigana, surface=s.surface) for s in token.subword]
        if token.subword
        else []
    )

    if not ojad_span:
        # k=0 path: emit type-0 fallback so the downstream override pass and
        # callers still see one AccentInfo per token.
        return WordAccentResult(
            surface=yahoo_surface,
            furigana=yahoo_furigana,
            accent=[
                AccentInfo(
                    furigana=yahoo_furigana,
                    accent_marking_type=0,
                    length=len(yahoo_furigana),
                )
            ],
            subword=subword,
        )

    # Drop OJAD entries with empty text (phrase-boundary sentinels). They
    # carry no audible mora and would surface as a stray "(…||0)" row.
    voiced_span = [e for e in ojad_span if e["text"]]
    if not voiced_span:
        return WordAccentResult(
            surface=yahoo_surface,
            furigana=yahoo_furigana,
            accent=[
                AccentInfo(
                    furigana=yahoo_furigana,
                    accent_marking_type=0,
                    length=len(yahoo_furigana),
                )
            ],
            subword=subword,
        )
    accents = [
        AccentInfo(
            furigana=e["text"],
            accent_marking_type=e["accent"],
            length=len(e["text"]),
        )
        for e in voiced_span
    ]
    # Numerics had no Yahoo furigana to begin with — surface the OJAD reading.
    display = (
        "".join(e["text"] for e in voiced_span) if is_numeric else yahoo_furigana
    )
    return WordAccentResult(
        surface=yahoo_surface,
        furigana=display,
        accent=accents,
        subword=subword,
    )


def _fallback_word(token: Any) -> WordAccentResult:
    return _build_word_result(token, [])


async def align_accent(
    furigana_results: list[Any], ojad_results: list[dict[str, Any]]
) -> list[WordAccentResult]:
    """Align yahoo furigana with OJAD per-moji entries via global DP.

    Returns one WordAccentResult per Yahoo token. Each token consumes a
    (possibly empty) contiguous span of OJAD entries; the assignment that
    minimises total mismatch cost wins.
    """
    n = len(furigana_results)
    m = len(ojad_results)

    if n == 0:
        return []
    if m == 0:
        return [_fallback_word(t) for t in furigana_results]

    # Pre-compute per-token classification and OJAD texts.
    token_kinds: list[tuple[bool, bool]] = []
    for t in furigana_results:
        is_num = bool(numeric_pattern.match(t.surface))
        is_pct = _is_punct_token(t.furigana, is_num)
        token_kinds.append((is_num, is_pct))
    ojad_texts = [e["text"] for e in ojad_results]

    # dp[i][j] = best cost aligning tokens [0..i) to ojad entries [0..j).
    dp: list[list[float]] = [[_INF] * (m + 1) for _ in range(n + 1)]
    back: list[list[int]] = [[-1] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = 0.0

    for i in range(n):
        token = furigana_results[i]
        is_num, is_pct = token_kinds[i]
        for j in range(m + 1):
            base = dp[i][j]
            if base == _INF:
                continue
            k_limit = min(_K_MAX, m - j)
            for k in range(0, k_limit + 1):
                cost = _match_cost(token, ojad_texts[j : j + k], is_num, is_pct)
                if cost == _INF:
                    continue
                new_cost = base + cost
                if new_cost < dp[i + 1][j + k]:
                    dp[i + 1][j + k] = new_cost
                    back[i + 1][j + k] = j

    # Pick the best terminal state. Prefer fully consuming OJAD; otherwise
    # take the cheapest end (trailing empty entries get inherited for free
    # by the previous token's span since their text contributes nothing to
    # edit distance).
    best_j = m
    best_cost = dp[n][m]
    if best_cost == _INF:
        for j in range(m + 1):
            if dp[n][j] < best_cost:
                best_cost = dp[n][j]
                best_j = j

    if best_cost == _INF:
        logger.error(
            "DP alignment found no valid path (n=%d, m=%d); falling back per token.",
            n,
            m,
        )
        return [_fallback_word(t) for t in furigana_results]

    # Backtrack to recover the OJAD span each token consumed.
    spans: list[tuple[int, int]] = [(0, 0)] * n
    cur_j = best_j
    for i in range(n, 0, -1):
        prev_j = back[i][cur_j]
        if prev_j < 0:
            logger.error("DP backtrace broken at i=%d, j=%d", i, cur_j)
            return [_fallback_word(t) for t in furigana_results]
        spans[i - 1] = (prev_j, cur_j)
        cur_j = prev_j

    logger.debug("DP alignment cost=%.2f spans=%s", best_cost, spans)
    return [
        _build_word_result(furigana_results[i], ojad_results[s:e])
        for i, (s, e) in enumerate(spans)
    ]


# Hiragana / katakana / CJK Unified Ideographs (incl. Extension A). A
# chunk with no chars in this set is treated as pure English / code /
# markdown / URL — pipeline is skipped entirely and the line is echoed
# back verbatim so document reconstruction still works.
_CJK_RE = re.compile(
    "["
    "぀-ゟ"  # Hiragana
    "゠-ヿ"  # Katakana
    "㐀-䶿"  # CJK Unified Ideographs Extension A
    "一-鿿"  # CJK Unified Ideographs
    "]"
)

# Sentence terminators that close a Japanese clause: kuten (。), full-width
# question (？), full-width exclamation (！), and full-width period (．).
# ASCII `.!?` are intentionally excluded — they appear in abbreviations,
# decimals, and code/identifier fragments that we don't want to split on.
# A zero-width split (lookbehind) keeps the terminator attached to the
# preceding sentence so accent prediction still sees the clause boundary.
_SENTENCE_SPLIT_RE = re.compile("(?<=[。！？．])")


def _split_sentences(line: str) -> list[str]:
    """Split a line into sentence-sized chunks for parallel processing.

    OJAD's phrasing module degrades badly on long inputs (a single
    misaligned mora can cascade across the whole paragraph), and the
    streaming endpoint can't parallelise within a `\\n`-delimited chunk.
    Splitting on full-width sentence terminators fixes both: each sentence
    is short enough for OJAD to handle reliably, and they fan out across
    the in-flight Semaphore.
    """
    return [s for s in _SENTENCE_SPLIT_RE.split(line) if s.strip()]

# URLs are stripped before the pipeline runs. OJAD's phrasing scraper
# produces only noise for Latin punctuation runs, and Yahoo's tokenizer
# can fragment a URL across several alphabet/symbol tokens — both drag
# the alignment DP off-rail for the surrounding Japanese. We swap each
# URL for one fixed-string placeholder (which Yahoo keeps as a single
# "alphabet" word), run the pipeline, then walk the result and restore
# the originals in order.
# URL body stops at whitespace, any Japanese char (so `…はhttps://x.jp/aです`
# strips just the URL, leaving `です` to be processed), or common quoting
# punctuation `,()<>[]"'` (so `(https://x.jp)` strips just the URL).
_URL_RE = re.compile(r"https?://[^\s　-鿿,()<>\[\]\"']+")
_URL_PLACEHOLDER = "URLPLACEHOLDER"


def _has_japanese(text: str) -> bool:
    """True if `text` contains any hiragana, katakana, or CJK ideograph."""
    return bool(_CJK_RE.search(text))


def _strip_urls(text: str) -> tuple[str, list[str]]:
    """Replace each URL with `_URL_PLACEHOLDER`, returning URLs in order."""
    urls: list[str] = []

    def repl(m: "re.Match[str]") -> str:
        urls.append(m.group(0))
        return _URL_PLACEHOLDER

    return _URL_RE.sub(repl, text), urls


def _restore_urls(
    result: list[WordAccentResult], urls: list[str]
) -> list[WordAccentResult]:
    """Swap placeholder tokens in `result` back to their original URLs."""
    if not urls:
        return result
    it = iter(urls)
    out: list[WordAccentResult] = []
    for w in result:
        if w.surface == _URL_PLACEHOLDER:
            url = next(it, None)
            if url is None:
                # Placeholder count exceeded URL count: leave the token
                # untouched. Indicates a Yahoo tokenization surprise; the
                # output is still readable.
                out.append(w)
                continue
            out.append(
                WordAccentResult(
                    surface=url, furigana=url, accent=[], subword=[]
                )
            )
        else:
            out.append(w)
    return out


async def _process_accent_chunk(
    text: str, client: httpx.AsyncClient
) -> Response:
    """Run the full accent pipeline on a single chunk of text.

    Shared by `/api/MarkAccent/` (whole input as one chunk) and
    `/api/MarkAccent/stream/` (one call per `\\n`-split paragraph).
    """
    try:
        query_text = neologdn.normalize(text, tilde="normalize")

        # Strip URLs first so a pure-URL line is detected as non-Japanese
        # by the language check below and short-circuits the pipeline.
        stripped_text, urls = _strip_urls(query_text)

        # No hiragana/katakana/kanji outside URLs — passthrough the line
        # as a single token. Callers reconstructing the document still
        # see the chunk in the stream; we just skip the Yahoo + OJAD
        # round-trips entirely.
        if not _has_japanese(stripped_text):
            return Response(
                status=200,
                result=[
                    WordAccentResult(
                        surface=query_text,
                        furigana=query_text,
                        accent=[],
                        subword=[],
                    )
                ],
                error=None,
            )

        # Apply furigana overrides BEFORE alignment: many of the overrides
        # (e.g. "4日"→"よっか", "27日"→"にじゅうしちにち") merge a numeric
        # surface with the counter into one token whose furigana matches what
        # OJAD reads as a single phrase. align_accent's numeric-anchor logic
        # otherwise cascades-fails on these inputs because numeric tokens lack
        # any Yahoo furigana for OJAD to align against.
        raw = await _fetch_yahoo_raw(stripped_text, client)
        if isinstance(raw, _YahooFetchError):
            logger.warning(f"Yahoo fetch failed: {raw.error.message}")
            return Response(status=raw.status, result=None, error=raw.error)
        furigana_results = apply_furigana_overrides(raw)
        if not furigana_results:
            logger.warning("Yahoo returned empty token list")
            return Response(
                status=500,
                result=None,
                error=ErrorInfo(
                    code=500, message="Yahoo returned empty token list"
                ),
            )
        logger.debug(f"Yahoo Results Count: {len(furigana_results)}")

        _ojad_surface, ojad_results = await get_ojad_result(stripped_text, client)

        final_results = await align_accent(furigana_results, ojad_results)
        final_results = apply_accent_overrides(final_results)
        final_results = _restore_urls(final_results, urls)

        return Response(status=200, result=final_results)

    except Exception as e:
        logger.exception(f"Unexpected error occurred: {text}")
        # Some httpx exceptions (PoolTimeout, ReadTimeout) have empty
        # str(); fall back to the type name so the client sees something.
        detail = str(e) or repr(e) or type(e).__name__
        return Response(
            status=500,
            result=None,
            error=ErrorInfo(code=500, message=f"Error: {detail}"),
        )


@router.post("/MarkAccent/", tags=["MarkAccent"], response_model=Response)
async def mark_accent(
    request: Request, client: httpx.AsyncClient = Depends(get_http_client)
) -> Response:
    """Receive POST request, return a Response object."""
    logger.info(f"[API] Received Request Text: {request.text}")
    return await _process_accent_chunk(request.text, client)


@router.post("/MarkAccent/stream/", tags=["MarkAccent"])
async def mark_accent_stream(
    request: Request,
    client: httpx.AsyncClient = Depends(get_http_client),
) -> StreamingResponse:
    """Split the input on `\\n` (line) and then on full-width sentence
    terminators (sub-chunk), process each piece in parallel under a bounded
    semaphore, and stream one NDJSON line per piece in input order.

    Each emitted object carries `{"chunk": line_idx, "subchunk": sub_idx}`:
    `line_idx` is the original `\\n`-split index (blank lines preserve their
    position so a client knows position 2 was empty); `sub_idx` distinguishes
    sentences inside one line. A line with no terminator yields one
    subchunk with `sub_idx=0`.
    """
    logger.info(f"[API] Received streaming request: {request.text!r}")

    # (line_idx, sub_idx, text). Long paragraphs are split into sentence-
    # sized chunks because OJAD's phrasing predictor degrades on long
    # inputs and a single misalignment used to cascade across the whole
    # paragraph. Splitting also fans the work out under the semaphore.
    chunks: list[tuple[int, int, str]] = []
    for line_idx, line in enumerate(request.text.split("\n")):
        if not line.strip():
            continue
        for sub_idx, sentence in enumerate(_split_sentences(line)):
            chunks.append((line_idx, sub_idx, sentence))

    # OJAD's u-tokyo backend and (to a lesser extent) Yahoo's furigana API
    # both fall over when hit with 30+ parallel scrapes — the symptom was
    # most chunks of a long document returning empty-string httpx errors.
    # Cap in-flight work so well-behaved inputs still parallelise (a 4-chunk
    # paragraph fans out fully) without hammering the upstream services.
    semaphore = asyncio.Semaphore(4)

    async def run_chunk(line: str) -> Response:
        async with semaphore:
            return await _process_accent_chunk(line, client)

    async def generate() -> AsyncIterator[bytes]:
        if not chunks:
            return
        tasks = [asyncio.create_task(run_chunk(text)) for _, _, text in chunks]
        # Yield in input order so the client renders chunks monotonically.
        for (chunk_idx, sub_idx, _text), task in zip(chunks, tasks):
            try:
                resp = await task
                payload: dict[str, Any] = {
                    "chunk": chunk_idx,
                    "subchunk": sub_idx,
                    **resp.model_dump(),
                }
            except Exception as exc:
                logger.exception(
                    f"Streaming chunk {chunk_idx}.{sub_idx} failed"
                )
                detail = str(exc) or repr(exc) or type(exc).__name__
                payload = {
                    "chunk": chunk_idx,
                    "subchunk": sub_idx,
                    "status": 500,
                    "result": None,
                    "error": {"code": 500, "message": f"Error: {detail}"},
                }
            yield (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")

    return StreamingResponse(generate(), media_type="application/x-ndjson")
