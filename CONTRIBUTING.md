# Contributing

Thank you for helping improve TokenFlow. Please keep changes small, reviewable, and safe for local execution.

Chinese guide: [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md).

## Before you start

- Read [README.md](README.md) and [SECURITY.md](SECURITY.md).
- Do not include API keys, cookies, credentials, personal paths, model weights, installers, logs, or virtual environments.
- Report security issues through the private process in `SECURITY.md`, not a public issue.

## Development checks

```powershell
python -m py_compile tokenflow.py freetoken_provider.py freetoken_manager.py
python tokenflow.py --self-check
git diff --check
```

Run these checks before every pull request. If a change affects subprocess execution, HTTP routes, installer discovery, or path handling, add a focused test or describe the manual verification.

## Pull requests

- Explain user-visible behavior and security impact.
- Prefer the Python standard library and the smallest implementation that works.
- Keep local process execution explicit and avoid shell interpolation.
- Preserve loopback binding, read-only/plan Harness modes, and no silent installer execution.
- Update documentation when behavior changes.
- Keep commits focused and use a clear message, such as `feat: add FreeToken lifecycle controls`.

## License

By contributing, you agree that your contribution is provided under this repository's MIT License.
