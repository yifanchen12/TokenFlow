# Explicit OmniRoute integration

## Goal and acceptance

Add two opt-in routes: `/api/chat` can name `omniroute`, and `/api/execute` can launch a Codex CLI subprocess with a per-invocation OmniRoute Responses provider. Existing defaults, global Codex configuration, and unrelated pending compression edits stay unchanged. No OmniRoute installation or bundled runtime is included.

## Reuse and boundaries

- Reuse `model_providers.py`'s OpenAI-compatible client for `/v1/models` and `/v1/chat/completions`; add a provider specification and explicit-only auto-routing rule.
- Live-installation correction: `/v1/models` returned a broad catalog but omitted the working local `ollama-local/qwen3.5:9b` ID. In `unified_chat`, an explicitly requested OmniRoute model must be sent to the gateway without a catalog-membership check; keep the existing model-name validation in `OpenAICompatibleProvider.chat` and keep membership checks for other providers. No new client or route is needed.
- Live-call correction: the existing 20-second provider timeout expired during a local 9B generation. Give only the explicitly selected OmniRoute client a longer bounded timeout (90 seconds); retain 20 seconds for other providers and the 2-second status probe. No background job, retry loop, or new config knob is needed.
- Reuse `tokenflow.py`'s bounded, shell-free harness launcher; add one opt-in backend value and `-c` CLI overrides for Codex's Responses provider. Read the gateway key from an environment variable; never pass its value as a CLI argument.
- Use `ui_page.py` for explicit selection and egress confirmation. Update both language READMEs and security policies.
- Do not modify FreeToken installation, Claude/DSH launch rules, database, global Codex files, or the existing compression work.

## Contract and safety

- `provider='omniroute'` is explicit only, even if `TOKENFLOW_PROVIDER_ORDER` contains it and the gateway URL is loopback. OmniRoute may forward data to remote or paid models.
- `harness_backend='direct'|'omniroute'` defaults to `direct` and applies only with `harness='codex'`. For OmniRoute, `model='auto'` is passed to the gateway; a named model is passed unchanged.
- The gateway base URL defaults to `http://127.0.0.1:20128/v1`. A configured remote gateway must use HTTPS and a key. URL credentials, query, and fragment are disallowed. The installed OmniRoute instance returned 401 without an Endpoint Key, which the user created and saved outside the repository; some other local deployments may permit keyless access.
- Per-invocation Codex config uses `model_provider`, `model_providers.omniroute.base_url`, and `wire_api='responses'`; `env_key` is configured only when the environment contains a gateway key. No global Codex settings are written.
- Browser execution asks for confirmation when the OmniRoute harness backend is selected. Direct API clients are responsible for consent.

## Build order and verification

1. Add and self-check provider URL validation, explicit chat routing, and auto exclusion.
2. Add and self-check Codex argument construction and backend validation, then wire `/api/execute`.
3. Add UI choices/confirmation and bilingual configuration/security documentation.
4. Run Python compilation, existing self-checks, mock-gateway HTTP integration checks, UI-script syntax check when tooling is available, and diff hygiene. Live OmniRoute/Codex end-to-end checks require a running gateway and configured models, so do not claim them if unavailable.
5. Recheck the live provider path with the explicit local Ollama model ID even though it is absent from `/v1/models`; auto selection continues to use the returned catalog, and no automatic OmniRoute routing is added.
6. Recheck the live generation with the bounded OmniRoute-specific timeout; report separately whether the gateway returned a final assistant answer, since an HTTP 200 with `finish_reason=length` and empty content is not an answer-quality pass.

## Assumption

The gateway accepts OpenAI-compatible `/v1/chat/completions` and `/v1/responses` on the same base URL. This is supported by upstream documentation but must be verified against the user's actual installation before relying on a specific provider or quota.

Verified during live setup: `/api/health` and authenticated `/v1/models` respond; `ollama-local/qwen3.5:9b` returns HTTP 200 for a safe test prompt, but the short output budget ended with `finish_reason=length` and no final text. The model catalog is not a complete allow-list for explicit models. No claim of answer quality or Codex Responses compatibility follows from that chat test.
