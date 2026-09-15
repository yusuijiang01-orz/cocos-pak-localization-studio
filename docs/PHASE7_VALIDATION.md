# Studio vNext Phase 7 Validation

Phase 7 is the end-to-end validation stage.  Automated unit tests alone are not accepted as proof that a translated game package is safe.

## Real corpus baseline

The user-supplied combined export audited for this phase is:

- File: `all_paks_player_visible_full_localization.xlsx`
- SHA-256: `31e6f87bfb5294400e17437f6894b034e3d55fff5f7063188ac292e3399c9b25`
- Rows: 49,528
- Normalized unique source texts: 46,584
- Reusable duplicates after safe normalization: 2,944 (5.94%)
- Duplicate/conflicting IDs: 0
- Missing identity rows: 0

PAK distribution:

| PAK | rows |
| --- | ---: |
| settings.pak | 28,490 |
| updatefs.pak | 10,521 |
| ui.pak | 10,517 |

Resource distribution:

| type | rows |
| --- | ---: |
| TSV | 31,438 |
| INI | 11,549 |
| TXT | 5,334 |
| LUA | 1,207 |

After the Phase-7 Vietnamese/mojibake regression fix, the vNext source classifier produces:

| classification | count |
| --- | ---: |
| sentence | 21,790 |
| ui_short | 11,026 |
| proper_noun | 10,873 |
| already_chinese | 4,617 |
| mixed_source | 1,018 |
| technical | 204 |

Language detection:

| language | count |
| --- | ---: |
| vi | 43,509 |
| zh | 4,617 |
| mixed | 1,018 |
| technical | 204 |
| latin_other | 180 |

### Phase-7 corpus bug found

The previous source classifier treated every standalone `Â` or `Ã` as possible mojibake.  This incorrectly marked 146 valid Vietnamese rows such as `Ân Hồng` / `Ân Giao` as `corrupt_source`, excluding them from the normal automatic translation route.  The rule now only accepts strong mojibake signatures (replacement markers, `á»`/`áº`, or UTF-8-as-Latin-1 continuation patterns).  The audited corpus now has **0** rows falsely classified as corrupt by that rule.

## Automated E2E gates

Phase 7 now includes a real PACK-format synthetic end-to-end test, not just mocks:

1. create three valid method-1 PACK archives named `settings.pak`, `updatefs.pak`, `ui.pak`;
2. extract them through `pak_core.extract_one`;
3. ingest real Studio `text_records.json` structures into vNext;
4. apply safe Chinese targets;
5. run final vNext preflight;
6. materialize INI/TSV/TXT resources;
7. rebuild all three archives through the production builder;
8. NRV2B self-roundtrip every changed stream;
9. re-extract every candidate archive;
10. byte/content-check translated resources;
11. verify original archives remain byte-identical;
12. require a `verified` build snapshot.

A second E2E regression verifies that a structure-damaging target is blocked before publication.

## Windows/Electron gate

GitHub Actions has a `windows-latest` Phase-7 job which:

1. runs the three-PAK PACK E2E test on Windows;
2. installs the declared Electron version;
3. launches the **actual** production `electron/main_vnext.js` entry point;
4. waits for the real renderer to load;
5. verifies the preload bridge exposes dashboard/translation/review/build/compatibility APIs;
6. verifies the production renderer contains the six vNext navigation pages;
7. exits non-zero on timeout, renderer failure, missing IPC, or missing UI wiring.

## Real game validation still required

The final Phase-7 sign-off requires the user's untouched original archives and the Windows game runtime.  The following are not considered complete until they are performed on those assets:

- import untouched `settings.pak`, `updatefs.pak`, `ui.pak`;
- confirm extraction manifests match their source archives;
- run the real workspace readiness gate;
- run a controlled translation sample through TM + glossary + Ollama + QA;
- verified-build all three real archives;
- re-extract all three generated archives with zero failures;
- confirm no protected-token/identifier/encoding regressions;
- place the generated archives in the real client using the user's normal deployment method;
- launch the game and exercise login/startup, UI screens, NPC/dialog/task text, item/equipment text and representative Lua-driven screens;
- confirm no startup crash, scene crash, resource-load error, mojibake, mixed Chinese/Vietnamese regression, or missing placeholder;
- only then mark Phase 7 complete and create the Phase-8 incremental ZIP patch.

## Reproducible commands

Audit a real combined XLSX:

```bat
python backend\vnext_phase7_cli.py audit-xlsx --xlsx "D:\path\all_paks_player_visible_full_localization.xlsx"
```

Check whether a real Studio workspace is ready:

```bat
python backend\vnext_phase7_cli.py readiness --workspace "D:\path\workspace"
```

Run the real three-PAK verified build validation:

```bat
python backend\vnext_phase7_cli.py build --workspace "D:\path\workspace" --workers 1
```

For final-release validation, add `--require-translated --fail-on-warnings` only after the review queue has been cleared to the desired release threshold.
