"""Unit tests for the HTS full-context label parser (`fullcontext.py`).

These use synthetic labels so they exercise the parser in isolation (no
pyopenjtalk dependency). The regression test for `test_adjacent_single_mora_
phrases_not_collapsed` pins the accent-phrase-index fix: without `ap` in the
dedup key, consecutive single-mora phrases that share (a2, f1, f2) collapse
into one mora and shift every downstream marking.
"""

from __future__ import annotations

from api.accent.fullcontext import accent_markings_from_labels, nucleus_marking


def _mora_label(phoneme: str, a2: int, f1: int, f2: int, ap: int) -> str:
    """Build one synthetic HTS full-context label with the fields the parser
    reads: current phoneme (`-p3+`), mora position (`/A:0+a2+0`), phrase
    mora-count + nucleus (`/F:f1_f2`), and accent-phrase index (`/I:...@ap+`).
    """
    return (
        f"x^x-{phoneme}+x=x"
        f"/A:0+{a2}+0"
        f"/B:xx-xx_xx/C:xx_xx+xx/D:xx+xx_xx/E:xx_xx!xx_xx-xx"
        f"/F:{f1}_{f2}#0_xx@1_1|1_1"
        f"/G:xx_xx%xx_xx_xx/H:xx_xx"
        f"/I:1-1@{ap}+1&1-1|1+1"
        f"/J:xx_xx/K:1+1-1"
    )


_SIL = "x^x-sil+x=x/A:xx+xx+xx/F:xx_xx/I:xx-xx@xx+xx"
_PAU = "x^x-pau+x=x/A:xx+xx+xx/F:xx_xx/I:xx-xx@xx+xx"


class TestNucleusMarking:
    def test_heiban_low_then_high(self) -> None:
        # nucleus 0: mora 1 LOW, rest HIGH plateau, no fall.
        assert [nucleus_marking(a2, 0) for a2 in (1, 2, 3)] == [0, 1, 1]

    def test_atamadaka_fall_on_first(self) -> None:
        # nucleus 1: FALL on mora 1, then LOW.
        assert [nucleus_marking(a2, 1) for a2 in (1, 2, 3)] == [2, 0, 0]

    def test_nakadaka_fall_in_middle(self) -> None:
        # nucleus 3 over 4 morae: LOW, HIGH, FALL, LOW.
        assert [nucleus_marking(a2, 3) for a2 in (1, 2, 3, 4)] == [0, 1, 2, 0]

    def test_odaka_fall_on_last(self) -> None:
        # nucleus 2 over 2 morae: LOW then FALL on the last mora.
        assert [nucleus_marking(a2, 2) for a2 in (1, 2)] == [0, 2]


class TestAccentMarkingsFromLabels:
    def test_sil_pau_skipped(self) -> None:
        labels = [_SIL, _mora_label("a", 1, 1, 0, 1), _PAU]
        assert accent_markings_from_labels(labels) == [0]

    def test_multiple_phonemes_per_mora_emit_once(self) -> None:
        # A 2-mora heiban phrase, two phonemes on the first mora (k, a) and
        # one on the second (a). Same (ap, a2, f1, f2) → one marking per mora.
        labels = [
            _mora_label("k", 1, 2, 0, 1),
            _mora_label("a", 1, 2, 0, 1),
            _mora_label("a", 2, 2, 0, 1),
        ]
        assert accent_markings_from_labels(labels) == [0, 1]

    def test_nakadaka_phrase(self) -> None:
        labels = [
            _mora_label(p, a2, 4, 3, 1) for p, a2 in zip("yamamiti", (1, 2, 3, 4))
        ]
        assert accent_markings_from_labels(labels) == [0, 1, 2, 0]

    def test_adjacent_single_mora_phrases_not_collapsed(self) -> None:
        # Regression: three single-mora atamadaka phrases (`え / あ / う`).
        # All share (a2=1, f1=1, f2=1); only the accent-phrase index differs.
        # Must yield three markings, not one.
        labels = [
            _SIL,
            _mora_label("e", 1, 1, 1, 1),
            _PAU,
            _mora_label("a", 1, 1, 1, 2),
            _PAU,
            _mora_label("u", 1, 1, 1, 3),
            _SIL,
        ]
        assert accent_markings_from_labels(labels) == [2, 2, 2]

    def test_repeated_shape_across_phrases_kept_distinct(self) -> None:
        # Two identical 2-mora heiban phrases back to back → 4 markings.
        labels = [
            _mora_label("a", 1, 2, 0, 1),
            _mora_label("a", 2, 2, 0, 1),
            _mora_label("a", 1, 2, 0, 2),
            _mora_label("a", 2, 2, 0, 2),
        ]
        assert accent_markings_from_labels(labels) == [0, 1, 0, 1]

    def test_empty_labels(self) -> None:
        assert accent_markings_from_labels([]) == []
