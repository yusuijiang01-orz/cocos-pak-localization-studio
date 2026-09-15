# Studio vNext Phase 4 — Safe Materialization and Verified PAK Build

Phase 4 closes the gap between a safe translation target in SQLite and a PAK that can be published to the game.

## Non-negotiable rules

1. The original PAK is immutable build input.
2. The legacy `localization/text_records.json` is never overwritten by the vNext builder.
3. Runtime syntax is reconstructed from an out-of-band skeleton; model output never owns placeholders/tags.
4. A target must pass final QA again at build time. Cached `qa_status='passed'` is not blindly trusted.
5. Human-blocked targets cannot be rebuilt.
6. Pending/deferred/rejected `error` or `fatal` review items block the build.
7. Legacy materializer fallback/skip is a hard failure in vNext. Silent source fallback is not accepted as a successful translation build.
8. Every changed PAK must pass the existing builder's compression self-check, structure gate, re-extraction and byte-roundtrip check.
9. Selected PAKs are built into an isolated staging directory first. Final publication starts only after every selected changed PAK verifies.
10. A SHA-256 build snapshot is written to `build_snapshots` for audit/reproducibility.

## Workspace ingestion

The XLSX ingestion path remains useful for corpus analysis, but production vNext builds should ingest the live Studio workspace:

```bat
python backend\vnext_cli.py ingest-workspace --workspace "D:\workspace"
```

This reads `localization/text_records.json` without modifying it and creates exact occurrences with:

- PAK name
- extracted source file
- legacy record id
- row/column/key locator metadata
- protected runtime skeleton
- one or more deduplicated text-span unit ids

Example source:

```text
$Hoàn thành <c=green>{0}</c> nhiệm vụ
```

is stored conceptually as:

```text
PROTECTED "$"
TEXT      "Hoàn thành "
PROTECTED "<c=green>"
PROTECTED "{0}"
PROTECTED "</c>"
TEXT      " nhiệm vụ"
```

Only the `TEXT` pieces become translation units.

## Final QA preflight

```bat
python backend\vnext_cli.py build-preflight --workspace "D:\workspace"
```

Optional strict release mode:

```bat
python backend\vnext_cli.py build-preflight ^
  --workspace "D:\workspace" ^
  --require-translated ^
  --fail-on-warnings
```

The preflight checks current targets again for:

- rejected/failed target state
- human-blocked fingerprint
- protected-token mismatch
- number/percentage mismatch
- encoding replacement characters
- Chinese/Vietnamese or Chinese/unapproved-Latin mixing
- non-Chinese output
- word-by-word spaced Han output
- locked terminology mismatch
- unresolved error/fatal human review

Warnings are reported but do not block by default.

## Temporary legacy-record reconstruction

`prepare_legacy_records()` creates a temporary copy of the legacy record list. It reconstructs each full runtime string from the immutable skeleton and current vNext span targets.

Untranslated spans keep their original source text unless `--require-translated` is enabled.

The user's real `text_records.json` remains untouched.

## Strict materialization

The temporary record cache is passed into the existing byte-aware materializer. vNext treats either of these as a build failure:

- `skipped_count > 0`
- `safe_fallback_count > 0`

This prevents the UI from reporting a successful build while some requested translations silently stayed in the source language.

## Verified PAK build

```bat
python backend\vnext_cli.py build-paks --workspace "D:\workspace"
```

Build selected archives only:

```bat
python backend\vnext_cli.py build-paks ^
  --workspace "D:\workspace" ^
  --pak ui.pak ^
  --pak settings.pak
```

Default output:

```text
<workspace>\build-vnext\
```

The Phase-4 wrapper calls the existing hardened builder, which already provides:

- original PAK vs extraction-manifest identity gate
- sparse changed-file materialization
- protected path/tag/header checks
- unsupported compression-method refusal
- NRV2B compressor decode-and-byte-compare self-check
- compact physical-order archive reconstruction
- byte-identical preservation of untouched compressed payloads
- full candidate PAK re-extraction
- changed-resource byte-roundtrip comparison
- atomic archive publication

Phase 4 adds a higher-level multi-PAK transaction: all selected candidates verify before any of them is copied to the final vNext output folder.

## Build snapshots

```bat
python backend\vnext_cli.py build-history --workspace "D:\workspace"
```

Each build records:

- source PAK path / size / SHA-256
- target PAK path / size / SHA-256
- materialization report
- build verification report
- preflight result
- verified / verified_no_changes / failed status

## Current boundary

Phase 4 is backend-only. It is not yet wired into the production Electron UI. Phase 5 will expose workspace ingest, translation progress, review queue, final build gate and build results through the redesigned Studio interface.

Real game validation with the user's three original PAKs is still required before vNext can be considered release-ready.
