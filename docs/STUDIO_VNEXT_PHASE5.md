# Studio vNext Phase 5 — translation-first UI

Phase 5 exposes the Phase 1–4 backend through a new default Studio workflow while keeping all legacy tools available.

## Default navigation

The renderer now exposes six top-level destinations:

1. **项目概览** — unique units, active occurrences, translated/pending/review counts, knowledge counts and recent jobs/builds.
2. **智能翻译** — TM/reference/cache/Ollama pipeline with cooperative stop-and-save.
3. **审核** — pending QA queue, source/target inspection, manual edit+approve, reject/block, or defer.
4. **术语与 TM** — approved/locked glossary management, manual TM management, and canonical-reference browsing.
5. **构建** — final preflight, selected-PAK verified build, and build history.
6. **旧版工具** — the complete pre-vNext Studio UI remains available and unchanged.

## Translation UX

Starting smart translation always re-ingests the current workspace first so occurrence locators and protected skeletons are current. The backend then uses the existing Phase-2 priority chain:

`locked/manual -> trusted TM -> canonical reference -> valid model cache -> relevant approved terminology -> Ollama -> QA`

The renderer receives vNext progress events through Electron IPC. The primary action changes to **停止并保存** while a job is running. Stop is cooperative: the current durable batch/checkpoint is committed before the process exits.

## Review UX

The review page exposes the Phase-3 durable queue and lets a human:

- edit the Chinese target and approve it as a locked TM entry;
- approve an unchanged safe target;
- reject the current target, blocking the exact bad target for that source in the current project;
- defer an item for later.

Only risk/QA items are shown; this is not a requirement to manually re-read all translated strings.

## Knowledge UX

The knowledge page provides:

- glossary create/update/delete with status, type, scope, priority and locked state;
- manual TM create/update/delete, stored as quality `manual`;
- read-only canonical-reference search.

Glossary entries remain terminology constraints; they are never used as a word-by-word sentence translator.

## Build UX

The build page always re-ingests the live workspace before preflight/build. It exposes:

- final QA/review preflight;
- optional `require translated` strictness;
- optional fail-on-warning strictness;
- selected PAK scope;
- verified Phase-4 build and re-extraction;
- durable build history.

No final PAK is published until all selected changed candidates pass verification.

## Electron integration

`package.json` now boots through `electron/main_vnext.js`, which first loads the complete legacy runtime and then registers the vNext IPC surface. The preload exposes vNext APIs without removing legacy APIs.

The new Phase-5 renderer is loaded after the legacy renderer. This is intentional: legacy IDs/events continue to exist, while the vNext shell hides the legacy main area by default and makes it accessible through **旧版工具**.

## Validation

CI performs:

- Python compile for all vNext backend modules and CLI files;
- Phase 1–5 Python tests;
- Node syntax checks for the new Electron and renderer JavaScript.

CI is not a substitute for a real Windows/Electron/three-PAK/game-runtime validation. That remains Phase 7.
