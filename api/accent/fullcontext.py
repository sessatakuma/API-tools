"""Shared HTS full-context label parsing for pitch accent.

OpenJTalk (via `pyopenjtalk.extract_fullcontext`) and the JSUT gold set
(`jsut-label` `*.lab` files) both emit HTS-style full-context labels. This
module turns either source into a per-mora accent marking sequence in the
project's convention:

    0 = LOW / unaccented
    1 = HIGH plateau
    2 = FALL kernel (the last HIGH mora before the pitch drop)

Relevant label fields (one label per phoneme):
    -<p3>+        current phoneme (sil/pau = silence/pause)
    /A:a1+a2+a3   a2 = mora position (1-indexed, forward) in the accent phrase
    /F:f1_f2      f1 = #morae in the accent phrase, f2 = accent type (nucleus;
                  0 = heiban, N>=1 = kernel on mora N)
"""

from __future__ import annotations

import re

_P3 = re.compile(r"\-(.*?)\+")
_A2 = re.compile(r"/A:-?\d+\+(\d+)\+")
_F = re.compile(r"/F:(\d+)_(\d+)")


def nucleus_marking(a2: int, nucleus: int) -> int:
    """Per-mora 0/1/2 for the mora at position `a2` (1-indexed) given the
    accent phrase's `nucleus` (0 = heiban, N>=1 = kernel on mora N)."""
    if nucleus == 0:  # 平板: LOW, then HIGH plateau, no fall
        return 0 if a2 == 1 else 1
    if nucleus == 1:  # 頭高: FALL on mora 1, then LOW
        return 2 if a2 == 1 else 0
    # 中高 / 尾高: LOW, HIGH up to the kernel, FALL on the kernel, then LOW
    if a2 == 1:
        return 0
    if a2 < nucleus:
        return 1
    if a2 == nucleus:
        return 2
    return 0


def accent_markings_from_labels(labels: list[str]) -> list[int]:
    """One 0/1/2 marking per mora, in reading order, across all accent phrases.

    Works on any HTS full-context label list — from OpenJTalk's frontend or
    from `jsut-label`'s manually-annotated `*.lab` files.
    """
    markings: list[int] = []
    prev_key: tuple[int, int, int] | None = None
    for lab in labels:
        p3_m = _P3.search(lab)
        if not p3_m or p3_m.group(1) in ("sil", "pau"):
            continue
        a2_m, f_m = _A2.search(lab), _F.search(lab)
        if not a2_m or not f_m:
            continue
        a2 = int(a2_m.group(1))
        f1, f2 = int(f_m.group(1)), int(f_m.group(2))
        key = (a2, f1, f2)
        # Phonemes of one mora share (a2, f1, f2); emit once per new mora.
        if key == prev_key:
            continue
        prev_key = key
        markings.append(nucleus_marking(a2, f2))
    return markings
