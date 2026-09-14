# V3-B Preview 1 Validation

Real sample: user-provided `ui.pak`.

- Import/extract: 216 / 216 entries successful.
- Text records: 14,532.
- Seed `localization.db`: 41 TM entries + 10 glossary entries.
- Local exact-match batch translation on sample: 89 records translated, 0 token risks.
- Rebuild after those 89 record updates: 12 PAK entries changed.
- Output archive: complete 216-entry `ui_localized.pak`.
- Rebuilt archive was unpacked again with the validated decoder: 216 / 216 successful.
- Every unpacked entry was byte-compared against the expected post-edit resource: PASS.
- Modified entries remain Method 1 and use a valid NRV2B literal stream; unchanged entries retain their original packed bytes.

This proves the tool's own round-trip data integrity. Target-client compatibility still requires an in-game test because the client implementation is not available in this package.
