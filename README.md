# TokenFlow

**A local, low-token execution framework for AI Agent workflows.**

TokenFlow reduces unnecessary context before a task reaches a local model or an AI coding Harness. The core is intentionally small: Python standard library, deterministic preprocessing, a local HTTP UI, and optional adapters for FreeToken, Codex, Claude, and DSH.

Chinese documentation: [README.zh-CN.md](README.zh-CN.md).

## Highlights

- Classifies tasks as code, table, document, or general.
- Extracts task-matching text with complete Python functions, Markdown sections or prose paragraphs; retains the original when extraction is uncertain or too large.
- Estimates before/after tokens for routing feedback. Estimates are not billing-grade tokenizer counts.
- Detects local Codex, Claude, and DSH commands and selects a default Harness/model pair.
- Uses Codex read-only mode and Claude plan mode by default.
- Connects to a local FreeToken OpenAI-compatible API without copying its inference runtime.
- Supports configurable Ollama, Laya-compatible, and cloud OpenAI-compatible providers.
- Extracts text from TXT, Markdown, source files, DOCX, XLSX, and optionally PDF.
- Stores document chunks and deterministic 64-dimensional vectors in SQLite.
- Provides explicit Jev decision requests as a routing/guardrail backend, not as a chat-model replacement.
- Provides allow-listed PC actions with dry-run and explicit execution opt-in.
- Provides a user-confirmed Windows installer launcher and local FreeToken engine starter.
- Offers an opt-in OmniRoute setup panel for installation, local startup, and an in-memory Endpoint Key.

## Architecture

The console's primary action now submits a task to a bounded workflow: local
file reading and statistics, optional configured-model classification/summary,
model planning, sequential model/Harness assignment, and result acceptance.
Pure supported counting/statistics requests complete without any model call.
Reasoning results require a valid reviewer verdict; a rejected result may be
repaired once within the configured call budget. Failed or missing acceptance
is never displayed as completed.

### Saved connections and detected models

Open **Provider and Harness configuration** in the console to add named
connections with a Base URL, API Key and protocol (Chat Completions, local Ollama, Responses,
or Anthropic Messages). Saving probes `/models`; the current catalog and previous
model records persist across restarts. Endpoints without discovery support can
use manually registered IDs. Failed discovery preserves existing records and
shows its error. Local detection probes only configured/default loopback
Ollama and FreeToken endpoints, without generating text or downloading models.

Codex model choices can refresh the native CLI model/list catalog; local config/cache is the fallback;
Claude reads configured model IDs. Cached/registered IDs are not access guarantees.
Codex bindings require a Responses endpoint, Claude bindings require Messages;
DSH supports Chat Completions through a temporary profile patch or its native profile. Native subscription login stays with the Harness.
You can import existing API-key connections without displaying their keys.
Bindings are applied to TokenFlow-launched child processes, preserving the
Harness's native configuration files. Keys are passed through child environment
variables, never command arguments. Default execution remains read-only/plan mode.

Connections, roles and recent task records use `connections.db` beside the user
cache, outside the repository. `TOKENFLOW_CONFIG_DB` overrides this path.
Windows keys use DPAPI protection for the current system user; POSIX uses owner-only
storage permissions. Blank Key input preserves the key; changing the destination
requires re-entering it. Task records contain results and should be treated as private.
The older OmniRoute panel remains a separate, process-memory-only integration.

The task policy assigns a local model, planner, executor and reviewer, with a
3–8 stage-call budget (default 6). Planning supports at most four typed steps;
different installed Harnesses can exchange step outputs. A Harness invocation
may make additional internal calls that TokenFlow cannot meter. The UI separates
visible prompt estimates, provider-reported usage and missing usage; comparisons
with one raw input can be negative and do not guarantee billing savings or
general semantic correctness. Planning may use a labeled source preview, while
execution preserves the selected source and acceptance checks the original input.

New protected APIs: `GET /api/config`, `POST /api/config/connection`,
`POST /api/config/discover`, `POST /api/config/discover-local`,
`POST /api/config/import-native`, `POST /api/config/settings`,
`POST /api/tasks/start`, `GET /api/tasks`, `GET /api/tasks/<id>`.
Configuration and task APIs require loopback access and the session header,
including GET requests. Run `python test_orchestration.py` for the focused
offline workflow checks; they also run under `python tokenflow.py --self-check`.
Native Codex TOML import requires Python 3.11+ (included in the Windows build).

