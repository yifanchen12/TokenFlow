# Security Policy

## Scope

This policy covers the TokenFlow source code and its default local HTTP server. It does not automatically cover FreeToken, Codex, Claude, DSH, model weights, operating systems, GPU drivers, or third-party installers.

TokenFlow is a local developer tool, not a multi-tenant service. Its default trust boundary is the current user account and the loopback interface.

## Security-sensitive operations

The following endpoints and capabilities can cause local side effects:

1. `POST /api/execute` can invoke an installed local Harness.
2. `POST /api/freetoken/install` can open a local installer.
3. `POST /api/freetoken/start` can start a local model server.
4. Harnesses may read files, call tools, or execute commands according to their own configuration.

## Required deployment rules

- Keep TokenFlow bound to `127.0.0.1`.
- Do not use `--host 0.0.0.0` on an untrusted network.
- Do not expose the server through port forwarding, a public reverse proxy, or a shared LAN without authentication, authorization, request limits, and audit logging.
- Do not send secrets in task content. Treat prompts, documents, source code, and Harness output as sensitive data.
- Use Codex read-only and Claude plan mode unless a human explicitly approves a change.
- Review every command, file modification, or external side effect before accepting it.
- Verify installer provenance and signatures where available. Do not disable antivirus or execution policy to bypass a warning.

## Secret handling

Never commit API keys, OAuth tokens, cookies, SSH keys, certificates, local environment files, private model repository credentials, or personal absolute paths.

If a secret is exposed:

1. Revoke or rotate it immediately.
2. Remove it from the working tree and prevent reintroduction.
3. Preserve only the minimum evidence needed for investigation.
4. Report the incident privately.

## Vulnerability reporting

Do not open a public issue for an unpatched vulnerability or publish proof-of-concept details first. Use the repository's **Security → Advisories → Report a vulnerability** workflow. If that workflow is unavailable, open a minimal issue asking for a private reporting channel without including exploit details.

Please include:

- affected commit, tag, or version;
- operating system and Python version;
- exact reproduction steps with secrets and personal data removed;
- impact and realistic attack prerequisites;
- a suggested mitigation, if known.

Maintainers should acknowledge a valid report within 7 days, provide a severity assessment or mitigation plan within 14 days, and coordinate disclosure after a fix is available. These are targets, not a guarantee.

## Supply-chain boundaries

TokenFlow does not redistribute FreeToken runtime wheels, model weights, Codex, Claude, DSH, or installer binaries. Users must obtain those artifacts from official sources and comply with their licenses and terms.

The one-click Windows helper only locates a user-provided installer and opens it for explicit confirmation. It is not a signature verifier and does not silently install software.

Chinese version: [SECURITY.zh-CN.md](SECURITY.zh-CN.md).
