# TokenFlow

**A local, low-token execution framework for AI Agent workflows.**

TokenFlow reduces unnecessary context before a task reaches a local model or an AI coding Harness. The core is intentionally small: Python standard library, deterministic preprocessing, a local HTTP UI, and optional adapters for FreeToken, Codex, Claude, and DSH.

Chinese documentation: [README.zh-CN.md](README.zh-CN.md).

## Highlights

- Classifies tasks as code, table, document, or general.
- Compresses long text with a deterministic head-tail policy.
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

## Architecture

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
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py
python tokenflow.py --self-check
python tokenflow.py
```

Open <http://127.0.0.1:8765>. On Windows, double-click `start_tokenflow.cmd`.

### Windows executable

Download the ready-to-run [Windows executable from the latest GitHub Release](https://github.com/yifanchen12/TokenFlow/releases/latest/download/TokenFlow.exe), or run `build_tokenflow_exe.cmd` to build `dist/TokenFlow.exe` yourself. The executable starts the local server and opens the TokenFlow console in the default browser. Use **Close service** in the page to stop it. Generated binaries stay out of source commits and are distributed as release assets.

The SQLite cache is stored in the current user's application-data directory by default (for example, `%LOCALAPPDATA%\TokenFlow\tokenflow.db` on Windows). Set `TOKENFLOW_DB_PATH` to use a different location. Opening the status page does not create the database; it is initialized when documents are indexed.

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

`POST /api/chat` accepts `provider: "auto" | "freetoken" | "ollama" | "laya" | "cloud"`. Automatic routing follows `TOKENFLOW_PROVIDER_ORDER`, which defaults to `freetoken,ollama,laya,cloud`.

Configure providers with environment variables:

```text
TOKENFLOW_FREETOKEN_URL       Local FreeToken endpoint; default http://127.0.0.1:1919
TOKENFLOW_OLLAMA_URL          Ollama OpenAI-compatible endpoint; default http://127.0.0.1:11434/v1
TOKENFLOW_LAYA_URL            User-supplied Laya-compatible endpoint
TOKENFLOW_LAYA_API_KEY        Optional Laya key, read only from the environment
TOKENFLOW_CLOUD_URL           User-supplied cloud OpenAI-compatible endpoint
TOKENFLOW_CLOUD_API_KEY       Cloud key, read only from the environment
TOKENFLOW_PROVIDER_ORDER      Comma-separated provider order
```

TokenFlow never prints or stores provider keys. Laya has no assumed public endpoint in this repository; configure the endpoint supplied by the deployment. Jev is exposed separately through `POST /api/jev/decision` with `JEV_API_KEY` and optional `TOKENFLOW_JEV_URL`. It is an explicit typed-decision call and is never invoked silently as a general chat model.

## Document parsing and cache

`POST /api/parse` accepts JSON with a local `path` or a `multipart/form-data` upload. Supported formats are TXT, Markdown, common source/text files, DOCX, XLSX, and PDF when `pypdf` is installed.

JSON requests are limited to 2 MB. Uploaded files are limited to 20 MB per file.

`POST /api/index` stores extracted text in SQLite and creates a deterministic hashing-vector cache. `POST /api/search` retrieves the highest-scoring chunks. This is a local lexical/vector cache, not a claim of embedding-model semantic quality.

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

## Harness execution

The page can pass the compressed task to Codex, Claude, or DSH. Code tasks prefer Codex; other tasks prefer Claude. Long and code tasks prefer the quality model.

```powershell
$body = @{task="Analyze this code"; content="print('hello')"; harness="auto"; model="auto"} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:8765/api/execute `
  -Method Post -ContentType "application/json" -Body $body
```

Codex uses read-only mode and Claude uses plan mode by default. DSH requires a configured `DSH_HOME` and profile.

## HTTP API

```text
GET  /health
GET  /api/harnesses
GET  /api/freetoken
GET  /api/providers
GET  /api/tokenizer
GET  /api/store
GET  /api/local-models
POST /api/shutdown
POST /api/run
POST /api/local-chat
POST /api/chat
POST /api/execute
POST /api/parse
POST /api/index
POST /api/search
POST /api/pc/plan
POST /api/pc/execute
POST /api/jev/decision
POST /api/freetoken/install
POST /api/freetoken/start
```

The install and start endpoints control local processes. Do not forward them through a public reverse proxy.

## Security and privacy

Read [SECURITY.md](SECURITY.md) before deployment. Keep the server on `127.0.0.1`, never commit secrets or personal paths, treat Harness execution as code execution, and obtain third-party runtimes and models from official sources.

## Development

```powershell
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py
python tokenflow.py --self-check
```

Before a pull request, scan the diff for secrets, absolute paths, generated logs, model files, credentials, and unintended subprocess changes. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Limitations

- Token estimates are heuristic and model-independent.
- Preprocessing is deterministic cleaning and head-tail extraction, not semantic summarization.
- The local HTTP API has no authentication, quotas, or multi-user isolation.
- TokenFlow does not redistribute FreeToken runtime files, model weights, or third-party Harnesses.
- Provider availability depends on user configuration and network access.
- PDF extraction requires the optional `pypdf` package; local model integrations do not imply model weights are included.
- The hashing-vector cache is deterministic retrieval infrastructure, not a trained embedding model.
- PC Agent execution requires an explicit environment switch and still needs human review.

## License

Released under the MIT License. See [LICENSE](LICENSE).
