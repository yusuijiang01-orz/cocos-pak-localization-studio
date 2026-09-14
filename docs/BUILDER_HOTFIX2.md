# V3-B Preview 1 Hotfix 2 — NRV2B compression fix

## Changed

- Replaced the temporary literal-only Method-1 writer with a real greedy LZ NRV2B encoder.
- Rebuilt entries remain Method 1 and use the same 8-bit NRV2B stream syntax as the original archive.
- Every newly compressed entry is decoded immediately and byte-compared before it can be written.
- Archive-level round-trip verification remains mandatory after build.
- Unchanged entries still reuse the exact original compressed bytes.

## Real ui.pak acceptance test

Input original archive: 429,293 bytes.
Test translation CSV: 1,142 changed records affecting 59 PAK entries.
Rebuilt archive: 440,448 bytes (about 102.6% of original size).
Archive entries: 216.
Re-extraction: 216/216 successful.
Expected-vs-reextracted bytes: all 216 entries identical.
Round-trip gate: PASS.

The previous literal-only implementation produced approximately 1.6 MB for the same class of build and has been removed from the builder.
