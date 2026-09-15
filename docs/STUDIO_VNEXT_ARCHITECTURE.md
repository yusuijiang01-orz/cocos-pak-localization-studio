# Studio vNext — Phase 1 architecture freeze

Branch: `studio-vnext-phase1-core`

## Goal

vNext does **not** replace the current Studio in one jump. Phase 1 creates a side-by-side core that freezes the data contracts needed for a safer translator. The current Electron UI, PAK extractor, materializer and builder remain untouched.

The principal rule is:

> A translation engine may translate natural-language spans, but it must never own runtime syntax, source-file structure, archive structure, or build safety.

## Five layers

1. **Immutable source baseline** — original PAK identity, SHA-256, file identity and source occurrence remain authoritative. Future builds must start from that baseline, never from a previously patched PAK.
2. **Translation Unit layer** — a unit is a unique natural-language source span identified by `source_key`; many source occurrences can point at one unit. Runtime syntax is stored out-of-band in occurrence skeletons.
3. **Reusable knowledge layer** — human/approved TM, canonical Chinese reference names, curated glossary and model cache are global and reusable across updates/workspaces.
4. **Translation job layer** — jobs operate only on unresolved unique units. Every unit has independent status/retry state; checkpoints live in SQLite.
5. **QA/build gate** — protected-token mismatch, encoding damage, Chinese/Vietnamese mixed output and word-by-word spaced Han output are rejected before build.

## Databases

### Project DB: `<workspace>/vnext/project.sqlite3`

Owns project-specific state:

- `projects`
- `source_paks`
- `translation_units`
- `occurrences`
- `current_targets`
- `translation_jobs`
- `job_items`
- `qa_findings`
- `build_snapshots`

`translation_units` are deduplicated natural-language spans. `occurrences` preserve where each span came from and how it will be reconstructed into the runtime resource.

### Global knowledge DB

Default Windows location:

`%APPDATA%/cocos-pak-localization-studio/vnext/knowledge.sqlite3`

Owns reusable knowledge:

- `translation_memory`
- `glossary_terms`
- `canonical_reference`
- `model_cache`

Machine output and human-approved TM are deliberately separate concepts. A locked human translation cannot be overwritten by later model output.

## Priority policy

Highest to lowest:

1. Locked manual translation
2. Approved canonical/reference translation
3. Reviewed TM
4. Other trusted TM
5. Compatible model cache
6. New model translation

A glossary is a **constraint**, not a sentence translator. It must never rebuild a long sentence by word-for-word substitution.

## Source classification

Every unit is classified before routing:

- `already_chinese`
- `ui_short`
- `sentence`
- `proper_noun`
- `mixed_source`
- `technical`
- `corrupt_source`
- `unknown`

Mixed/corrupt source is not sent through the normal translation lane without review.

## Protected syntax contract

vNext stores runtime syntax out-of-band. The model receives only natural-language spans. Reassembly is local and deterministic.

Phase 1 wraps the existing narrow `protected_segments.PATTERN` recognizer because it already encodes substantial game-specific knowledge. Later phases can replace the recognizer without changing the vNext skeleton contract.

## Compatibility

Phase 1 deliberately leaves existing features in place:

- PAK import/extraction
- full/single XLSX export/import
- Ollama/API translation
- API review
- manual edits
- legacy TM
- Chinese reference assets
- materialization and PAK build

Existing TM/glossary can be copied into the new global knowledge database with `vnext_cli.py migrate-legacy-tm`. It is a copy, not a destructive migration.

## Phase 1 CLI

Initialize the side-by-side stores:

```bat
python backend\vnext_cli.py init --workspace "D:\your-workspace"
```

Ingest the current full multi-PAK XLSX as vNext source units:

```bat
python backend\vnext_cli.py ingest-xlsx --workspace "D:\your-workspace" --xlsx "all_paks_player_visible_full_localization.xlsx"
```

Inspect classification/dedup statistics:

```bat
python backend\vnext_cli.py stats --workspace "D:\your-workspace"
```

Copy prior TM/glossary knowledge:

```bat
python backend\vnext_cli.py migrate-legacy-tm --legacy-db "D:\your-workspace\localization.db"
```

Run the Phase-1 self-test:

```bat
python backend\test_vnext_phase1.py
```

## Validation against the supplied 3-PAK XLSX

The Phase-1 ingestion code was exercised against `all_paks_player_visible_full_localization.xlsx` supplied during the refactor discussion:

- input occurrences: **49,528**
- normalized unique units: **46,584**
- already-Chinese occurrences classified: **4,617**
- Vietnamese occurrences classified: **43,509**
- mixed-source occurrences classified: **1,018**
- possible mojibake occurrences flagged: **146**
- proper-noun candidates: **10,819**
- sentence candidates: **21,717**
- UI-short candidates: **11,009**

These counts are diagnostic, not final linguistic truth. They establish a repeatable baseline for later routing/QA improvements.

## Deferred to Phase 2

Phase 1 does not wire the new core into the Electron UI and does not alter the active PAK build path. Phase 2 will add the single intelligent translation pipeline:

`TM → reference → glossary constraints → Ollama → QA → transactional commit`

with live progress, stop/resume, incrementality and duplicate fan-out.
