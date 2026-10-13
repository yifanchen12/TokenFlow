# Budgets, recovery, storage and real-task evaluation

Authorized on 2026-10-13: implement all six reviewed improvements. The user chose to supply real task samples later; deliver the collection/import/annotation/evaluation tools now without inventing real samples or making paid calls.

## Reuse and scope

- `model_providers.py`, `runtime_config.py`: reuse safe provider exceptions, normalized usage, saved settings and SQLite transactions. Preserve usage on truncated Responses/Messages failures. Add configurable per-role output limits, context allowance and task token allowance.
- `task_orchestrator.py`: reuse bounded calls, cancellation, saved runs and the single running task lock. Check estimated input plus reserved output before each stage; charge returned usage where available and conservative estimates otherwise. Add durable request IDs and payload fingerprints; retries return the existing task, conflicting payloads fail. Parse path inputs inside the worker so start returns promptly.
- `document_parser.py`, `tokenflow_store.py`: reuse document parsing and SQLite. Cache successful extraction by file bytes, extension, parser version and relevant dependency versions; retain original OCR metadata. Cache failure falls back to extraction, failed extraction is never cached.
- `runtime_config.py`, `tokenflow_store.py`, `tokenflow.py`: add protected storage listing/deletion/pruning and optional retention settings. Active runs cannot be deleted. Deleted task requests retain receipts so stale retries cannot silently recreate a task. Keep automatic retention off by default.
- `ui_page.py`: preserve the existing dark console and responsive structure. Expose budgets, pending request recovery, storage size/list/delete controls, retention settings and draft benchmark export. Draft answers require human verification.
- `eval_workflow.py`, `scripts/prepare_eval_cases.py`: reuse the case schema, parser and full-pipeline runner. Import real task drafts with explicit human verification; support DOCX/XLSX/PDF/images as source files. Report quality, full input/output totals, latency and category comparisons. Unverified drafts cannot be evaluated as real labelled cases.
- `test_controls.py`, `scripts/test_ui.js`, README files and CI: focused offline regression, no live model calls. Leave OCR engine implementation, provider credentials, personal deliverables and PC Agent permissions unchanged.

## Contracts

1. `ProviderError.usage` contains only normalized numeric usage, never raw responses, credentials or partial output. Errors remain safe strings.
2. Settings retain existing defaults/keys and add `max_task_tokens`, `context_window_tokens`, `output_limits`, `history_max_runs`, `history_retention_days`, `cache_max_mb`, `cache_retention_days`. Budget checks estimate visible input, reserve output, and prevent starting an over-budget stage; Harness internal consumption remains unknown. Fixed model context capacities must be configured by the user.
3. Optional `request_id` on `/api/tasks/start`: same ID and payload returns the original run even after completion/restart; conflicting payload returns an error. GET `/api/tasks/request/<id>` allows recovery after a lost response. Frontend retains only pending ID in localStorage, not source content.
4. File parse cache is opt-in at application entry points; direct parser fixtures remain independent of user storage. Cache hits reflect the current filename and return `cache_reused` and current parse latency.
5. Management routes use loopback/session protection. Deletion is explicit in the UI. Automatic pruning follows user-selected limits, protects active runs, and preserves request receipts. Cache capacities measure logical stored content; SQLite file space may be reusable after deletion rather than immediately shrinking.
6. Real-task draft export includes original cached source and a candidate answer, with blank expected answer/facts and `label_status=draft`. Preparation requires `label_status=verified`, human-provided expected answer and must-keep facts. Examples/templates are drafts, never reported as completed real benchmarks.

## Build order and acceptance

1. Provider usage, settings, receipts and parser cache: fixtures show failed-call usage survives, limits validate, unchanged files parse once, parser/dependency changes invalidate cache.
2. Orchestration: over-budget requests cause zero model calls; duplicate submissions/restarts reuse a run; cancellation and earlier regression remain intact.
3. Storage and evaluation assembly: active deletion is rejected, retention prunes only eligible records, deleted receipts reject replay; draft cases are blocked and verified imports preserve source and labels; reports compare categories and total tokens.
4. UI/API integration: submit timeout/retry recovers without duplicate calls; budget/storage/draft controls operate with protected APIs at desktop/mobile widths.
5. Main self-check, evaluation self-check, Node UI regression, 60-case offline fact retention, Python/JS syntax, Windows EXE build/smoke. No paid model evaluation; real sample execution remains deferred by the user.

Verified: truncation drops adapter usage; stages share a 2400 cap; start lacks request identity; OCR precedes cache indexing; history only limits display, not retained records. Unknown: real tasks and manually confirmed answers, intentionally deferred.

## Verification results

- Full main self-check (including budget/cache/identity and protected HTTP routes), evaluation self-check, Node task/history/lost-response regression, Python/JavaScript syntax and Git whitespace checks passed.
- The 60-case offline repository evaluation retained all required facts. No real model quality or billed savings are claimed.
- Browser testing verified saved budget fields and a budget-rejected task with zero model calls, storage capacity/default retention controls, completed mock task/draft download, and desktop/narrow layouts without horizontal overflow. Screenshots are in `tmp/controls-qa/`.
- The generated Windows EXE passed genuine local OCR plus repeated-file reuse, zero-call statistics, duplicate/restarted receipt lookup, budget persistence, blank-label draft export, cache/history management, protected-key checks and shutdown.
- The existing `dist/TokenFlow.exe` could not be overwritten. The verified new executable and checksum are in `dist/controls/`; no user process was stopped to replace the old executable.
- `tmp/real-tasks/manifest.json` is an empty annotation template. All exercised imports/model responses were regression fixtures; real sample collection/evaluation is deferred by the user's explicit choice.
