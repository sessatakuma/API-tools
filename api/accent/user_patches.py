"""User-maintained patch table for OJAD / UniDic misreads.

Each entry is a literal-match override: when the concatenated surface
text contains the key, the matched span is replaced by the listed
segments. The whole pipeline runs in two passes against the override
set (`apply_furigana_overrides` before OJAD, `apply_accent_overrides`
after alignment), so adding an entry here fixes both the displayed
furigana and the resulting accent contour.

## Schema

Every segment is a **3-tuple**:

    (segment_surface, segment_furigana, (accent_int, accent_int, ...))

The accent tuple has one integer per **mora** of `segment_furigana`
(small kana like ゅ / ょ attach to the preceding mora, so じゅ counts
as one entry). Values:

  - `0` — LOW
  - `1` — HIGH (plateau)
  - `2` — FALL kernel (高→低 boundary)

Examples for `ほか` (2 morae):

  | shape       | tuple        | meaning              |
  |-------------|--------------|----------------------|
  | heiban      | `(1, 1)`     | both HIGH            |
  | atamadaka   | `(2, 0)`     | FALL on 1st, LOW 2nd |
  | all-LOW     | `(0, 0)`     | particle-style       |
    # extra `う` onto the following `の` (test_2 idx 929).
  | nakadaka    | `(0, 2)`     | rise then FALL       |

The segment surfaces, concatenated, MUST equal the key length —
otherwise the boundary check at apply time logs a warning and skips
that entry. The accent tuple length MUST equal the mora count of the
segment's furigana.

## Adding an entry

1. Run the affected input through `POST /api/MarkAccent/` and confirm
   the misalignment is deterministic.
2. Add a line to `USER_PATCHES`.
3. Re-run `./scripts/run_10_tests.sh` to confirm no regression on the
   fixture corpus.
"""

USER_PATCHES: dict[str, tuple[tuple[str, str, tuple[int, ...]], ...]] = {
    # OJAD reads `33m/s` with a stray `みっ` from a sound-change quirk
    # in its CRF — without the patch, `33` ends up as `さんじゅうみっ`.
    "33m/s": (
        ("33", "さんじゅうさん", (0, 1, 1, 1, 1, 1)),
        ("m/s", "めーとるまいびょう", (1, 1, 1, 1, 1, 1, 1, 1)),
    ),
    "本当の": (
        ("本当", "ほんとう", (0, 1, 1, 1)),
        ("の", "の", (1,)),
    ),
    "他の": (
        ("他", "ほか", (2, 0)),
        ("の", "の", (0,)),
    ),
    "世にも": (
        ("世", "よ", (2,)),
        ("に", "に", (0,)),
        ("も", "も", (0,)),
    ),
}
