"""User-maintained patch table for OJAD / UniDic misreads.

Each entry is a literal-match override: when the concatenated surface
text contains the key, the matched span is replaced by the listed
segments. The whole pipeline runs in two passes against the override
set (`apply_furigana_overrides` before OJAD, `apply_accent_overrides`
after alignment), so adding an entry here fixes both the displayed
furigana and the resulting accent contour.

## Schema

Each `USER_PATCHES[key]` value is a tuple of segments. Each segment is
either:

- **2-tuple** `(segment_surface, segment_furigana)` — the segment's
  accent defaults to **heiban** (all morae HIGH plateau).
- **3-tuple** `(segment_surface, segment_furigana, accent_spec)` —
  `accent_spec` selects the pitch shape. Choices:

  | spec | shape | example for `ほか` |
  |---|---|---|
  | `"heiban"`   | every mora HIGH                            | `(ほ,1)(か,1)` |
  | `"atamadaka"`| first mora FALL, rest LOW                  | `(ほ,2)(か,0)` |
  | `"low"`      | every mora LOW                             | `(ほ,0)(か,0)` |
  | `(0, 1, 2)`  | tuple of `0`=LOW / `1`=HIGH / `2`=FALL,    | per-mora       |
  |              | one per mora — must match mora count       |                |

The segment surfaces, concatenated, MUST equal the key length —
otherwise the boundary check at apply time logs a warning and skips
that entry.

## Adding an entry

1. Run the affected input through `POST /api/MarkAccent/` and confirm
   the misalignment is deterministic.
2. Add a line to `USER_PATCHES`.
3. Re-run `./scripts/run_10_tests.sh` to confirm no regression on the
   fixture corpus.

For atamadaka / per-mora marks that don't fit the shapes above, fall
back to writing a full `FuriganaOverride` in `reading_overrides.py`.
"""

USER_PATCHES: dict[str, tuple[tuple, ...]] = {
    # OJAD reads `33m/s` with a stray `みっ` from a sound-change quirk
    # in its CRF — without the patch, `33` ends up as `さんじゅうみっ`.
    "33m/s": (
        ("33", "さんじゅうさん"),
        ("m/s", "めーとるまいびょう"),
    ),
    # UniDic gives `本当` the casual short form `ほんと` (3 morae) but
    # OJAD pronounces it `ほんとう` (4 morae). The mismatch leaks the
    # extra `う` onto the following `の` (test_2 idx 929).
    "本当の": (
        ("本当", "ほんとう"),
        ("の", "の"),
    ),
    # UniDic gives `他` the bound-morpheme reading `た`; in this phrase
    # OJAD reads it as the standalone `ほか`, and the extra `か` lands
    # on the following `の` (test_2 / test_4). Demonstrates the
    # atamadaka shape — change to `"heiban"` if your renderer prefers
    # a flat curve.
    "他の": (
        ("他", "ほか", "atamadaka"),
        ("の", "の"),
    ),
    # `世にも` (literary "extraordinarily ...") is read `よにも`; OJAD
    # spelled it `せにも` here, leaking `い` onto the `に` token
    # (test_2 idx 629). Plain heiban contour.
    "世にも": (
        ("世", "よ"),
        ("に", "に"),
        ("も", "も"),
    ),
    # --- examples of explicit per-mora accent shapes ---
    #
    # # `名前` standalone is atamadaka ナ↓マエ — patched as int tuple:
    # "名前": (
    #     ("名前", "なまえ", (2, 0, 0)),
    # ),
    #
    # # All-LOW shape with the `"low"` shorthand:
    # "ほにゃらら": (
    #     ("ほにゃらら", "ほにゃらら", "low"),
    # ),
}
