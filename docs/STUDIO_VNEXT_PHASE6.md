# Studio vNext Phase 6 — Legacy compatibility boundaries

Phase 6 keeps legacy tools available while preventing them from silently bypassing vNext state and safety assumptions.

## Rules

1. **Legacy export/read-only tools remain available.**
2. **Legacy import/translation tools may still modify `localization/text_records.json`.** Such a mutation invalidates the vNext workspace fingerprint.
3. **vNext verified build is blocked while the workspace fingerprint is stale.** The user must explicitly synchronize or adopt the legacy state first.
4. **Legacy direct build remains available only as an explicitly warned compatibility path.** It does not use vNext final QA, TM, workspace fingerprint or verified-build publication gate.
5. **Legacy translations are never promoted directly into global TM.** The user can choose “吸收旧版当前译文”; structurally safe and QA-safe translations become project-local `legacy_candidate` targets only. Human approval is still required before promotion into trusted TM.
6. **Locked vNext/manual targets always win.** Legacy adoption cannot overwrite them, including when overwrite mode is requested.

## Workspace fingerprint

Every `ingest-workspace` stores the exact SHA-256 of `localization/text_records.json` in the vNext project DB. The compatibility status compares that fingerprint with the current file.

States:

- `synced`: vNext describes the current legacy cache.
- `never_synced`: no vNext baseline has been created yet.
- `stale`: a legacy tool or external editor changed the cache after the last vNext sync.
- `missing_records`: the legacy record cache is missing.

## Legacy adoption

`backend/vnext_compat_cli.py adopt-legacy --workspace <path>`:

- re-ingests immutable source text from `source_original`;
- compares protected-token skeletons;
- rejects structure mismatches;
- re-runs Phase-3 QA and terminology checks;
- detects conflicting translations for the same canonical source unit;
- preserves all existing vNext targets unless explicitly told to overwrite;
- never overwrites locked targets;
- stores safe adopted translations as `legacy_candidate / legacy:workspace`;
- never writes adopted content to the global TM automatically.

## Renderer compatibility layer

The legacy page now labels high-risk translation/build buttons and shows a compatibility banner with:

- **仅同步源索引** — use when legacy current translations are not needed.
- **吸收旧版当前译文** — import safe existing legacy translations as vNext project candidates.

Direct legacy build and old translation engines require an explicit compatibility confirmation.

## Phase-5 wiring correction

Phase 6 also adds the missing runtime references for `vnext.css` and `vnext_ui.js` to `renderer/index.html`. Phase-5 CI previously syntax-checked those assets but did not assert that the production HTML actually loaded them. The CI now has an explicit renderer asset-wiring gate.
