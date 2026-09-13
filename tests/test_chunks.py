"""Tests for chunk building + the per-chunk length cap (`build_chunks` /
`preprocess.cap_chunk_length`).

Both are pure Python (no fugashi / pyopenjtalk), so these run anywhere. They
pin the guard that keeps the quadratic alignment DP from blowing up on a single
unbroken chunk: any chunk longer than `MAX_CHUNK_CHARS` is hard-split, every
character is preserved, and each piece gets its own `sub_idx`.
"""

from __future__ import annotations

import anyio
import pytest

import api.accent.chunking as chunking_module
from api.accent.chunking import build_chunks
from api.accent.models import WordResult
from api.accent.preprocess import MAX_CHUNK_CHARS, cap_chunk_length


@pytest.fixture(autouse=True)
def stub_chunk_tokenizer(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_tag_local(text: str) -> list[WordResult]:
        tokyo = text.find("東京都")
        if tokyo < 0:
            return [WordResult(surface=text, furigana=text)]
        surfaces = [text[:tokyo], "東京都", text[tokyo + 3 :]]
        return [
            WordResult(surface=surface, furigana=surface)
            for surface in surfaces
            if surface
        ]

    monkeypatch.setattr(chunking_module, "tag_local", fake_tag_local)


def _build_chunks(text: str) -> list[tuple[int, int, str]]:
    return anyio.run(build_chunks, text)


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


def test_oversized_chunk_avoids_splitting_kanji_compound() -> None:
    chunk = "あ" * 199 + "東京都は美しい街です"
    pieces = cap_chunk_length(chunk)
    assert not (pieces[0].endswith("東") and pieces[1].startswith("京都"))
    assert "".join(pieces) == chunk
    assert all(len(piece) <= MAX_CHUNK_CHARS for piece in pieces)


def test_oversized_chunk_prefers_supplied_token_boundary() -> None:
    chunk = "山" * 198 + "東京都は美しい街です"
    pieces = cap_chunk_length(
        chunk, token_boundaries={198, 201, 202, 205, 206, 207, 208}
    )
    assert pieces[0] == "山" * 198
    assert pieces[1].startswith("東京都")
    assert "".join(pieces) == chunk


def test_build_chunks_keeps_tokyo_metropolis_together() -> None:
    line = "山" * 198 + "東京都は美しい街です"
    chunks = _build_chunks(line)
    assert chunks[0][2] == "山" * 198
    assert chunks[1][2].startswith("東京都")
    assert "".join(chunk[2] for chunk in chunks) == line


def test_chunking_uses_earlier_boundary_to_keep_short_token_whole() -> None:
    chunk = "山" * 90 + "1" * 120
    pieces = cap_chunk_length(chunk, token_boundaries={90, 210})
    assert [len(piece) for piece in pieces] == [90, 120]
    assert "".join(pieces) == chunk


def test_normal_sentences_stay_one_chunk_each() -> None:
    # Well-punctuated text: each sentence is its own chunk, none split.
    chunks = _build_chunks("猫が好き。犬も好き。")
    assert [c[2] for c in chunks] == ["猫が好き。", "犬も好き。"]
    assert [(c[0], c[1]) for c in chunks] == [(0, 0), (0, 1)]


def test_degenerate_long_line_is_split_with_sequential_subidx() -> None:
    # A single line with no sentence terminators → one oversized chunk that
    # must be hard-split, with sub_idx incrementing across the pieces.
    line = "あ" * (MAX_CHUNK_CHARS + 10)
    chunks = _build_chunks(line)
    assert len(chunks) == 2
    assert [(c[0], c[1]) for c in chunks] == [(0, 0), (0, 1)]
    assert "".join(c[2] for c in chunks) == line
    assert all(len(c[2]) <= MAX_CHUNK_CHARS for c in chunks)


def test_boundary_tokenizer_failure_falls_back_to_hard_splits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_tokenizer(_text: str) -> list[WordResult]:
        raise RuntimeError("dictionary unavailable")

    monkeypatch.setattr(chunking_module, "tag_local", fail_tokenizer)
    line = "あ" * (MAX_CHUNK_CHARS + 1)

    chunks = _build_chunks(line)

    assert "".join(chunk[2] for chunk in chunks) == line
    assert all(len(chunk[2]) <= MAX_CHUNK_CHARS for chunk in chunks)


def test_impossibly_large_request_skips_tokenizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def record_tokenizer(_text: str) -> list[WordResult]:
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(chunking_module, "tag_local", record_tokenizer)

    chunks = _build_chunks("あ" * (MAX_CHUNK_CHARS * 64 + 1))

    assert len(chunks) > chunking_module.MAX_CHUNKS_PER_REQUEST
    assert not called


def test_subidx_continues_across_sentence_and_split() -> None:
    # First a normal sentence, then an oversized terminator-free tail on the
    # same line: sub_idx must stay unique/monotonic across both.
    line = "短い文。" + "あ" * (MAX_CHUNK_CHARS + 1)
    chunks = _build_chunks(line)
    sub_ids = [c[1] for c in chunks]
    assert sub_ids == list(range(len(chunks)))
    assert chunks[0][2] == "短い文。"
    assert "".join(c[2] for c in chunks[1:]) == "あ" * (MAX_CHUNK_CHARS + 1)
