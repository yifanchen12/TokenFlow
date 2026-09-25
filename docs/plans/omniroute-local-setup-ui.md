# OmniRoute local setup UI

## Requirement reading

Expose user-triggered OmniRoute installation, local startup, and gateway URL/Endpoint Key setup in TokenFlow's existing page. The user approved a visible `npm install -g omniroute` action after confirmation and a Key that lasts only for the current TokenFlow run. Update both language READMEs and security notes, verify, build the Windows EXE, then push to GitHub. Keep OmniRoute opt-in for model calls.

## Reuse survey

- `freetoken_manager.py`, `tokenflow.py`, and `ui_page.py` already provide status/action endpoints, same-origin session-token checks, and comparable FreeToken controls. FreeToken's button opens an existing installer; it does not download silently.
- `model_providers.py` already validates OmniRoute URLs and routes explicit chat calls. `tokenflow.py` already configures Codex per invocation without changing global Codex settings.
- No project dependency manifest or test framework is present. Use Python standard-library `shutil`, `subprocess`, `urllib`, and in-memory state. Upstream documents `npm install -g omniroute` and the loopback dashboard/API on port 20128.
- Existing checks: `python tokenflow.py --self-check`, `python eval_workflow.py --self-check`, Python compilation, and the Windows PyInstaller build script.

## Capability and file boundaries

- Session configuration: `model_providers.py` owns an atomic URL/Key snapshot and its existing URL validation; chat clients capture a matching pair. `tokenflow.py` passes the Key only to an explicitly OmniRoute-backed Codex child, never in command arguments or unrelated child environments.
- Local lifecycle: new `omniroute_manager.py` reports installed/running/npm status, launches a user-confirmed visible npm installer on Windows, and starts only the default local gateway bound to `127.0.0.1`. It does not install Node, silently update OmniRoute, or manage remote gateways.
- HTTP/UI: `tokenflow.py` adds `GET /api/omniroute` and session-protected, loopback-client-only `POST /api/omniroute/config`, `/install`, and `/start`; `ui_page.py` adds status, URL/password inputs, and explicit buttons plus the upstream guide link. GET and POST responses never contain the Key.
- Documentation/build: `README.md`, `README.zh-CN.md`, `SECURITY.md`, `SECURITY.zh-CN.md`, and `build_tokenflow_exe.cmd` describe the actual installer/session behavior and compile the new module.
- Unchanged: FreeToken modules, database, document parser, default model auto-routing, global Codex configuration, and other providers.

## Interfaces and invariants

- `configure_omniroute(url, key=None)` validates the existing local/remote URL contract, rejects oversized inputs, and replaces the in-memory snapshot; omitted/blank Key keeps the current Key. No disk, user environment, browser storage, or response persistence.
- `omniroute_status() -> dict` returns only non-secret installation/running/configuration booleans and the validated URL. Health checks probe only loopback.
- `install_omniroute()` launches a fixed npm package command only after an explicit page confirmation; missing npm and non-Windows platforms return actionable errors or the official guide, not silent fallback.
- `start_omniroute()` starts the default local gateway only when the CLI is installed; the child is bound to loopback and receives no session Key.
- Existing `/api/chat` still excludes OmniRoute from automatic routing. Only explicit OmniRoute Codex invocations receive the session Key via child environment.

## Build order

1. Implement and self-check the provider session snapshot and independent local lifecycle module using mocks; no actual npm install during tests.
2. Assemble the URL/Key snapshot with chat and Codex child environment; verify no Key appears in args, responses, or unrelated children.
3. Wire guarded HTTP endpoints and page controls; update bilingual/security docs, rebuild and smoke-test the EXE, then commit and push.

## Validation

- Focused checks: missing npm, installed/running states, local-only startup, invalid/remote URL handling, same-origin/session/loopback guards, Key non-disclosure, and explicit-only routing.
- Run repository self-checks, compilation, JavaScript syntax check, HTTP smoke test, `git diff --check`, and EXE start/exit check. Do not actually reinstall the already installed OmniRoute instance as part of automated tests.

## Facts vs assumptions

- Verified: existing FreeToken UI/action pattern, active OmniRoute CLI on this Windows machine, and upstream npm/port instructions. The browser Key input is approved as run-scoped.
- Assumed: users without Node/npm will follow the linked upstream setup guide; automated npm installation is Windows-only in this first interface.
