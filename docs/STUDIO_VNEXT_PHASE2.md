# Studio vNext Phase 2 — Smart incremental translation pipeline

Phase 2 turns the Phase-1 storage/safety primitives into one deterministic translation path:

`current target -> trusted TM -> canonical reference -> model cache -> relevant glossary constraints -> Ollama -> unified QA -> transactional commit`

## Non-negotiable behavior

- A repeated source string is translated once, then fans out to all active occurrences.
- Manual/approved/reviewed/reference/seed TM is trusted before model output.
- Locked TM/reference output cannot be overwritten by automatic model output.
- Glossary is a constraint on whole-sentence translation. It is never used as a word-by-word translator.
- Only approved/locked glossary rows are injected into prompts.
- Model cache is keyed by source + model + prompt + relevant glossary + context. Unrelated glossary changes do not invalidate every cached sentence.
- Each successful batch is committed immediately.
- Job/item state is durable in SQLite. Re-running the same configuration resumes completed work instead of starting from zero.
- Stop is cooperative: `stop_requested=1` is checked between units/batches so the latest committed batch remains reusable.
- Batch JSON failures are recursively split until a bad row is isolated instead of losing the whole batch.
- QA rejection never becomes buildable translation.
- Chinese+Vietnamese or Chinese+unapproved Latin output is an error, not a warning.
- Confirmed glossary terms are checked after model output; ignored terminology is rejected.
- Corrupt-source and technical-source units are not automatically sent to the model.

## CLI preview

```bat
python backend\vnext_cli.py translate ^
  --workspace "D:\game-workspace" ^
  --model "qwen3:14b" ^
  --ollama-base "http://127.0.0.1:11435" ^
  --batch-size 16
```

Stop the latest job without discarding committed progress:

```bat
python backend\vnext_cli.py stop-job --workspace "D:\game-workspace"
```

Inspect status:

```bat
python backend\vnext_cli.py job-status --workspace "D:\game-workspace"
```

This phase is still backend-first. Electron integration, human review UI, build materialization and verified PAK output are later phases.
