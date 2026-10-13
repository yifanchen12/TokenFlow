# Six authorized TokenFlow improvements

User approved implementation on 2026-10-13: “全修，开始”. Preserve the existing uncommitted work and UI style.

## Reuse survey and boundaries

- `document_parser.py`: stdlib ZIP/XML parsing already bounds decompression. Extend XLSX cells, workbook relationships, sheet metadata and DOCX paragraph separation. No dependency required.
- `tokenflow.py`: existing Harness adapters, bounded output and timeouts. Local Codex help confirms stdin; Claude supports text input in print mode; installed DSH headless bundle exposes the `headless-runner.config.task` overlay. Reuse temporary overlay files for DSH.
- `task_orchestrator.py`: existing call budget, execution/review/repair, single-task lock. Add cooperative cancellation, narrow single-step routing, optional preprocessing and document-cache retrieval.
- `tokenflow_store.py`: existing content-hash deduplication, lexical hash search. Scope retrieval to one document; use existing structure-preserving extraction for selected source lines. Never mix unrelated cached documents into a task.
- `runtime_config.py`: preserve storage and secret protection; recognize cancelling tasks interrupted on restart.
- `ui_page.py`: preserve visual style; separate running task from displayed history, expose cancel/return-to-running controls and retry polling errors without resubmitting tasks.
- `eval_workflow.py`: reuse sample validation, scoring and reports; add full-pipeline comparison against one raw-input call, summed input/output usage, calls, latency, acceptance and repairs. Live evaluation remains explicit opt-in.
- `test_orchestration.py`, parser/store self-checks and one focused UI check: executable regression fixtures, no credentials or paid requests.
- `README.md`, `README.zh-CN.md`, CI: document and run the new checks.
- Unchanged: OCR implementation, installers, provider credentials and PC Agent permissions; no new runtime dependencies.

## Contracts and invariants

- Parsed XLSX returns `text` plus `sheets` with names and row/cell positions; sparse cells never shift columns, inline/shared strings remain intact. Single-sheet text stays TSV, multi-sheet text includes sheet labels.
- Harness transport keeps complete prompt off argv. Bounded subprocess helper accepts stdin/cancel event; cancelled or timed-out process trees terminate and pipes close.
- `POST /api/tasks/cancel {id}` uses existing session/loopback checks. Terminal statuses include `cancelled`; `cancelling` means an in-flight HTTP call is settling. Already produced results and usage stay available. No call starts after cancellation.
- Simple source-only QA/summary/translation uses executor + reviewer; multi-step/tool requests keep planning. Deterministic statistics still use zero model calls.
- Cache indexes only supplied content, deduplicates repeated inputs and returns source references. Broad tasks keep full source, uncertain/oversized extraction falls back to original. Reviewer always reads original input.
- History selection never changes the tracked running task or deletes its recovery marker. Refresh also detects running tasks from the server.
- Pipeline evaluation includes every attempted stage and repair. Missing/incomplete usage or failed acceptance cannot pass the gate; estimates never masquerade as reported usage. Synthetic fixtures are explicitly labelled, not independent production quality evidence.

## Build order and validation

1. Parser and Harness transport fixtures: inline/shared/sparse XLSX, multiple sheets, DOCX paragraphs, 40k stdin, timeout/cancellation.
2. Cache/routing/cancellation fixtures: deduplication, scoped source retrieval and original-source review, two-call simple tasks, zero-call stats, multi-step planning, interrupted/cancelled tasks.
3. HTTP/UI assembly: authenticated cancel API, independent history, refresh recovery, polling retry; syntax and interaction checks at desktop/mobile widths.
4. Pipeline evaluation: mocked calls with known usage, repair/failure/missing-usage gate; independent documented case format and explicit live invocation.
5. Repository checks: main self-check, evaluation self-check, 60-case offline fact retention, JS syntax, Python compile. Build/smoke-test EXE if available.

Verified facts: current XLSX loses inline strings/sparse columns; argv transport exceeds Windows bounds; ordinary QA currently requires three calls; cache search is not wired into orchestration; history replaces the tracked run. Assumption: authenticated provider protocols stay compatible with existing adapters. No external model request is necessary to implement and validate these fixes.

## Verification results — 2026-10-13

- Python compile and Git whitespace checks passed. Main self-check, focused optimization checks, evaluation self-check and Node UI state regression passed.
- The 60-case repository offline evaluation retained all required facts (critical fact loss rate 0). This checks extraction only, not live model answer quality or billed savings.
- Regression fixtures cover sparse/multi-sheet XLSX, paragraph-preserving DOCX, long UTF-8 subprocess stdin, Harness transports, authenticated cancellation, interrupted restart, scoped cache retrieval, two-call routing, repair costs and missing-usage gates.
- Browser testing exposed a whitespace-sensitive CSV routing bug; normalized matching now recognizes “统计 CSV 的行数和平均值” as zero-call work and still requires a valid table.
- The Windows executable passed isolated startup, API policy, protected-key handling, local OCR, zero-call task, restart persistence, page controls and shutdown checks. No paid/model-provider calls were made.
- Browser desktop and narrow viewport geometry had no horizontal overflow; screenshots are in `tmp/optimization-qa/`. After clearing the stalled test tab, browser interactions also passed: zero-call CSV statistics, viewing history during a live task, returning to that task, cancellation retaining the finished API call's usage with no subsequent reviewer call, and restoring task tracking on refresh. These checks used an isolated local fixture API, not a paid model.
