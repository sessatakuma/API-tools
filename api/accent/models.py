"""Pydantic schemas shared by the MarkAccent and MarkFurigana endpoints.

Both endpoints accept the same `Request` (just a single `text` field) and
return the same `{status, result, error}` envelope. They only differ in the
shape of `result` — furigana returns `list[WordResult]`, accent returns
`list[WordAccentResult]` — so we keep two separate Response classes
(`FuriganaResponse`, `AccentResponse`) for explicit FastAPI `response_model`
typing.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Request(BaseModel):
    """Request body for both /MarkAccent/ and /MarkFurigana/."""

    text: str = Field(description="The text to query")


class ErrorInfo(BaseModel):
    """Error payload returned in the Response envelope when something
    upstream fails (Yahoo / OJAD request errors, parse errors, etc.).
    """

    code: int = Field(description="The error code that follows JSON-RPC 2.0")
    message: str = Field(
        description="The error message that describe the details of an error"
    )


class WordResult(BaseModel):
    """A single Yahoo Furigana word entry.

    `subword` is populated when Yahoo splits a token containing both kanji
    and kana (e.g. `食べる` → `食 / べる`); each subword follows the same
    schema recursively.
    """

    furigana: str = Field(description="Furigana of given kana and kanji")
    surface: str = Field(description="The (partial of) original query text")
    subword: list[WordResult] = Field(
        default_factory=list,
        description="A list contains more details when a word contains "
        "both kanji and kana. Each elements in subword follow the same "
        "schema as this parent object (containing `furigana`, `surface`, "
        "and `subword`).",
    )


class AccentInfo(BaseModel):
    """Per-mora pitch annotation produced by OJAD's phrasing module.

    `accent_marking_type` semantics (see also `api/accent/README.md`):
      - 0 = LOW (or unknown / fallback)
      - 1 = HIGH plateau (OJAD's `accent_plain` class)
      - 2 = FALL kernel (pitch drops after this mora; OJAD's `accent_top`)
    """

    furigana: str = Field(description="The furigana of given kana and kanji")
    accent_marking_type: int = Field(
        description="The type of accent, including none (0), heiban (1), fall (2)"
    )
    length: int = Field(description="Length of the furigana")


class WordAccentResult(BaseModel):
    """A single word from the MarkAccent pipeline.

    Combines the surface + reading from Yahoo Furigana with the per-mora
    pitch contour from OJAD. `subword` is propagated through from the
    Yahoo response unchanged.
    """

    furigana: str = Field(description="Furigana of given kana and kanji")
    surface: str = Field(description="The (partial of) original query text")
    accent: list[AccentInfo] = Field(description="The accent of givent word")
    subword: list[WordResult] = Field(
        default_factory=list,
        description="A list contains more details when a word contains "
        "both kanji and kana.",
    )


class FuriganaResponse(BaseModel):
    """Response envelope for /MarkFurigana/."""

    status: int = Field(
        default=200, description="Status code of response align with RFC 9110"
    )
    result: list[WordResult] | None = Field(
        description="A list contains marked results"
    )
    error: ErrorInfo | None = Field(
        default=None,
        description="An object that describe the details of an error when occur",
    )


class AccentResponse(BaseModel):
    """Response envelope for /MarkAccent/."""

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
