# Repository quality baseline — v0.4.0

[中文报告](repository-quality.zh-CN.md) · [Per-case results](repository-quality.json) · [Dataset](../../eval_cases.repository.json)

## Scope and method

Executed on 2026-10-04 with local Ollama `qwen3.5:9b`. The dataset contains 60 curated, source-grounded factual questions over 10 public repository files. It contains no private user documents or production task logs. Each question has an explicit answer and annotated facts present in its source.

The raw source and the prompt actually selected by TokenFlow were submitted to the same model. Requests used the native Ollama chat endpoint with a 32,768-token context, temperature 0, thinking disabled and a maximum of 128 output tokens. Raw/processed request order alternated between cases. Answers were checked by normalized exact matching, including explicitly enumerated alternatives. Numeric substring matches do not count as correct answers.

Every recorded source hash was verified against the final tested files. The largest reported input plus the output allowance fit within the configured context. Input and total counts below are reported by Ollama; they are separate from TokenFlow's prompt-size estimates and are not billable-cost measurements.

## Results

| Metric | Raw | Selected TokenFlow prompt |
|---|---:|---:|
| Completed paired cases | 60 | 60 |
| Correct answers | 60/60 (100%) | 60/60 (100%) |
| Cases retaining all annotated facts | 60/60 | 60/60 |
| Reported input tokens | 184,031 | 135,866 |
| Reported total tokens | 184,296 | 136,131 |
| Median request latency | 2,211 ms | 1,172 ms |

Input-token reduction: **26.17%** across all 60 cases. Compression was selected in 30 cases; the other 30 retained the original input. The benchmark gate passed: at least 50 completed pairs, zero annotated-fact losses, processed accuracy at least 90%, accuracy drop no greater than 2 percentage points, and at least 5% reported input-token savings. Missing usage or incomplete pairs cannot pass.

The first offline run found 11 cases with annotated-fact loss. Expanding matching lines to complete Python functions, Markdown sections or prose paragraphs, with original-input fallback when the budget is exceeded, reduced that count to zero in the final offline run. This before/after observation concerns fact retention, not a measured before/after model-accuracy improvement.

## Interpretation and limitations

This run demonstrates lower reported input usage without lost accuracy **on these repository QA cases only**. It does not establish representative production quality, improvement on arbitrary documents, or a causal accuracy advantage over the previous version. The questions are mostly short, checkable facts; complex reasoning, OCR, long-document synthesis and contradictory evidence are not covered. The corpus helped guide the extraction fix and is not a held-out generalization set.

Latency is descriptive, not a controlled performance claim: model caches were warm, request order alternated, and EXE packaging ran concurrently during part of the test. No monetary savings or hosted-provider behavior were measured. The hashing retrieval check and database migration checks are separate from this answer benchmark.

Future domain evaluations should use independently annotated, held-out tasks and the same-model paired protocol. Reports can contain model answers and document facts; review them before publication.

## Reproduction

Configure `TOKENFLOW_OLLAMA_URL` for the local Ollama OpenAI-compatible endpoint, then run:

```powershell
python eval_workflow.py --cases eval_cases.repository.json --provider ollama --model qwen3.5:9b --max-tokens 128 --ollama-context 32768 --report tmp/repository-model.json --enforce-gate
```

This issues 120 model requests. Re-running against a different source revision, model build or runtime may yield different results. The published JSON contains per-case source SHA-256 values, both answers, usage, latency, scoring results and gate thresholds; it intentionally omits private gateway addresses and credentials.
