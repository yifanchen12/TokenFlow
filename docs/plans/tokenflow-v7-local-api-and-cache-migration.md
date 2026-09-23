# Local API write protection and legacy cache import

## Requirement reading

Implement the two approved follow-ups: prevent browser-originated cross-site requests from triggering local write/process actions, and give users a safe way to import the SQLite cache created by earlier TokenFlow versions after the default database location changed.

## Reuse survey

- Reusing: `TokenFlowHandler.do_POST` - central entry point for every mutating API; currently has no request token or Origin validation.
- Reusing: `TokenFlowHandler._send_json` and the served `PAGE` - existing JSON/page delivery can expose an in-memory per-process request token to the same-origin UI.
- Reusing: `DocumentStore.status`, `_connect`, and `index` - current schema/status and content-hash deduplication support safe import without replacing the destination DB.
- Reusing: `ThreadingHTTPServer` - no new server/runtime dependency is needed.
- Verified legacy format: commit `7841f5e` defaulted to `Path("tokenflow.db")`, relative to the process working directory; current default is per-user application data.
- Gap needing new code: same-origin write-token contract, old-cache discovery/import, visible user confirmation, and bilingual API/security documentation.
- Existing checks: `python tokenflow.py --self-check`; `python -m py_compile ...`; Windows build via PyInstaller command in `build_tokenflow_exe.cmd`.

## Capability and file boundaries

- Local browser write protection:
  - `tokenflow.py` - create an unpredictable token per process, serve it through a no-store `GET /api/session`, inject it into the local UI response, and require it on every POST. If an `Origin` header is present, accept only an HTTP loopback origin matching the request Host and server port. Reject missing/invalid token and invalid Origin with HTTP 403 before any route side effect.
  - `ui_page.py` - attach the token header to JSON and multipart POST requests.
  - `README.md`, `README.zh-CN.md`, `SECURITY.md`, `SECURITY.zh-CN.md` - explain browser CSRF protection and the session-token handshake for CLI clients; explicitly state this is not user authentication and does not make LAN exposure safe.
- Legacy cache import:
  - `tokenflow_store.py` - detect only the old `tokenflow.db` locations (current working directory, source directory, and packaged executable directory when frozen), report availability without creating a DB, and import documents using the existing content-hash deduplication. Read source DBs read-only and never delete or overwrite them.
  - `tokenflow.py` - expose read-only availability through `GET /api/store` and a token-protected `POST /api/store/migrate`; return a bounded count/result and safe error messages.
  - `ui_page.py` - show an import button only when a legacy DB is detected, request explicit confirmation, then display completion/error.
  - bilingual READMEs - explain when the prompt appears and that the source DB remains untouched.
- Unchanged: provider adapters, Harness execution policy, PC Agent allow-list, FreeToken installer behavior, database schema, and the v0.1.0 release artifact.

## Interfaces and invariants

- `GET /api/session` -> `{"token": <random process token>}` with `Cache-Control: no-store`; caller is the same-origin UI or a local API client.
- Every `POST /api/*` must send `X-TokenFlow-Token`; absent or invalid tokens return 403 before any action. A supplied `Origin` must be HTTP, loopback, and match the request Host and bound server port. Requests without Origin remain available to local non-browser clients only with the token.
- `GET /api/store` includes `legacy_database_available: boolean`; it remains read-only and does not initialize either database.
- `POST /api/store/migrate` imports documents from detected legacy DB(s), deduplicates by existing content hash, leaves all source files intact, and never replaces the current database. Repeating the import is safe.
- If a legacy file is not a valid TokenFlow database, return a generic actionable error without exposing its absolute path or raw SQLite exception.

## Build order

1. Add and self-check pure request-validation rules and read-only legacy candidate/import helpers.
2. Assemble the session handshake and migration API; verify API calls with valid/invalid token and import into a temporary database.
3. Wire the UI prompt and token header; update bilingual security/API documentation; run self-check, compilation, and packaged Windows smoke check.

## Validation

- `python tokenflow.py --self-check` covers accepted loopback origin/token, rejected foreign origin/token, read-only legacy detection, successful import, deduplication, and preservation of source DB.
- Live HTTP smoke check confirms POST without token and with foreign Origin return 403 without side effects; same-origin token succeeds.
- `GET /api/store` does not create target or legacy DB files; import is repeatable and leaves source DB intact.
- Package and start the Windows executable; confirm the UI detects and imports a temporary legacy fixture.
- Scan changes for credentials and local absolute paths; `git diff --check`.

## Open questions

- None. The legacy source is discovered only in well-defined old working/application locations; no arbitrary file-picker or path-accepting import API is added.

## Facts vs assumptions

- Verified: the prior database default was relative `tokenflow.db`; the current default is per-user application data; the UI sends all POST calls from one file.
- Assumed: earlier TokenFlow database files use the current `documents`/`chunks` schema. Import reports an actionable error and preserves the source if the schema differs.
