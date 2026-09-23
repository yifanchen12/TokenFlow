# Prompt-aware token metrics and local-only automatic routing

## Requirement reading

Implement the first two approved priorities: report honest estimated savings for the prompt actually sent to each destination, avoid compression when it has no estimated benefit, provide a small reproducible quality comparison, and prevent automatic provider routing from sending content to a non-loopback endpoint.

## Reuse survey

- Reusing: `tokenflow.py:run_workflow`, `compact_content`, `build_harness_prompt`, and `count_tokens` already centralize compression, destination prompts, and estimated token counting.
- Reusing: `model_providers.py:unified_chat` and `OpenAICompatibleProvider` already own provider ordering and an explicit model-call interface.
- Reusing: `ui_page.py` already carries explicit provider selection; README and SECURITY files already describe local/remote boundaries in separate languages.
- Reusing: standard-library `urllib.parse`, `ipaddress`, `json`, and `time`; the repository has no mandatory dependency manifest or dedicated test suite.
- Gap: counting currently covers only content, clamps a negative result to zero, and may send a longer compressed value; automatic routing includes configured remote providers. No reproducible quality comparison exists.
- Callers: `POST /api/run`, `/api/local-chat`, `/api/chat`, `/api/execute` all call `run_workflow`; the UI calls `/api/chat`.
- Build/test: `python tokenflow.py --self-check`, `python -m py_compile`, `build_tokenflow_exe.cmd` / PyInstaller.

## Capability and file boundaries

- Prompt-aware measurement:
  - `tokenflow.py` - use each destination's rendered visible prompt for baseline/candidate estimates, send original content if candidate is not smaller, preserve response keys and add a scope/applied indicator; add edge-case self-checks.
  - `model_providers.py` - share the model message constructor with the production call and evaluation script, avoiding divergent prompt definitions.
- Quality comparison:
  - `eval_workflow.py` - standalone standard-library evaluator with illustrative code/document/table fixtures, offline retained-fact checks, and optional explicit same-provider/model answer comparison; no network call by default and no storage of input or responses.
- Local-only automatic routing:
  - `model_providers.py` - auto mode excludes `cloud` and considers only configured HTTP(S) loopback endpoints for other providers, regardless of environment order; share that endpoint check with the local-chat boundary. Explicitly named providers remain available. Add focused self-checks.
  - `tokenflow.py` - reject a non-loopback FreeToken URL in `/api/local-chat`; callers who intentionally use remote FreeToken may select it via `/api/chat`.
  - `ui_page.py` - distinguish local-only auto from explicit cloud/Laya routing and confirm potentially remote choices before sending.
  - `README.md`, `README.zh-CN.md`, `SECURITY.md`, `SECURITY.zh-CN.md` - document the changed routing and estimate/evaluation meaning.
- Distribution: source commit/push and a rebuilt Windows executable in the next Release after validation; generated artifacts stay ignored.
- Unchanged: `tokenflow_store.py`, parser, PC Agent, FreeToken installation flow, harness safety modes, and database schema.

## Interfaces and invariants

- `run_workflow(task, content, render_prompt=None) -> dict` - returns the existing response fields; baseline and optimized estimates use the chosen rendered visible prompt; `result` is uncompressed source if the candidate does not reduce estimated tokens; no negative saving is hidden.
- `chat_messages(task, content) -> list[dict]` - one shared message format used by routing, measurement, and optional evaluation.
- `unified_chat(..., provider='auto')` - no non-loopback endpoint is contacted in auto mode even if configured in `TOKENFLOW_PROVIDER_ORDER`. Explicit provider selection may contact its configured endpoint and keeps existing API shape.
- `/api/local-chat` only contacts a loopback FreeToken endpoint; this closes a second route that otherwise contradicts the local-only label.
- Evaluation with no provider is offline only. Model comparison requires an explicit provider; its fixture scores are illustrative, not a general quality guarantee.
- Token estimates count visible prompt text, not hidden provider framing or billable usage.

## Build order

1. Implement and self-check the independent prompt/message and loopback-selection helpers using fixed fixtures and mocked providers.
2. Assemble prompt measurement and offline/optional-model evaluation; check unchanged API result shape and provider boundaries together.
3. Wire the UI and documentation; compile, run all self-checks, rebuild and smoke-test the EXE, then publish source and the next Release.

## Validation

- 2401-character regression: no longer transmits the longer candidate as a saving.
- Prompt baseline/candidate use identical wrappers for each destination; short and long paths keep existing response keys.
- Auto mode never calls configured remote FreeToken/Ollama/Laya/cloud; explicit cloud mode still works in a local mock test.
- Local-chat rejects a remote FreeToken URL without issuing a request.
- Offline evaluator reports a middle-fact loss without claiming a model-quality result; optional model calls are never made by default.
- Full self-check, compilation, `git diff --check`, EXE startup/shutdown and Release asset/hash verification.

## Open questions

- No user-labeled corpus or preferred model is provided. Use clearly labeled synthetic fixtures; do not report broad answer-quality claims or perform paid/remote evaluation automatically.

## Facts vs assumptions

- Verified: `run_workflow` currently counts only content and clamps savings; auto provider order defaults to `freetoken,ollama,laya,cloud`; all four workflow routes use `run_workflow`; the UI permits explicit provider selection.
- Assumed: the previous approval covers the first two recommended priorities and a normal source/Windows release, as in the established project workflow; no PC Agent or retrieval change is included.
