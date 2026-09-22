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
GET  /api/local-models
POST /api/run
POST /api/local-chat
POST /api/execute
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
- Laya, Jev, Ollama, SQLite, and vector caching are not included in this MVP.

## License

Released under the MIT License. See [LICENSE](LICENSE).
