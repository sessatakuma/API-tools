"""Pydantic schemas shared by the MarkAccent endpoint family.

`/MarkAccent/` and `/MarkAccent/stream/` accept the same `Request` and emit
the same `WordAccentResult` shape; only the response envelope differs
(single `AccentResponse` vs NDJSON-streamed per-chunk objects).

`WordResult` carries MA-derived POS metadata (`pos`, `pos1`, `base`,
`conjugation_type`, `conjugation_form`) and UniDic-derived lexical accent
hints (`lexical_kernel`, `lexical_kernel_alts`). The POS fields are kept on
the model for in-pipeline use by `apply_accent_patches` but are excluded
from serialization — clients never need to see them. Strong-mode kernel
fields are exposed in the response.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Request(BaseModel):
    """Request body for both /MarkAccent/ and /MarkAccent/stream/."""

    text: str = Field(description="The text to query")
    render_english_furigana: bool = Field(
        default=False,
        description=(
            "When True, emit furigana for ASCII-letter tokens (so a client "
            "can show Japanese-style readings like Apple→アップル). Default "
            "False: pure-English tokens come back with empty furigana so "
            "they render as plain text."
        ),
    )
    render_katakana_furigana: bool = Field(
        default=False,
        description=(
            "When True, emit furigana for pure-katakana tokens (kata2hira-"
            "ed copies of the surface). Default False: a learner who can "
            "already read katakana doesn't need ruby on コーヒー. The "
            "per-mora pitch contour in `accent` is returned either way — "
            "this flag only controls the top-level ruby."
        ),
    )


class ErrorInfo(BaseModel):
    """Error payload returned in the Response envelope when something
    upstream fails (OJAD request errors, tokeniser errors, etc.).
    """

    code: int = Field(description="The error code that follows JSON-RPC 2.0")
    message: str = Field(
        description="The error message that describes the details of an error"
    )


class WordResult(BaseModel):
    """A single token from the local fugashi + UniDic tokeniser.

    `furigana` holds the reading in hiragana. The five POS metadata fields are
    `None` when a token is constructed by override replacements (i.e. has no
    MA backing) — see `apply_furigana_overrides`. `lexical_kernel` /
    `lexical_kernel_alts` come from UniDic's per-morpheme `aType` field.
    """

    furigana: str = Field(description="Reading of the surface in kana")
    surface: str = Field(description="The (partial of) original query text")
    subword: list["WordResult"] = Field(
        default_factory=list,
        description=(
            "Reserved for compatibility — the tokeniser does not emit subwords. "
            "Kept on the model so override-constructed tokens still satisfy "
            "the old schema."
        ),
    )
    # MA-derived metadata: kept on the model for in-pipeline use by
    # `apply_accent_patches` (ます/たい 助動詞 detection), but excluded from
    # serialization — clients never need to see them.
    base: str | None = Field(default=None, exclude=True)
    pos: str | None = Field(default=None, exclude=True)
    pos1: str | None = Field(default=None, exclude=True)
    conjugation_type: str | None = Field(default=None, exclude=True)
    conjugation_form: str | None = Field(default=None, exclude=True)
    lexical_kernel: int | None = Field(
        default=None,
        description=(
            "UniDic per-morpheme accent kernel position (`aType`). "
            "0 = heiban (no kernel), N >= 1 = kernel on mora N (1-indexed). "
            "`None` for tokens without aType (particles, auxiliaries, "
            "override-constructed tokens, etc.)."
        ),
    )
    lexical_kernel_alts: list[int] | None = Field(
        default=None,
        description=(
            "Alternative kernel positions when UniDic records multiple "
            'attested readings (e.g. `aType="2,0"`). The first value also '
            "appears as `lexical_kernel`; subsequent values are alternates. "
            "`None` when the entry has a single reading."
        ),
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

    Combines the surface + reading from the local tokeniser with the per-mora
    pitch contour from OJAD. POS metadata mirrors `WordResult` and is passed
    through `align_accent` so downstream patches (see `apply_accent_patches`
    in `reading_overrides`) can branch on POS / conjugation.

    Strong-mode fields (`lexical_kernel`, `lexical_kernel_alts`,
    `kernel_absorbed`) carry UniDic-derived lexical accent metadata that lets
    callers render per-word kernel hints in addition to the per-mora pitch
    contour in `accent`. See `WordResult` for kernel semantics.
    """

    furigana: str = Field(description="Furigana of given kana and kanji")
    surface: str = Field(description="The (partial of) original query text")
    accent: list[AccentInfo] = Field(description="The accent of given word")
    subword: list[WordResult] = Field(
        default_factory=list,
        description="A list contains more details when a word contains "
        "both kanji and kana.",
    )
    # MA-derived metadata: kept on the model for in-pipeline use by
    # `apply_accent_patches`, excluded from serialization.
    base: str | None = Field(default=None, exclude=True)
    pos: str | None = Field(default=None, exclude=True)
    pos1: str | None = Field(default=None, exclude=True)
    conjugation_type: str | None = Field(default=None, exclude=True)
    conjugation_form: str | None = Field(default=None, exclude=True)
    lexical_kernel: int | None = Field(
        default=None,
        description=(
            "UniDic per-morpheme accent kernel position (`aType`). "
            "0 = heiban, N >= 1 = kernel on mora N. `None` for tokens "
            "without aType (particles, auxiliaries, override-constructed)."
        ),
    )
    lexical_kernel_alts: list[int] | None = Field(
        default=None,
        description=(
            "Alternative kernel positions for multi-reading entries "
            '(e.g. `aType="2,0"` → [2, 0]). The first value also appears '
            "as `lexical_kernel`. `None` when the entry has a single reading."
        ),
    )
    kernel_absorbed: bool = Field(
        default=False,
        description=(
            "True when `lexical_kernel >= 1` (UniDic says this word has a "
            "kernel) but OJAD's per-mora output for this word's range "
            "contains no FALL marker — i.e. the kernel was absorbed into a "
            "larger prosodic phrase by OJAD's connected-speech sandhi. "
            "Useful for callers that want to display per-word lexical "
            "accent in addition to OJAD's surface contour."
        ),
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
        description="An object that describes the details of an error when one occurs",
    )
