"""User-maintained patch table for OJAD / UniDic misreads.

Each entry is a literal-match override: when the concatenated surface
text contains the key, the matched span is replaced by the listed
segments. The whole pipeline runs in two passes against the override
set (`apply_furigana_overrides` before OJAD, `apply_accent_overrides`
after alignment), so adding an entry here fixes both the displayed
furigana and the resulting accent contour.

## Adding an entry

1. Run the affected input through `POST /api/MarkAccent/` and confirm
   the misalignment is deterministic (re-runs reproduce it).
2. Add a line to `USER_PATCHES`. The key is the literal surface
   fragment to match (regex special chars are escaped internally —
   write plain text). The value is a tuple of `(segment_surface,
   segment_furigana)` pairs. The segment surfaces, concatenated, MUST
   equal the key.
3. Re-run `./scripts/run_10_tests.sh` to confirm no regression on the
   fixture corpus.

## Example

OJAD pronounces `33m/s` as `さんじゅう・みっ・めーとる・まいびょう`
— a stray `みっ` leaks out of a sound-change quirk in OJAD's CRF.
The patch:

    "33m/s": (
        ("33", "さんじゅうさん"),
        ("m/s", "めーとるまいびょう"),
    ),

gives back `33|さんじゅうさん` and `m/s|めーとるまいびょう` as two
clean tokens.

## Accent

Every patch's accent defaults to **heiban** (HIGH plateau, no FALL
kernel) — fine for "just fix the reading" cases. If you need
atamadaka / custom per-mora marks, drop down to a fully-built
`FuriganaOverride` in `reading_overrides.py` directly; this file is
data-only by design.
"""

USER_PATCHES: dict[str, tuple[tuple[str, str], ...]] = {
    # OJAD reads `33m/s` with a stray `みっ` from a sound-change quirk.
    "33m/s": (
        ("33", "さんじゅうさん"),
        ("m/s", "めーとるまいびょう"),
    ),
    # Add more below — see the module docstring for the format.
}
