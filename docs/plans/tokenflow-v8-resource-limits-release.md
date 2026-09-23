# TokenFlow resource limits and Windows release

## Requirement reading

Implement the three approved improvements: bound DOCX/XLSX decompression, bound memory used by local harness output, and publish an updated Windows executable so the latest Release matches the current source.

## Reuse survey

- Reusing: `document_parser.py` centralizes all file and upload parsing; its current 20 MB input limit does not limit ZIP expansion.
- Reusing: `tokenflow.py:execute_harness` owns subprocess timeout and output formatting; `_clip_output` limits only the returned text.
- Reusing: `document_parser.py:self_check`, `tokenflow.py:self_check`, and `build_tokenflow_exe.cmd` provide the existing verification/build path.
- Reusing: Python `zipfile`, `subprocess`, and `threading`; no new dependency or public API is needed.
- Gap: decompression and subprocess output must be bounded while data is read, not only after the work finishes.
- Callers: `parse_document_bytes` serves `/api/parse` and `/api/index`; `execute_harness` serves `execute_workflow`.
- Build/test: `python tokenflow.py --self-check`; `python -m py_compile`; PyInstaller command from `build_tokenflow_exe.cmd`.

## Capability and file boundaries

- Document expansion limits: `document_parser.py` validates ZIP entry count, XML member size, and cumulative decompressed XML size before parsing; its self-check covers an ordinary document and an oversized member.
- Harness output limits: `tokenflow.py` drains both output streams while retaining only a bounded prefix; its self-check covers large output and timeout without changing the result keys.
- Release: `dist/TokenFlow.exe` is generated locally and attached to a new GitHub Release with a SHA-256 checksum; the source and this plan are committed and pushed. `build_tokenflow_exe.cmd` remains unchanged unless the observed build requires a correction.
- Unchanged: `ui_page.py`, provider adapters, routing policy, `tokenflow_store.py`, API routes, and bilingual READMEs (their latest-release links already apply).

## Interfaces and invariants

- `parse_document_bytes(name, data) -> dict`: existing successful result shape remains unchanged; oversized or malformed DOCX/XLSX raises `DocumentParseError` with a user-facing message.
- `execute_harness(harness, model, prompt) -> dict`: same result fields, output/error truncation marker, and 90-second timeout; even excessive child output does not grow retained data beyond the configured cap.
- Release artifact is built from the pushed commit; the latest-release download link resolves to that artifact.

## Build order

1. Add document limits and focused self-check fixtures, then verify parsing independently.
2. Add bounded harness capture and focused self-check, then verify its result contract.
3. Run assembled self-check and compile; build and smoke-test the executable; commit, push, publish a new Release, and verify its attached artifact/checksum.

## Validation

- Normal DOCX/XLSX and over-limit ZIP fixtures.
- Normal/large-output and timeout harness fixtures; existing output shape unchanged.
- Full `--self-check`, compilation, executable startup, clean diff, and release asset/hash inspection.

## Open questions

- None. Use conservative fixed limits; legitimate files exceeding them receive an explicit error rather than risking resource exhaustion.

## Facts vs assumptions

- Verified: current source HEAD is `a3363d7`, the latest public Release is still `v0.1.0`, and both READMEs link to `/releases/latest/download/TokenFlow.exe`.
- Assumed: `v0.2.0` is the next suitable release tag; check for a collision immediately before publication.
