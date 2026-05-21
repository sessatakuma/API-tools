"""Local UniDic tokeniser (data layer).

Replaces the former Yahoo MA HTTP path with in-process fugashi + NINJAL
UniDic 3.1.0. Field mapping vs the old Yahoo MA response:

    surface          ← token.surface
    furigana         ← jaconv.kata2hira(feat.kana) (fugashi emits katakana)
    base             ← feat.lemma (with "-gloss" suffix stripped)
    pos              ← feat.pos1            (top-level POS, e.g. 動詞 / 助動詞)
    pos1             ← feat.pos2            (sub-category, e.g. 一般 / 普通名詞)
    conjugation_type ← feat.cType
    conjugation_form ← feat.cForm
    lexical_kernel   ← parsed from feat.aType (UniDic-only; was unavailable
                        via Yahoo MA)

All fields in fugashi's feature struct use "*" as the null marker; we map
those to None so the downstream patches see the same shape they did under
Yahoo MA.
"""

from __future__ import annotations

import fugashi
import jaconv

from api.accent.models import WordResult

_UNIDIC_NULL = "*"

_TAGGER: fugashi.Tagger | None = None


def _get_tagger() -> fugashi.Tagger:
    """Lazy-instantiate the fugashi tagger.

    Singleton because constructing `fugashi.Tagger()` loads the UniDic
    dictionary (~774 MB) and takes a second or two; per-request construction
    would be wasteful.
    """
    global _TAGGER
    if _TAGGER is None:
        _TAGGER = fugashi.Tagger()
    return _TAGGER


def _none_if_null(value: str | None) -> str | None:
    return None if value in (None, _UNIDIC_NULL) else value


def _strip_lemma_gloss(lemma: str | None) -> str | None:
    """Drop the English/etymology gloss fugashi appends to loanword lemmas.

    UniDic stores `コーヒー-coffee` as the lemma for コーヒー; we want just
    `コーヒー` to keep parity with the lemmas Yahoo MA used to return.
    """
    if lemma is None:
        return None
    return lemma.split("-", 1)[0]


def _parse_atype(atype_raw: str | None) -> tuple[int | None, list[int] | None]:
    """Parse fugashi's `aType` field (UniDic per-morpheme kernel position).

    `aType` is `"*"` for tokens without accent annotation (particles,
    auxiliaries, etc.), a single int as string for unambiguous entries
    (`"2"`), or comma-separated ints when UniDic records multiple attested
    readings (`"2,0"`).

    Returns (primary, alternates):
      - `(None, None)` for unknown
      - `(2, None)` for a single reading
      - `(2, [2, 0])` for multi-reading (alts always contains the primary)
    """
    if atype_raw is None or atype_raw == _UNIDIC_NULL:
        return None, None
    parts = [p.strip() for p in atype_raw.split(",")]
    values: list[int] = []
    for p in parts:
        try:
            values.append(int(p))
        except ValueError:
            return None, None
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], None
    return values[0], values


def tag_local(text: str) -> list[WordResult]:
    """Tokenise `text` with local fugashi + NINJAL UniDic.

    Drop-in replacement for the former Yahoo Furigana fetch — returns the
    same `list[WordResult]` shape that `apply_furigana_overrides` and
    `align_accent` expect, with two added fields populated from UniDic:
    `lexical_kernel` (= aType primary) and `lexical_kernel_alts`
    (= aType alternates when multi-reading).
    """
    tagger = _get_tagger()
    parsed: list[WordResult] = []
    for tok in tagger(text):
        feat = tok.feature
        surface = tok.surface
        # Pick the orthographic kana (`kana`) rather than the phonological
        # `pron`: UniDic stores 忙しい as `kana=イソガシイ` (matches OJAD's
        # ortho-kana output) but `pron=イソガシー` (with chōonpu, which
        # would never align). Fall back through pron then surface if kana
        # is missing or null (e.g. punctuation tokens have `kana="*"`).
        kana_kata = _none_if_null(getattr(feat, "kana", None))
        if kana_kata is None:
            kana_kata = _none_if_null(getattr(feat, "pron", None))
        reading = jaconv.kata2hira(kana_kata) if kana_kata else surface
        primary, alts = _parse_atype(getattr(feat, "aType", None))
        parsed.append(
            WordResult(
                surface=surface,
                furigana=reading,
                base=_strip_lemma_gloss(getattr(feat, "lemma", None)),
                pos=_none_if_null(getattr(feat, "pos1", None)),
                pos1=_none_if_null(getattr(feat, "pos2", None)),
                conjugation_type=_none_if_null(getattr(feat, "cType", None)),
                conjugation_form=_none_if_null(getattr(feat, "cForm", None)),
                lexical_kernel=primary,
                lexical_kernel_alts=alts,
            )
        )
    return parsed
