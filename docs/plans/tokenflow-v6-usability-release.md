# TokenFlow usability fixes and first Windows release

## Requirement reading

Implement the four agreed usability fixes, rebuild the one-click Windows executable, and publish the source plus executable as the repository's first GitHub Release.

## Reuse survey

- Reusing: `pc_agent.py:plan_actions` - already validates the five allowed action types; the UI currently sends a hard-coded Enter example.
- Reusing: `TokenFlowHandler.do_POST` - already parses JSON and multipart uploads; it currently applies one 2 MB body limit to both.
- Reusing: `run_server` and `ThreadingHTTPServer` - provides the server lifecycle needed for an explicit local shutdown request.
- Reusing: `DocumentStore.status` - can report counts, but constructing the store currently initializes the database.
- Reusing: `build_tokenflow_exe.cmd` - builds a single-file windowed executable with PyInstaller.
- Gap needing new code: editable PC action preview, size-specific upload guard, shutdown route/UI, non-creating store status, and user-data database path.
- Existing checks: `python tokenflow.py --self-check`; PyInstaller build script. No separate test suite or dependency manifest exists.
- Release survey: no local tags. GitHub release listing was unavailable because the API network request was blocked; use the first release tag `v0.1.0` unless the remote reports a collision.

## Capability and file boundaries

- PC action preview:
  - `ui_page.py` - replace the fixed Enter demo with an editable JSON action input and render the validated dry-run.
  - `pc_agent.py` - unchanged; remains the action schema and validator.
- Upload and shutdown lifecycle:
  - `tokenflow.py` - retain 2 MB JSON limit, allow multipart up to 20 MB, and add a POST-only shutdown endpoint.
  - `ui_page.py` - add a visible close-runtime button and confirmation; show a clear stopped state.
  - `launcher.py` - unchanged; retains browser launch and server start.
- Database location and status:
  - `tokenflow_store.py` - choose a platform user-data directory by default, add non-initializing read-only status, and initialize only when indexing/searching.
  - `tokenflow.py` - use read-only status for startup cards.
- Documentation and distribution:
  - `README.md`, `README.zh-CN.md` - document file limits, database location policy, close action, PC preview, and Release download.
  - `build_tokenflow_exe.cmd` - unchanged unless the build reveals a required fix.
  - `dist/TokenFlow.exe` - generated Release artifact, excluded from source commits and uploaded to GitHub Release.
- Unchanged: provider APIs, model routing, Jev adapter, tokenizer implementation, and FreeToken engine management.

## Interfaces and invariants

- `POST /api/pc/plan` receives the user's JSON `actions` array and returns the existing validated dry-run contract; malformed input remains HTTP 400.
- `POST /api/shutdown` returns success, then stops the server from a separate thread so the response can flush.
- `POST /api/parse` and `/api/index` allow multipart bodies up to 20 MB; JSON requests remain capped at 2 MB.
- `GET /api/store` must not create directories or database files.
- Default database path is per-user application data; `TOKENFLOW_DB_PATH` continues to override it.
- PC Agent execution remains opt-in and is not added to the UI close/preview changes.

## Build order

1. Update storage and HTTP contracts, using current code paths and in-memory multipart fixtures for verification.
2. Wire the UI to the action preview, runtime shutdown, and read-only status endpoints.
3. Update bilingual documentation, rebuild the single-file EXE, commit source, and publish `v0.1.0` with the EXE asset.

## Validation

- Confirm the application compiles and its existing self-check passes.
- Confirm multipart requests at the boundary are accepted/rejected correctly, while JSON remains capped at 2 MB.
- Confirm `GET /api/store` leaves the database absent until a write operation.
- Confirm the PC action editor submits user-entered JSON and renders validation errors.
- Confirm the shutdown button stops the packaged server after returning its response.
- Confirm the rebuilt EXE starts and serves the new UI before attaching it to the Release.
- Scan the staged source/docs for personal paths and credentials before commit.

## Open questions

- GitHub Release API availability must be rechecked during publishing; the current API listing request was blocked by network policy.

## Facts vs assumptions

- Verified: current branch is `master`, aligned with `origin/master`, and has no local tags.
- Verified: `dist/TokenFlow.exe` is ignored by Git and was previously built as a 35 MB single-file executable.
- Assumed: `v0.1.0` is an appropriate first public binary release because no local tags exist.
