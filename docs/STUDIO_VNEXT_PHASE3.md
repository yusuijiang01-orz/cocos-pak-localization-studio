# Studio vNext Phase 3 — QA / Human Review / TM Promotion

Phase 3 adds the safety and human-correction layer between automatic translation and future PAK materialization.

## Goals

1. Automatic translation may propose text, but unsafe text must not silently become trusted text.
2. Human corrections become durable reusable translation memory (TM).
3. A translation explicitly rejected by the user must not immediately reappear from TM/reference/cache/model output in the same project.
4. Review state and history survive Studio restarts.
5. The review queue must stay small: only risk items and warnings are queued, rather than forcing manual review of every model result.

## Unified QA gates

Current build-blocking findings include:

- `PROTECTED_TOKEN_MISMATCH` — placeholders/tags/paths/control markers changed.
- `ENCODING_REPLACEMENT_CHAR` — obvious mojibake/replacement characters.
- `NUMBER_MISMATCH` — numeric values/percentages/order changed.
- `NO_CHINESE_TARGET` — translatable Vietnamese/Latin source produced no Chinese.
- `ZH_VI_MIXED` — Chinese target still contains Vietnamese diacritics.
- `ZH_LATIN_MIXED` — Chinese target still contains non-approved Latin words.
- `WORD_BY_WORD_SPACED_HAN` — suspicious `每 日 能 有 ...` word-by-word output.
- `GLOSSARY_TERM_MISMATCH` — approved/locked terminology was ignored.

Warnings currently include unchanged output, extreme length ratio and suspicious repeated punctuation. Warnings are build-safe but are routed to human review.

## Review queue

Project database tables:

- `review_items` — current durable queue state.
- `review_actions` — audit history for approve/reject/defer/reopen.
- `blocked_targets` — project-local fingerprints of translations explicitly rejected by the user.
- `qa_findings` — unresolved and historical automated QA findings.

Review states:

- `pending`
- `approved`
- `rejected`
- `deferred`
- `resolved`

Queue priority is weighted by severity, source risk, proper-noun risk and occurrence count. A bad translation reused in many files therefore rises above a one-off warning.

## Human approval

Approving a translation does all of the following:

1. Re-runs hard QA and refuses approval if any error/fatal issue remains.
2. Writes the accepted target into `current_targets`.
3. Marks it locked by default.
4. Promotes the source/target pair into global trusted TM.
5. Uses quality `manual` when the reviewer edited the target; otherwise `approved`.
6. Resolves old QA findings and records an audit action.
7. Removes a matching project-local rejection fingerprint if the user intentionally approves that exact target later.

This gives the intended priority:

`human locked TM > approved TM/reference > model cache > new Ollama call`

## Human rejection

Rejecting a translation:

1. Keeps the rejected text for audit/display instead of deleting evidence.
2. Marks the project target rejected and unlocked.
3. Marks matching model cache rejected when applicable.
4. Adds the exact target fingerprint to `blocked_targets`.
5. Returns translation job items to a retryable failed state.

The Phase-2 pipeline now checks `blocked_targets` before applying TM, reference, cache or fresh model output. If the model returns the exact rejected target again, it is not accepted.

## CLI smoke workflow

```bat
python backend\vnext_cli.py review-sync --workspace "D:\workspace"
python backend\vnext_cli.py review-stats --workspace "D:\workspace"
python backend\vnext_cli.py review-list --workspace "D:\workspace" --state pending --limit 50
python backend\vnext_cli.py review-show --workspace "D:\workspace" --unit-id u_xxx
```

Approve the current suggestion:

```bat
python backend\vnext_cli.py review-approve --workspace "D:\workspace" --unit-id u_xxx
```

Edit and approve (becomes locked manual TM):

```bat
python backend\vnext_cli.py review-approve --workspace "D:\workspace" --unit-id u_xxx --target "每天完成七关试炼。"
```

Reject/defer/reopen:

```bat
python backend\vnext_cli.py review-reject --workspace "D:\workspace" --unit-id u_xxx --note "语义错误"
python backend\vnext_cli.py review-defer --workspace "D:\workspace" --unit-id u_xxx
python backend\vnext_cli.py review-reopen --workspace "D:\workspace" --unit-id u_xxx
```

## Deliberately not included yet

Phase 3 is backend-first. The current production Electron interface is still not switched to vNext. The review queue will be wired into the new Studio UI in the UI migration phase. PAK materialization/build remains legacy until Phase 4 adds build gates and re-extraction verification.
