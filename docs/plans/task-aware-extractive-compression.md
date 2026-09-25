# Task-aware extractive compression

## Requirement reading

Stop losing answer-bearing middle spans to fixed head-tail truncation. Select original source excerpts relevant to the user's task, and preserve the full input when selection is uncertain. This is a deterministic safety improvement, not a claim of better model answers.

## Reuse survey

- Reusing: `tokenflow.py:compact_content` and `run_workflow` are the single compression path for `/api/run`, `/api/local-chat`, `/api/chat`, and `/api/execute`; prompt-aware token measurement and the smaller-prompt gate already exist.
- Reusing: `eval_workflow.py` supplies document, code, and table middle-fact fixtures, offline retention checks, and opt-in same-model comparison.
- Reusing: Python `re` and string operations; the repository has no mandatory dependency manifest or dedicated test framework.
- Gap: `compact_content` does not receive the task and always drops the middle of long input.
- Build/test: `python tokenflow.py --self-check`, `python eval_workflow.py --self-check`, `python -m py_compile tokenflow.py eval_workflow.py`, `git diff --check`.

## Capability and file boundaries

- Task-aware source selection:
  - `tokenflow.py` selects matching original lines with adjacent context and a table header when applicable; no confident match or over-budget selection returns the original. `run_workflow` passes the task to this function and keeps its response contract.
  - `tokenflow.py` self-check covers relevant middle facts, no-match fallback, and prompt-aware savings.
- Honest evaluation:
  - `eval_workflow.py` evaluates the same task-aware candidate sent by the workflow; its three fixture facts must all survive, and its model comparison remains opt-in.
- User-facing limitations:
  - `README.md` and `README.zh-CN.md` state the new selection/fallback behaviour and that answer quality remains unproven.
- Unchanged: `model_providers.py`, `tokenflow_store.py`, UI, document parser, model routing, database schema, and Windows build process.

## Interfaces and invariants

- `compact_content(content, limit=MAX_COMPRESSED_CHARS, *, task="") -> str`: keeps the existing positional arguments; an absent/unspecific task or oversized selection returns the original. Only verbatim source lines plus explicit omission markers are emitted.
- `run_workflow(task, content, render_prompt=None) -> dict`: same keys and validation; uses a task-aware candidate and sends it only when estimated visible-prompt tokens decrease.
- No new dependencies, network calls, automatic model evaluation, or training.

## Build order

1. Implement the source selector in `tokenflow.py` and verify focused fixtures independently.
2. Align `eval_workflow.py` with the selector and run its offline retention check.
3. Use the shared workflow entry point, update both READMEs, and run repository self-checks and compilation.

## Validation

- All three existing middle facts retained; the selected candidate actually saves estimated tokens.
- No-match and broad tasks preserve original content, including the 2401-character boundary case.
- Existing response keys and prompt-aware savings remain intact; no model calls occur by default.

## Open questions

- No representative labeled user corpus or preferred model is available. Do not report answer-quality improvement or train a model in this change.

## Facts vs assumptions

- Verified: the four workflow routes use `run_workflow`; `compact_content` currently keeps fixed edges; offline fixtures lose their required middle facts.
- Assumed: source line extraction is a suitable conservative first repair; real answer quality needs separate model-backed validation.
