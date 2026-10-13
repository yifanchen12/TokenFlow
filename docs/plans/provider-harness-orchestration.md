# Provider, Harness and task orchestration

User request: repair the localhost console with saved CC Switch style connections,
detected models, native Harness configuration and a cost-conscious task loop.
The user's explicit implementation request authorizes this work.

## Reuse survey
- `document_parser.py`, `token_counter.py`, `tokenflow_store.py`: local parsing,
  measurement and retrieval already exist; preserve their contracts.
- `model_providers.py`: HTTP adapters, model selection and safe error messages.
- `tokenflow.py`: protected local HTTP API, bounded CLI execution and compression.
- `ui_page.py`: native controls and the existing dark console visual system.
- No application dependencies are required; use sqlite3, urllib and Windows DPAPI.
- Existing validation: Python compilation, JavaScript syntax, TokenFlow self-check,
  evaluator self-check and the 60-question offline regression baseline.

## Capability and file boundaries
- `runtime_config.py`: connection persistence, secret protection, native catalogs,
  model discovery, bindings, role settings and saved run records.
- `task_orchestrator.py`: deterministic tools, routing, bounded planning/execution,
  result review, call budgets and visible usage accounting.
- `tokenflow.py`: native/profile Harness invocation and protected API integration.
- `model_providers.py`: reuse its adapters for saved connection protocols.
- `ui_page.py`: connection editor, detected model selects, Harness bindings,
  role configuration, task progress and run history.
- `test_orchestration.py`: focused fixture checks for persistence, discovery,
  binding, budgets and acceptance/failure paths without paid model calls.
- README files: document the new workflow and operational limits.
- Unchanged: document parsing, token counting, vector store, installer/PC actions,
  existing evaluation cases and unrelated user deliverables.

## Interfaces and invariants
- Connection APIs never return keys. Blank key preserves a saved key.
- Discovery updates the current catalog; previous model records remain labeled.
- Native catalogs come from local config/cache; they are not promises of access.
- Codex custom connections require Responses; Claude requires Anthropic Messages.
- Keys enter child processes through environment variables, never CLI arguments.
- Task inputs include task, content or a parsed local path and role settings.
- Planner output is validated: at most four typed steps, installed Harness only,
  no arbitrary local shell tool. One bounded repair is allowed after failed review.
- Runs expose stage state, outputs, review and estimates separately from usage.
- Missing/failed review never counts as passed. No cost-saving guarantee is made.
- New config mutations require existing session protection and loopback access.

## Build order and acceptance
1. Config module: save/reopen profile and encrypted key; discover models with a
   local fixture, retain historical models, detect real native catalogs.
2. Orchestrator: statistics use zero model calls; multi-step fixtures demonstrate
   local classification, cloud planning, multiple Harnesses and result review;
   malformed plans, failed execution and budget exhaustion fail visibly.
3. Assemble adapter/Harness interfaces, then HTTP routes and responsive controls.
4. Run required regression checks; browser verifies saving, selection, binding,
   automatic execution/progress and restart persistence.

## Facts and limits
- Verified: this machine has Codex, Claude and DSH wrappers, a Codex model cache
  and native Codex/Claude settings. Current UI has only hand-entered model IDs.
- Native subscription auth remains in each Harness. It is not an API key for
  generic chat adapters. DSH's provider switch is not guessed without an observed
  CLI contract; use native DSH configuration and its configured models.
- Review is model-assisted for reasoning tasks; it does not prove arbitrary
  semantic correctness. Statistics have deterministic validation.

## BUPT and DSH correction (2026-10-12)
- Verified local `BUPT_API_BASE_URL` / `BUPT_API_KEY` and gateway `/models`:
  the catalog contains `deepseek-v4-flash`. This gateway is cloud inference.
- DSH desktop carries the BUPT provider, but headless has empty user overrides.
  Its installed startup accepts a task only, not a `--model` flag. Reuse the
  observed `--patch` provider/default-model contract with a temporary JSON patch;
  credentials enter the child environment. Do not copy desktop permission policy.
- User explicitly selected BUPT classification/summary, planning and review,
  with native Codex execution. The historical `local` role key remains compatible
  but permits an explicitly selected cloud preprocessing connection, counted as cloud.
- Modify runtime config, shared Harness execution, task stage labels, UI selects
  and existing focused tests. Verify gateway discovery, native execution, child
  patch secrecy and the full configured task before rebuilding the EXE.
