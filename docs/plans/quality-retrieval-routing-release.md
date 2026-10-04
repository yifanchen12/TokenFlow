# Quality, Chinese retrieval, routing controls and release

## Requirement reading

The user approved all four reviewed improvements. Implement a source-grounded quality benchmark and same-model comparison, Chinese retrieval, explicit OmniRoute model controls and preview, corrected bilingual documentation, and a verified Windows release. Approval is already present in the conversation.

## Reuse survey

- `eval_workflow.py` already accepts external cases and compares model answers; reuse it, fixing substring scoring and comparing the prompt actually selected by `run_workflow`.
- `tokenflow_store.py` already owns SQLite chunks and vectors. Add Chinese character n-grams to this implementation and migrate saved vectors transactionally; retain read-only status behavior.
- `model_providers.py` and `tokenflow.py` already own the OmniRoute URL/Key snapshot and chat/Codex paths. Share model policy enforcement there and expose a preview in the existing UI.
- Existing verification: `python tokenflow.py --self-check`, `python eval_workflow.py --self-check`, Python/JavaScript syntax checks and PyInstaller build. No dependency manifest or CI workflow exists.
- Observed local model: Ollama `qwen3.5:9b`. Existing GitHub releases stop at v0.3.0.

## Capability and file boundaries

- `eval_workflow.py`: case validation/loading, exact or explicit all-facts scoring, actual selected prompt comparison, usage/latency reporting, incremental local report and quality gates. `eval_cases.repository.json`: 60 curated questions over real public repository sources with checkable answers. These are repository QA, not private user task logs or a claim of representative field accuracy.
- `tokenflow.py/compact_content`: the first 60-case offline run found 11 losses from one-line context windows. Expand matches to complete Python functions (AST parsing only), Markdown sections or prose paragraphs; retain original input if expanded context exceeds the existing limit. This is a quality fix revealed by the approved benchmark, not a dataset-specific exception.
- `tokenflow_store.py`: Chinese bigrams/trigrams, bounded top-k scan, vector version migration preserving documents. Existing hashing retrieval stays dependency-free; FTS5 is deferred until corpus-scale measurements justify another index.
- `model_providers.py`: explicit OmniRoute models, optional exact-ID allow-list and non-executing route preview. `tokenflow.py`: use the same policy for chat, evaluation and Codex; add guarded preview route.
- `ui_page.py`, `omniroute_manager.py`: model list/allow-list field, clear preview of gateway/model and unknown final upstream cost; preserve process-only Key handling.
- `README.md`, `README.zh-CN.md`, `SECURITY.md`, `SECURITY.zh-CN.md`: actual compression, benchmark interpretation, model controls, migration and release instructions. New `.github/workflows/checks.yml` and `scripts/smoke_exe.ps1`: automatic Windows checks/build artifact and reusable EXE smoke. Publish v0.4.0 with EXE and SHA256 through GitHub after verification.
- Unchanged: parser, PC Agent, FreeToken runtime management, global Codex configuration and private documents.

## Interfaces and invariants

- `load_cases(path)`: existing inline-content arrays remain valid; relative source files resolve within the case file directory and are checked before network calls.
- `evaluate(...)`: save raw/processed answers, response-reported token usage, latency and explicit gate reasons. Missing usage, insufficient samples, or synthetic/repository-only tasks cannot establish a representative production quality pass.
- `omniroute_model(model)`: reject `auto`, empty/invalid IDs and IDs outside a configured allow-list before sending content or launching Codex. Unconfigured allow-list still requires an explicit model.
- `configure_omniroute(url, key=None, allowed_models=None)`: blank Key retains it; allow-list is non-secret, process-scoped and defaults to its environment variable. URL/Key behavior remains compatible.
- `POST /api/omniroute/preview`: return gateway URL, explicit selected model, configured allow-list and uncertainty about upstream routing/cost. Never issue chat/Responses or spawn a Harness.
- Vector schema upgrade is atomic and idempotent, with `GET /api/store` staying read-only. Search remains linear but keeps only top-k results in memory.

## Build order and validation

1. Implement independent evaluation and retrieval logic; verify false-positive answer scoring, missing usage, validation, Chinese matches and existing-cache migration. Reports checkpoint after each case; source-changing resume is deferred to avoid stale comparisons.
2. Implement shared routing policy and assemble it with chat/Codex/evaluation; verify auto and disallowed models never reach outbound calls or process creation.
3. Wire the preview/UI, docs and CI; run all self-checks, offline benchmark and real local model comparisons. Build and smoke-test the final EXE, scan public files, commit/push and publish a new release with verified checksums.

## Open questions and limits

- No 50–100 annotated field tasks were supplied. Use 60 real-source repository QA tasks as an honest first baseline and provide the format for future domain tasks. Do not label them as production examples.
- OmniRoute's final provider and billable cost may change inside the gateway. Model allow-lists constrain requested IDs, not combo fallbacks; the preview states this limit.
- Local model runtime latency/usage are measured at execution. Network availability may require approved tool escalation for GitHub; the user has already authorized publication.
- Ollama uses a custom local port in the observed environment. Its OpenAI-compatible API cannot set context size; evaluation therefore uses the native chat endpoint with an explicit context capacity, temperature 0 and thinking off. It translates native counts into the shared usage schema and records these settings. This prevents hidden default-context truncation in the source-code baseline. Regular application chat retains its existing endpoint behavior.