```text
Browser or CLI
      |
      v
TokenFlow HTTP server :8765
      |
      +--> deterministic preprocessing
      +--> FreeToken :1919 (optional local model)
      +--> Codex / Claude / DSH (optional Harness)
```

The server binds to `127.0.0.1` by default. It is not an authentication gateway and must not be exposed to an untrusted network without an explicit security layer.

## Quick start

Requirements: Python 3.10 or newer.

```powershell
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py omniroute_manager.py
python tokenflow.py --self-check
python tokenflow.py
```

Open <http://127.0.0.1:8765>. On Windows, double-click `start_tokenflow.cmd`.

### Windows executable

Download the ready-to-run [Windows executable from the latest GitHub Release](https://github.com/yifanchen12/TokenFlow/releases/latest/download/TokenFlow.exe), or run `build_tokenflow_exe.cmd` to build `dist/TokenFlow.exe` yourself. The executable starts the local server and opens the TokenFlow console in the default browser. Use **Close service** in the page to stop it. Generated binaries stay out of source commits and are distributed as release assets.

The SQLite cache is stored in the current user's application-data directory by default (for example, `%LOCALAPPDATA%\TokenFlow\tokenflow.db` on Windows). Set `TOKENFLOW_DB_PATH` to use a different location. Opening the status page does not create the database; it is initialized when documents are indexed.

If an earlier version's `tokenflow.db` is found in the old working or application directory, the cache card offers an explicit import. Imported documents are deduplicated into the current cache; the old database is left untouched.

## FreeToken integration

FreeToken is optional. TokenFlow uses its local OpenAI-compatible API:

```text
GET  http://127.0.0.1:1919/health
GET  http://127.0.0.1:1919/v1/models
POST http://127.0.0.1:1919/v1/chat/completions
```

On Windows, double-click `install_freetoken.cmd`. The helper checks `TOKENFLOW_FREETOKEN_INSTALLER`, an installer beside this repository, and the user's Downloads directory. The installer opens in its own window and requires explicit user confirmation.

Set `TOKENFLOW_FREETOKEN_MODEL` to the model directory and click **Start FreeToken** in the TokenFlow page. The helper does not silently download or execute an installer. Model weights are not bundled in this repository.

```powershell
$env:TOKENFLOW_FREETOKEN_MODEL = "<model-directory>"
$env:TOKENFLOW_FREETOKEN_URL = "http://127.0.0.1:1919"
python tokenflow.py
```

Linux users should install and start FreeToken according to its official documentation.

## Provider routing

`POST /api/chat` accepts `provider: "auto" | "freetoken" | "ollama" | "omniroute" | "laya" | "cloud"`. Automatic routing follows `TOKENFLOW_PROVIDER_ORDER`, which defaults to `freetoken,ollama,laya,cloud`, but **only tries HTTP(S) loopback endpoints and always excludes `cloud` and `omniroute`**. OmniRoute is excluded even on loopback because the gateway can forward requests to remote or paid models. Remote FreeToken, Ollama, and Laya endpoints are also excluded from automatic routing. If no eligible endpoint works, TokenFlow returns an error. Explicit provider selection can use its configured remote URL; the browser asks for confirmation, while direct API clients must manage that decision themselves.

`POST /api/local-chat` also requires a loopback FreeToken URL. To intentionally use remote FreeToken, call `/api/chat` with `provider: "freetoken"`.

Configure providers with environment variables:

```text
TOKENFLOW_FREETOKEN_URL       Local FreeToken endpoint; default http://127.0.0.1:1919
TOKENFLOW_OLLAMA_URL          Ollama OpenAI-compatible endpoint; default http://127.0.0.1:11434/v1
TOKENFLOW_OMNIROUTE_URL       OmniRoute /v1 endpoint; default http://127.0.0.1:20128/v1
TOKENFLOW_OMNIROUTE_API_KEY   Endpoint Key from OmniRoute; required by the tested local setup and for remote gateways
TOKENFLOW_OMNIROUTE_ALLOWED_MODELS  Optional comma-separated exact model IDs; OmniRoute always requires an explicit model
TOKENFLOW_LAYA_URL            User-supplied Laya-compatible endpoint
TOKENFLOW_LAYA_API_KEY        Optional Laya key, read only from the environment
TOKENFLOW_CLOUD_URL           User-supplied cloud OpenAI-compatible endpoint
TOKENFLOW_CLOUD_API_KEY       Cloud key, read only from the environment
TOKENFLOW_PROVIDER_ORDER      Comma-separated provider order
```

The page's **OmniRoute setup** panel detects an existing CLI, can start the default loopback gateway, and, on Windows, offers a confirmed **Install OmniRoute** action. Installation requires Node.js/npm and opens a visible window running `npm install -g omniroute` from the [upstream package](https://github.com/diegosouzapw/OmniRoute). TokenFlow does not bundle OmniRoute or install Node.js. Review the upstream project and package before accepting the install; other platforms should follow its official instructions. An already installed CLI is not reinstalled.

Enter a `/v1` URL and Endpoint Key in the panel, then save. The Key remains only in this TokenFlow process's memory (or can be supplied through `TOKENFLOW_OMNIROUTE_API_KEY`); it is never returned by the API, written to disk, or included in CLI arguments. A blank Key field preserves the existing Key. Closing TokenFlow discards the page-supplied Key. The gateway URL must not contain credentials, a query, or a fragment; remote URLs require HTTPS and a Key. The **Start** button controls only the default loopback gateway, not a custom or remote gateway. TokenFlow's chat adapter uses `/v1/models` and `/v1/chat/completions`. Laya has no assumed public endpoint in this repository; configure the endpoint supplied by the deployment. Jev is exposed separately through `POST /api/jev/decision` with `JEV_API_KEY` and optional `TOKENFLOW_JEV_URL`. It is an explicit typed-decision call and is never invoked silently as a general chat model.

OmniRoute calls require an explicit model ID (for example, `ollama-local/qwen3.5:9b`); `auto` is rejected before content is sent. Enter an optional exact-ID allow-list on the page and save it with the connection configuration, or set `TOKENFLOW_OMNIROUTE_ALLOWED_MODELS`. An empty allow-list permits explicit IDs, not automatic selection. The same policy covers chat, evaluation and TokenFlow-launched Codex. `POST /api/omniroute/preview` with `{"model":"<model-id>"}` shows the gateway and requested model without sending a task or starting a Harness. The page previews before an OmniRoute execution. TokenFlow cannot determine final upstream cost or restrict fallback within a gateway combo; verify those settings in OmniRoute.

## Document parsing and cache

`POST /api/parse` accepts JSON with a local `path` or a `multipart/form-data` upload. Supported formats are TXT, Markdown, common source/text files, DOCX, XLSX, and PDF when `pypdf` is installed.

JSON requests are limited to 2 MB. Uploaded files are limited to 20 MB per file.

`POST /api/index` stores extracted text in SQLite and creates a deterministic 64-dimensional hashing-vector cache. Chinese text uses character bigrams/trigrams, while English uses word tokens. `POST /api/search` keeps only the top results in memory during a linear scan. Existing vectors are rebuilt transactionally on the first index/search operation with the new version; documents are preserved. Status reads do not migrate or create a database. This is lexical retrieval, not an embedding-model semantic-quality claim.

Optional parsers:

```powershell
python -m pip install pypdf
```

## PC Agent safety boundary

`POST /api/pc/plan` validates an allow-list of `move`, `click`, `type`, `key`, and `wait` actions and always returns a confirmation-required dry run. `POST /api/pc/execute` remains dry-run unless `TOKENFLOW_PC_AGENT_EXECUTE=1` is explicitly set and the optional `pyautogui` package is installed.

The UI lets you edit the PC action JSON and preview the validated plan. Previewing never executes the actions. `POST /api/shutdown` stops the local server and is exposed by the UI's **Close service** button.

There is no shell action, arbitrary executable action, or automatic screen decision loop. Review every action before enabling execution.

## Exact token counting

TokenFlow supports optional local tokenizer backends. Without an optional backend, the response is explicitly marked `backend: "heuristic"` and `exact: false`.

For a local Hugging Face `tokenizer.json`:

```powershell
python -m pip install tokenizers
$env:TOKENFLOW_TOKENIZER_PATH = "<tokenizer-directory-or-file>"
```

For an OpenAI-compatible encoding:

```powershell
python -m pip install tiktoken
$env:TOKENFLOW_TOKENIZER_BACKEND = "tiktoken"
$env:TOKENFLOW_TIKTOKEN_ENCODING = "cl100k_base"
```

Use `GET /api/tokenizer` to inspect the active backend. Tokenizer loading is local-only; TokenFlow does not download model files automatically.

### Savings and quality checks

`/api/run` estimates content tokens. Model and Harness routes estimate the visible prompt/message text actually sent. For long inputs, task matches retain complete Python functions, Markdown sections or prose paragraphs. Broad tasks such as summarization/translation, missing matches, oversized excerpts and non-saving candidates use the original, returning `compression_applied: false`. This heuristic cannot guarantee every relevant fact. `token_counting.scope` identifies the scope; estimates exclude hidden overhead and are not billable usage.

Run the three illustrative offline cases without connecting to any model or uploading content:

```powershell
python eval_workflow.py
```

To compare answers from the same configured model on raw and candidate-compressed prompts, explicitly select a provider; this sends both prompts to that endpoint:

```powershell
python eval_workflow.py --provider freetoken --model <installed-model-id>
```

The repository also includes 60 curated questions grounded in actual public project files. They are a reproducible repository-QA baseline, not production user tasks:

```powershell
python eval_workflow.py --cases eval_cases.repository.json --report tmp/offline.json
python eval_workflow.py --cases eval_cases.repository.json --provider ollama --model <installed-model-id> --report tmp/model-comparison.json
```

Use `TOKENFLOW_OLLAMA_URL` when the local service uses a custom port. The evaluator compares the original with the prompt actually selected by TokenFlow, saves answers and response-reported usage, alternates pair order, and separates prompt estimates from reported input/output totals. It generates JSON and Markdown after each case; output may contain task answers and should be reviewed before sharing.

Ollama evaluation uses its native chat API to set `--ollama-context` (default 32768), temperature 0 and thinking off. The recorded model settings apply equally to both prompts. Choose sufficient capacity for the source and output; ordinary application chat keeps the configured OpenAI-compatible endpoint behavior. These settings follow the [Ollama context guidance](https://docs.ollama.com/api/openai-compatibility#setting-the-local-context-size).

Supply a JSON array through `--cases`. Each case needs `task`, `content` (or a relative `source_file` within the case file's directory), and `must_keep` literal facts. Model comparisons also need `expected_answer`: a string or alternative-answer array. `answer_match: "exact"` is the default; `contains_all` explicitly requires every listed fragment. Numeric substring matches such as `7` inside `17` do not pass. Label `source_kind` accurately.

The default quality gate requires at least 50 completed pairs, no annotated fact loss, processed answer accuracy of at least 90%, accuracy drop of at most 2 percentage points, and at least 5% reduction in provider-reported input tokens. Missing usage or incomplete cases are marked `not_evaluated`, never passed. `--enforce-gate` exits with code 2 unless the gate passes; thresholds are configurable. Passing only applies to the supplied cases, not general quality or billing savings. See [the checked-in comparison report](docs/reports/repository-quality.md) for measured results and limits.

## Harness execution

The page can pass the processed task to Codex, Claude, or DSH; it preserves the original when there is no estimated saving. Code tasks prefer Codex; other tasks prefer Claude. Long and code tasks prefer the quality model.

```powershell
$body = @{task="Analyze this code"; content="print('hello')"; harness="auto"; model="auto"} | ConvertTo-Json
$session = Invoke-RestMethod http://127.0.0.1:8765/api/session
$headers = @{"X-TokenFlow-Token"=$session.token}
Invoke-RestMethod http://127.0.0.1:8765/api/execute `
  -Method Post -ContentType "application/json" -Headers $headers -Body $body
```

Codex uses read-only mode and Claude uses plan mode by default. DSH requires a configured `DSH_HOME` and profile.

To route a TokenFlow-launched Codex CLI through OmniRoute, set `harness: "codex"`, `harness_backend: "omniroute"` and an explicit allowed `model` on `/api/execute` (or use the page). `auto` is rejected. The default backend is `direct`. OmniRoute mode uses per-invocation `/v1/responses` overrides without editing global Codex files. The gateway controls upstream accounts, cost and fallback, and must already support the Responses API. This is not a Codex desktop-app setting. Direct API callers must obtain consent for possible remote transfer and charges.

## HTTP API

```text
GET  /health
GET  /api/harnesses
GET  /api/freetoken
GET  /api/omniroute
GET  /api/providers
GET  /api/tokenizer
GET  /api/store
GET  /api/session
GET  /api/local-models
POST /api/shutdown
POST /api/run
POST /api/local-chat
POST /api/chat
POST /api/execute
POST /api/parse
POST /api/index
POST /api/store/migrate
POST /api/search
POST /api/pc/plan
POST /api/pc/execute
POST /api/jev/decision
POST /api/freetoken/install
POST /api/freetoken/start
POST /api/omniroute/config
POST /api/omniroute/install
POST /api/omniroute/start
POST /api/omniroute/preview
```

All POST requests require the per-process token returned by `GET /api/session`; browser requests also require a matching loopback Origin. The OmniRoute setup endpoints additionally require a loopback client. The token reduces cross-site request risk but is not user authentication. Install and start endpoints control local processes. Do not forward them through a public reverse proxy.

## Security and privacy

Read [SECURITY.md](SECURITY.md) before deployment. Keep the server on `127.0.0.1`, never commit secrets or personal paths, treat Harness execution as code execution, and obtain third-party runtimes and models from official sources.

## Development

```powershell
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py omniroute_manager.py
python tokenflow.py --self-check
python eval_workflow.py --self-check
```

GitHub Actions runs the checks on Windows, builds the EXE, validates startup/status/preview/shutdown using `scripts/smoke_exe.ps1`, and uploads an artifact with its SHA256 file. Published releases attach the verified executable and checksum separately from source commits.

Before a pull request, scan the diff for secrets, absolute paths, generated logs, model files, credentials, and unintended subprocess changes. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Limitations

- Token estimates may be heuristic; even with an optional tokenizer, visible-prompt counts are not billable usage.
- Preprocessing is deterministic, structure-aware extraction, not semantic summarization; representative task quality still needs external validation.
- The local HTTP API has no user authentication, quotas, or multi-user isolation; its per-process write token only mitigates browser-originated cross-site requests.
- TokenFlow does not redistribute FreeToken or OmniRoute runtime files, model weights, or third-party Harnesses.
- Provider availability depends on user configuration and network access.
- PDF extraction requires the optional `pypdf` package; local model integrations do not imply model weights are included.
- The hashing-vector cache is deterministic retrieval infrastructure, not a trained embedding model.
- PC Agent execution requires an explicit environment switch and still needs human review.

## License

Released under the MIT License. See [LICENSE](LICENSE).


## Local OCR / 本地文字识别

PNG/JPEG/WebP/BMP use optional offline RapidOCR (Chinese/English, CPU). PDF extraction supplements blank pages and mixed pages with large image regions containing little extractable text using PyMuPDF and OCR, up to 30 pages per file. Existing text and new recognition lines are deduplicated; results include OCR pages, confidence and warnings. Small logos are skipped. This layout heuristic still requires checking the extracted content. Without PyMuPDF, text pages warn that image content was not checked; scans fail explicitly. OCR makes no cloud calls and does not reserve GPU memory. Optional dependencies: `rapidocr_onnxruntime==1.2.3`, `Pillow`, `PyMuPDF`. The build bundles RapidOCR configs and model files when installed.

分类摘要角色可显式选择云端 API，调用统计会按实际连接归类。可从本机 `BUPT_API_BASE_URL` / `BUPT_API_KEY` 导入北邮连接。DSH headless 通过临时补丁绑定模型；Codex 原生登录使用 HTTPS，模型目录可由 CLI `model/list` 检测，避免使用不可调用的应用缓存模型。
## Input fidelity, task recovery and full-pipeline evaluation

XLSX extraction preserves inline/shared strings, sparse cells, blank rows and workbook sheet order. Single sheets produce TSV; multiple sheets include sheet names. Formulas use saved cached values, or retain the formula with a warning when no value is available; formulas are not recalculated. DOCX paragraph boundaries are preserved.

Codex and Claude receive complete UTF-8 prompts on stdin. DSH headless receives a temporary `headless-runner` task overlay, removed after the call. Prompt length no longer consumes Windows' command-line allowance.

Explicit single-step questions, summaries and translations over short supplied content skip planning and normally use executor + reviewer. Complex work still plans; preprocessing summaries are conditional. Tasks reuse the content-hash document index and restrict retrieval to the current document, with source references and structure-preserving extraction. Broad tasks retain full content, and reviewers always receive the original source.

The page tracks the running task independently of the displayed history, restores tracking on refresh, and retries transient polling failures. `POST /api/tasks/cancel` accepts `{"id":"<task-id>"}` with the same loopback/session protection as task start. Cancellation prevents subsequent calls and stops Harness process trees. In-flight API requests settle before `cancelling` becomes `cancelled`; completed work is retained. Restarted unfinished tasks become `interrupted`.

To explicitly compare one raw-input call with the entire orchestration:

```powershell
python eval_workflow.py --pipeline --connection <saved-connection-id> --model <model-id> --cases eval_cases.workflow.json --min-cases 6 --report tmp/pipeline.json
```

All stages use the selected saved API connection and model, with 2400-token raw/executor output caps, role-specific auxiliary caps recorded in the report, and a 3–8 call budget (`--max-calls`, default 6). Harness execution is disabled for this comparison. Connection IDs appear in `/api/config` under `connections[].id`. Temporary databases isolate evaluation from saved strategies and history. Reports include every stage, repairs, input/output usage and wall time. Missing usage remains unknown; unsuccessful acceptance or incomplete usage cannot pass the gate. Full-pipeline gates check both input and total-token savings. The six source-independent synthetic cases are workflow smoke scenarios, not a held-out production benchmark; use independently labelled holdout tasks for quality claims. Full orchestration can cost more than one call.

Offline regression commands:

```powershell
python tokenflow.py --self-check
python eval_workflow.py --self-check
node scripts/test_ui.js
```

## Budgets, reusable parsing and storage controls

The console configures a 64000-token task allowance and 32768-token context allowance by default; output limits default to local 1024, planner 1536, executor 2400 and reviewer 1024. Before a stage, visible input is estimated and output is reserved. Completed/failed usage is charged where available, including truncated Responses/Messages responses; unknown usage reserves estimates. This is a workflow guard, not a bill or a hard limit on Harness internals. Set context capacity to match the actual model.

Task start accepts an optional `request_id`. Repeating an ID with the same payload returns the same run, including after completion/restart; changed payloads are rejected. `GET /api/tasks/request/<id>` restores a submission whose response was lost. The page retains receipt IDs and timestamps in localStorage, not source content. Deleted history keeps a small receipt so a stale request cannot execute again.

Application file imports reuse successful extraction by file bytes, extension, parser version and dependency versions, including OCR. Failed extraction is not cached. The storage panel displays logical content size, database file size, source/parse entries and history count, and offers explicit deletion and optional retention. Defaults of 0 retain everything. Configured limits prune old finished runs at task completion and least-recently-used caches during imports/task completion; saving a policy or pressing organize applies it immediately. SQLite deleted space is reusable; physical files may not immediately shrink. Original-source review remains intact even if a retention policy removes its cached copy.

## Prepare real task benchmarks locally

From a finished task, export an evaluation draft. It contains cached source and a **candidate** answer, with blank expected answer/facts and `label_status=draft`. Independently check the answer, fill `expected_answer`, `must_keep`, `category`, and set `label_status=verified`. Without that step, real-task evaluation is rejected. A source cache removed by retention must be supplied again.

```powershell
python scripts/prepare_eval_cases.py --template tmp/real-tasks/manifest.json
# Fill actual tasks and labels. source_file paths stay within the manifest directory;
# TXT, DOCX, XLSX, PDF and images use the existing local parser.
python scripts/prepare_eval_cases.py --manifest tmp/real-tasks/manifest.json --output tmp/real-tasks/prepared.json
# Offline fact retention; no model calls.
python eval_workflow.py --cases tmp/real-tasks/prepared.json --report tmp/real-offline.json
# Explicit model calls, only after choosing a saved connection/model:
python eval_workflow.py --pipeline --connection <id> --model <model> --cases tmp/real-tasks/prepared.json --min-cases 20 --report tmp/real-pipeline.json
```

Prepared cases are portable text snapshots, excluding candidate answers. Reports include category accuracy, full input/output totals and median latency. Draft templates and regression fixtures are not real benchmark results. Keep private manifests, source documents and reports outside Git; `tmp/` is ignored.

## Synthetic corpus and evidence checks

Generate 120 rule-labelled synthetic cases locally: 20 each for long documents, conflicts, tables, Python dependencies, instructions inside source material and file parsing. Development and holdout sets each contain 60 cases with different template families. Answers come from generation rules, not model responses. TXT, DOCX, XLSX, PDF and PNG fixtures use the existing optional dependencies; missing components are recorded in `generation.json` and exit with code 2. The output directory must not already exist.

```powershell
python scripts/generate_eval_cases.py --seed 20261013 --output-dir tmp/synthetic-eval
python eval_workflow.py --cases tmp/synthetic-eval/all.json --report tmp/synthetic-direct.json
python eval_workflow.py --cases tmp/synthetic-eval/all.json --offline-task-context --report tmp/synthetic-context.json
```

Both commands are offline. Task-context mode uses isolated databases and the actual indexing/retrieval/selection path, without executing tasks or calling models. `development.json` and `holdout.json` can be evaluated separately. These developer-designed templates are regression evidence, not production task logs or independent generalization proof. Offline checks leave model accuracy and reported usage unknown; their gate cannot pass. Retaining facts in instruction-interference cases does not prove a model's instruction safety.

Search results are previews (`sources_scope=search_preview`), and no longer exclude lexical matches elsewhere in the source. Python extraction includes statically resolvable module bindings/imports/helper functions; dynamic lookup, nested definitions, complex writes or oversized selections fall back to the source.

Evaluators and orchestration share complete-text validation: truncated, pending-tool, empty or malformed responses are not complete answer pairs; returned usage is retained. Reports separate source kinds and categories. Each source/category must meet the same accuracy/drop thresholds and at least 10 complete pairs (`--min-category-cases`); a failing category blocks the overall gate, while undersized groups remain not evaluated.

## Reliable statistics, review preflight and history queries

Table sums and medians use Decimal precision derived from the values and row count, with a 4096-digit/exponent safety bound. `mean_exact=false` identifies rounded means. Missing formula caches and mixed nonnumeric values produce partial aggregate results marked `needs_review`; row/column counts remain available. Formula values are never recalculated by TokenFlow, and parsing warnings travel with uploaded and path-based tasks.

Before the first model call, the workflow checks the minimum full-source review budget/context. Planning and later calls reserve review capacity again. Dynamic output, estimation errors and Harness internals retain their existing limits. The raw visible-prompt baseline uses the executor's message framing (`baseline_scope=executor_visible_prompt`); `metrics.by_role` records estimates and reported usage by purpose.

History supports task-text and status filters plus pagination independently of the active run. Protected `GET /api/tasks` accepts `limit` (1–100, default 20), `offset` (0–1000000), `query` (up to 200 characters, literal substring), and `status`. Responses include `total`, `offset`, `limit`, `has_more`; ID lookup stays compatible.

Full-pipeline evaluation supports native Ollama settings: thinking off, temperature 0, matching context and model for raw and all workflow calls. The explicit local Ollama protocol uses these settings in ordinary application calls too; legacy loopback Chat Completions profiles can opt into native evaluation without changing their saved protocol:

```powershell
python eval_workflow.py --pipeline --ollama-native --connection <local-ollama-id> --model qwen3.5:9b --ollama-context 32768 --cases tmp/synthetic-eval/holdout.json --report tmp/pipeline-ollama.json
```

Completed-pair accuracy excludes failed workflows: also inspect `accepted_correct_cases / cases`. `reported_all_raw_*`, `reported_pipeline_*` and `reported_all_*_token_saving_ratio` include failed-call costs and preserve unknown usage. See the [2026-10-15 validation report (Chinese)](docs/reports/reliability-2026-10-15.zh-CN.md).

## Structured acceptance and exact table lookups

Select **Ollama (local)** for loopback addresses ending in `/v1`. Daily tasks and evaluation share the native adapter, thinking off, temperature 0 and the configured context capacity. Planning and review request bounded JSON schemas; client checks still reject invalid or inconsistent verdicts. Advanced single calls retain their own output cap. Newly discovered Ollama connections use this protocol; existing saved profiles and Harness binding rules remain unchanged.

Review separates the candidate answer from source material and review metadata. One budgeted repair requires concrete quotes present in both source and candidate. Invalid review output, unsupported corrections or failed rechecks retain the answer for review. After a repair, the result panel can show both candidates and their verdicts. Valid structure and matching quotes do not establish semantic correctness.

Narrow explicit CSV/TSV queries can complete locally with zero model calls, for example `CSV表格中 "K 17" 的 free_stock 值是多少？只回答数字。`. The first column must identify one row and the requested column must be unique. Duplicate keys, missing columns, empty targets or uncached formulas need review. Ambiguous, multi-sheet or multi-step requests retain the existing workflow.

Pipeline reports include candidate history, first/final candidate correctness, repair improvement/degradation counts and stage error codes. Generate a separate 60-case synthetic validation family (ten cases per category) into a new directory:

```powershell
python scripts/generate_eval_cases.py --validation-only --seed 20261020 --output-dir tmp/acceptance-validation
python eval_workflow.py --cases tmp/acceptance-validation/validation.json --offline-task-context --report tmp/acceptance-context.json
```

Offline checks establish labelled-fact retention only. See the [2026-10-20 implementation and evaluation report (Chinese)](docs/reports/acceptance-2026-10-20.zh-CN.md) for both model comparisons and unchanged gates.

## Lifetime input savings and task progress

The home page highlights recorded net visible-input reductions and their reference dollar value using [GPT-6.1 Sol Standard pricing](https://developers.openai.com/api/docs/models/gpt-6.1-sol), checked on 2026-10-20: $2 per million uncached input tokens, or $4 for an entire request exceeding 272K input tokens. The original-input baseline and each workflow call are priced separately. Planning, execution, review, repair and known routing retries are deducted. Unsuccessful operations earn no positive baseline; negative totals display as extra input cost.

This is an **estimated input-only comparison, not an invoice**. Cached input ($0.10/M) and output ($10/M) are reference rates in the details; unknown counterfactual outputs, hidden Harness calls and local inference volume are not invented as savings. Incomplete comparisons are counted separately. A numeric-only ledger in `connections.db` survives restarts and history/cache cleanup. Stable operation IDs prevent duplicate credits. Recoverable retained tasks can be backfilled; preprocessing previews and isolated evaluation databases do not become personal usage. `GET /api/config/savings` uses the existing loopback/session protection.

Running tasks show the current stage, elapsed time, a spinner and an indeterminate bar. Cancellation, reconnection and terminal states remain distinct; no model completion percentage is fabricated. Refresh recovers elapsed time, phone layouts keep the main action on one row, and reduced-motion preferences disable animation.

## Edge/cloud collaboration

The workbench offers hybrid, local-only and configured-role policies, with a no-model dispatch preview before submission. Simplified connection controls assign preparation and complex work; advanced controls retain separate planner/executor/reviewer choices. Existing saved roles remain intact; older settings retain configured-role behavior until another policy is selected.

Plans support up to four sequential steps with `id`, `depends_on`, `input_scope` (`source`, `dependencies`, `both`) and `expected_output`. Each step receives its requested source and prerequisite outputs rather than every earlier result. Legacy plans remain readable. Final acceptance retains the original source; summaries are planning aids. Actual location, dependencies, duration, intermediate output and escalation appear in the result panel.

A failed local model step can escalate once to the configured complex executor when hybrid mode and the remaining call/token budget permit, while reserving acceptance. Failed calls and escalation count toward usage. Cloud failures preserve partial results without labelling them final completion. Local-only mode rejects cloud connections and native Harnesses; deterministic tasks still need no model. Location follows saved connection metadata; native execution and hidden usage remain explicitly uncertain.

`POST /api/tasks/preview` requires the existing loopback/session protection and makes no model calls. `collaboration_mode` participates in task receipt identity. Evaluation records step handoffs and actual locations; same-model evaluation keeps configured-role semantics. Run `python test_collaboration.py` for focused routing, handoff, budget, partial-result, local-only and protected-preview checks. Synthetic fixtures and real model results are reported separately; collaboration is not a guarantee of correctness or cost savings.
