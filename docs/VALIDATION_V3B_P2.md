# V3-B Preview 2 validation

真实 `ui.pak`：429,293 bytes，216 entries。

Fresh import:
- Records: 14,532
- Unique normalized source texts: 10,014
- Existing Chinese unique: 739 (2,011 occurrences)
- TM-ready unique: 228 (1,142 occurrences)
- Pending unique: 9,047 (11,379 occurrences)

TM batch application:
- Batch 1: 200 unique -> 1,114 IDs
- Batch 2: 28 unique -> 28 IDs
- Token risks: 0

Build after TM application:
- Changed entries: 60
- `ui_localized.pak`: 440,413 bytes
- Original: 429,293 bytes
- Size delta: about +2.6%
- Re-extract: 216/216
- Round-trip: PASS

Automated checks:
- Python compile: PASS
- Node syntax checks: PASS
- Core unpack tests: PASS
- CSV exchange tests: PASS
- TM/token/NRV2B tests: PASS
- TM unique queue tests: PASS
