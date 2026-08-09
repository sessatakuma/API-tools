from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from api.accent.models import WordAccentResult

logger = logging.getLogger("api")


def _logical_length(text: str) -> int:
    return sum(not char.isspace() for char in text)


@dataclass(frozen=True, slots=True)
class SurfaceRewrite:
    start: int
    replacement: str
    original: str

    @property
    def end(self) -> int:
        return self.start + len(self.replacement)


def rewrite_matches(
    text: str,
    pattern: re.Pattern[str],
    replacement_of: Callable[[re.Match[str]], str],
) -> tuple[str, list[SurfaceRewrite]]:
    parts: list[str] = []
    rewrites: list[SurfaceRewrite] = []
    cursor = 0
    output_length = 0
    for match in pattern.finditer(text):
        prefix = text[cursor : match.start()]
        replacement = replacement_of(match)
        parts.extend((prefix, replacement))
        start = output_length + _logical_length(prefix)
        rewrites.append(
            SurfaceRewrite(
                start=start,
                replacement=replacement,
                original=match.group(0),
            )
        )
        output_length = start + _logical_length(replacement)
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts), rewrites


def restore_rewrites(
    result: list[WordAccentResult],
    rewrites: list[SurfaceRewrite],
    furigana_of: Callable[[SurfaceRewrite], str] | None = None,
) -> list[WordAccentResult]:
    out = list(result)
    for rewrite in reversed(rewrites):
        offset = 0
        restored = False
        start_index: int | None = None
        for index, word in enumerate(out):
            if offset == rewrite.start:
                start_index = index
            word_end = offset + _logical_length(word.surface)
            if offset <= rewrite.start and rewrite.end <= word_end:
                local_start = rewrite.start - offset
                local_end = rewrite.end - offset
                if word.surface[local_start:local_end] != rewrite.replacement:
                    break
                surface = (
                    word.surface[:local_start]
                    + rewrite.original
                    + word.surface[local_end:]
                )
                if furigana_of is not None and word.surface == rewrite.replacement:
                    out[index] = word.model_copy(
                        update={
                            "surface": rewrite.original,
                            "furigana": furigana_of(rewrite),
                            "accent": [],
                            "subword": [],
                        }
                    )
                else:
                    out[index] = word.model_copy(update={"surface": surface})
                restored = True
                break
            if start_index is not None and word_end == rewrite.end:
                words = out[start_index : index + 1]
                if "".join(item.surface for item in words) == rewrite.replacement:
                    first = words[0]
                    if furigana_of is not None:
                        replacement_word = first.model_copy(
                            update={
                                "surface": rewrite.original,
                                "furigana": furigana_of(rewrite),
                                "accent": [],
                                "subword": [],
                            }
                        )
                    else:
                        replacement_word = first.model_copy(
                            update={
                                "surface": rewrite.original,
                                "furigana": "".join(item.furigana for item in words),
                                "accent": [
                                    accent for item in words for accent in item.accent
                                ],
                                "subword": [],
                            }
                        )
                    out[start_index : index + 1] = [replacement_word]
                    restored = True
                break
            offset = word_end
        if not restored:
            logger.warning(
                "Surface rewrite at offset %d was not restored",
                rewrite.start,
            )
    return out
