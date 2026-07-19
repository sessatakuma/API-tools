"""Tests for chunk building + the per-chunk length cap (`build_chunks` /
`preprocess.cap_chunk_length`).

Both are pure Python (no fugashi / pyopenjtalk), so these run anywhere. They
pin the guard that keeps the quadratic alignment DP from blowing up on a single
unbroken chunk: any chunk longer than `MAX_CHUNK_CHARS` is hard-split, every
character is preserved, and each piece gets its own `sub_idx`.
"""

from __future__ import annotations

from api.accent.pipeline import build_chunks
from api.accent.preprocess import MAX_CHUNK_CHARS, cap_chunk_length


def test_short_chunk_unchanged() -> None:
    assert cap_chunk_length("猫が好き") == ["猫が好き"]


def test_boundary_length_not_split() -> None:
    s = "あ" * MAX_CHUNK_CHARS
    assert cap_chunk_length(s) == [s]


def test_oversized_chunk_split_into_capped_pieces() -> None:
    s = "あ" * (MAX_CHUNK_CHARS * 2 + 5)
    pieces = cap_chunk_length(s)
    assert all(len(p) <= MAX_CHUNK_CHARS for p in pieces)
    assert "".join(pieces) == s  # no character lost
    assert len(pieces) == 3


def test_oversized_prefers_comma_in_back_half() -> None:
    # A comma in the back half of the window is the preferred cut point, and it
    # stays attached to the left piece.
    chunk = "あ" * 150 + "、" + "あ" * 100
    pieces = cap_chunk_length(chunk)
    assert pieces[0] == "あ" * 150 + "、"
    assert pieces[1] == "あ" * 100
    assert all(len(p) <= MAX_CHUNK_CHARS for p in pieces)
    assert "".join(pieces) == chunk


def test_oversized_ignores_early_comma_and_hard_cuts() -> None:
    # A comma only in the front part of the window is skipped (it would leave a
    # tiny sliver); the cut falls back to a hard cut at the limit.
    chunk = "あ" * 50 + "、" + "あ" * 200
    pieces = cap_chunk_length(chunk)
    assert len(pieces[0]) == MAX_CHUNK_CHARS  # hard cut, comma too early
    assert all(len(p) <= MAX_CHUNK_CHARS for p in pieces)
    assert "".join(pieces) == chunk


def test_normal_sentences_stay_one_chunk_each() -> None:
    # Well-punctuated text: each sentence is its own chunk, none split.
    chunks = build_chunks("猫が好き。犬も好き。")
    assert [c[2] for c in chunks] == ["猫が好き。", "犬も好き。"]
    assert [(c[0], c[1]) for c in chunks] == [(0, 0), (0, 1)]


def test_degenerate_long_line_is_split_with_sequential_subidx() -> None:
    # A single line with no sentence terminators → one oversized chunk that
    # must be hard-split, with sub_idx incrementing across the pieces.
    line = "あ" * (MAX_CHUNK_CHARS + 10)
    chunks = build_chunks(line)
    assert len(chunks) == 2
    assert [(c[0], c[1]) for c in chunks] == [(0, 0), (0, 1)]
    assert "".join(c[2] for c in chunks) == line
    assert all(len(c[2]) <= MAX_CHUNK_CHARS for c in chunks)


def test_subidx_continues_across_sentence_and_split() -> None:
    # First a normal sentence, then an oversized terminator-free tail on the
    # same line: sub_idx must stay unique/monotonic across both.
    line = "短い文。" + "あ" * (MAX_CHUNK_CHARS + 1)
    chunks = build_chunks(line)
    sub_ids = [c[1] for c in chunks]
    assert sub_ids == list(range(len(chunks)))
    assert chunks[0][2] == "短い文。"
    assert "".join(c[2] for c in chunks[1:]) == "あ" * (MAX_CHUNK_CHARS + 1)
