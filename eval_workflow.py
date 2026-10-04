"""Opt-in, same-model quality and usage comparison over selected prompts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import time
import unicodedata
from pathlib import Path
from typing import Any, Callable

from model_providers import OpenAICompatibleProvider, PROVIDERS, ProviderError, chat_messages, choose_model, omniroute_model, reported_usage, response_text
from tokenflow import run_workflow


def sample_cases() -> list[dict[str, Any]]:
    filler = "这段背景与问题无关，仅供测试长上下文保留情况。\n" * 130
    return [
        {"name": "document", "task": "退款期限是几天？只回答数字。", "content": filler + "关键事实：退款期限为7天。\n" + filler,
         "must_keep": ["退款期限为7天"], "expected_answer": "7", "source_kind": "synthetic"},
        {"name": "code", "task": "代码中 ANSWER 的值是多少？只回答数字。", "content": ("# 无关代码注释\n" * 240) + "ANSWER = 42\n" + ("# 无关代码注释\n" * 240),
         "must_keep": ["ANSWER = 42"], "expected_answer": "42", "source_kind": "synthetic"},
        {"name": "table", "task": "表格中 A17 行的数量是多少？只回答数字。", "content": ("B01,0\n" * 400) + "A17,42\n" + ("B02,0\n" * 400),
         "must_keep": ["A17,42"], "expected_answer": "42", "source_kind": "synthetic"},
    ]


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError("评测样本顶层必须是非空数组")
    root = path.resolve().parent
    loaded = []
    for value in cases:
        if not isinstance(value, dict):
            raise ValueError("每个评测样本必须是对象")
        case = dict(value)
        if "source_file" in case:
            relative = case["source_file"]
            if not isinstance(relative, str) or Path(relative).is_absolute():
                raise ValueError("source_file 必须是样本目录内的相对文件路径")
            source = (root / relative).resolve()
            if not source.is_relative_to(root) or not source.is_file() or source.stat().st_size > 2_000_000:
                raise ValueError("source_file 不存在、超出样本目录或超过 2 MB")
            case["content"] = source.read_text(encoding="utf-8")
        loaded.append(case)
    return loaded


def _validate_case(case: Any, model_answers: bool) -> None:
    if not isinstance(case, dict) or not all(isinstance(case.get(key), str) and case[key].strip() for key in ("task", "content")):
        raise ValueError("评测样本需要非空 task、content 字符串")
    facts = case.get("must_keep")
    if not isinstance(facts, list) or not facts or not all(isinstance(fact, str) and fact for fact in facts):
        raise ValueError("评测样本需要非空 must_keep 字符串数组")
    if any(fact not in case["content"] for fact in facts):
        raise ValueError("must_keep 的事实必须存在于原文中")
    if case.get("answer_match", "exact") not in {"exact", "contains_all"}:
        raise ValueError("answer_match 只能为 exact 或 contains_all")
    if model_answers:
        expected = case.get("expected_answer")
        values = [expected] if isinstance(expected, str) else expected
        if not isinstance(values, list) or not values or not all(isinstance(value, str) and _normalize(value) for value in values):
            raise ValueError("模型评测需要 expected_answer 字符串或非空字符串数组")


def _normalize(answer: str) -> str:
    return unicodedata.normalize("NFKC", answer).strip().strip(chr(96) + "\"' \n\r").rstrip("。.").strip()


def answer_pass(answer: str, expected: str | list[str], method: str = "exact") -> bool:
    answer = _normalize(answer)
    values = [_normalize(value) for value in ([expected] if isinstance(expected, str) else expected)]
    if method == "exact":
        return answer in values
    return all(re.search(rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])", answer, re.I)
               if value.isascii() else value in answer for value in values)


def summarize(rows: list[dict[str, Any]], model_answers: bool, min_cases: int, max_accuracy_drop: float,
              min_saving: float, min_accuracy: float = .9) -> tuple[dict[str, Any], dict[str, Any]]:
    complete = [row for row in rows if "raw_answer_pass" in row and "processed_answer_pass" in row]
    usage_complete = bool(complete) and all("prompt_tokens" in row[label + "_usage"] for row in complete for label in ("raw", "processed"))
    total_complete = usage_complete and all("total_tokens" in row[label + "_usage"] for row in complete for label in ("raw", "processed"))
    raw_tokens = sum(row["raw_usage"]["prompt_tokens"] for row in complete) if usage_complete else None
    processed_tokens = sum(row["processed_usage"]["prompt_tokens"] for row in complete) if usage_complete else None
    saving = round(1 - processed_tokens / raw_tokens, 4) if raw_tokens else None
    raw_accuracy = sum(row["raw_answer_pass"] for row in complete) / len(complete) if complete else None
    processed_accuracy = sum(row["processed_answer_pass"] for row in complete) / len(complete) if complete else None
    loss_rate = sum(not row["processed_retains_facts"] for row in rows) / len(rows) if rows else 0
    summary = {"cases": len(rows), "source_kinds": sorted({row["source_kind"] for row in rows}), "completed_pairs": len(complete),
               "compressed_cases": sum(row["compression_applied"] for row in rows), "critical_fact_loss_rate": round(loss_rate, 4),
               "raw_accuracy": raw_accuracy, "processed_accuracy": processed_accuracy,
               "reported_raw_prompt_tokens": raw_tokens, "reported_processed_prompt_tokens": processed_tokens,
               "reported_prompt_token_saving_ratio": saving,
               "reported_raw_total_tokens": sum(row["raw_usage"]["total_tokens"] for row in complete) if total_complete else None,
               "reported_processed_total_tokens": sum(row["processed_usage"]["total_tokens"] for row in complete) if total_complete else None,
               "raw_median_latency_ms": statistics.median([row["raw_latency_ms"] for row in complete]) if complete else None,
               "processed_median_latency_ms": statistics.median([row["processed_latency_ms"] for row in complete]) if complete else None}
    failures, missing = [], []
    if loss_rate:
        failures.append("处理后丢失了标注的关键事实")
    if not model_answers:
        missing.append("尚未比较真实模型答案")
    elif len(complete) != len(rows):
        missing.append("模型请求存在未完成的成对结果")
    if model_answers and len(complete) < min_cases:
        missing.append(f"完成样本少于门槛 {min_cases}")
    if model_answers and not usage_complete:
        missing.append("缺少提供商报告的输入 Token 用量")
    if model_answers and usage_complete and not raw_tokens:
        missing.append("提供商报告的输入 Token 总量必须大于零")
    if processed_accuracy is not None and processed_accuracy < min_accuracy:
        failures.append("处理后答案准确率未达到最低阈值")
    if raw_accuracy is not None and raw_accuracy - processed_accuracy > max_accuracy_drop + 1e-9:
        failures.append("答案准确率下降超过阈值")
    if saving is not None and saving < min_saving:
        failures.append("输入 Token 节省未达到阈值")
    gate = {"status": "failed" if failures else "not_evaluated" if missing else "passed", "scope": "provided_cases_only",
            "representative_quality_proven": False, "reasons": failures + missing,
            "thresholds": {"min_cases": min_cases, "min_accuracy": min_accuracy, "max_accuracy_drop": max_accuracy_drop, "min_prompt_token_saving_ratio": min_saving},
            "note": "样本集门槛不证明真实业务代表性；实际账单和费用仍由提供商决定。"}
    return summary, gate


def evaluate(cases: list[dict[str, Any]], provider: str | None = None, model: str = "auto", *,
             timeout: float = 120, max_tokens: int = 512, min_cases: int = 50, max_accuracy_drop: float = .02,
             min_saving: float = .05, min_accuracy: float = .9, ollama_context: int = 32768, progress: Callable[[dict[str, Any]], None] | None = None) -> dict[str, Any]:
    if not isinstance(cases, list) or not cases:
        raise ValueError("需要非空评测样本数组")
    for case in cases:
        _validate_case(case, provider is not None)
    if min_cases < 1 or not 0 <= max_accuracy_drop <= 1 or not 0 <= min_saving <= 1 or not 0 <= min_accuracy <= 1:
        raise ValueError("评测门槛格式无效")
    if provider == "omniroute":
        model = omniroute_model(model)
    client = OpenAICompatibleProvider(provider, timeout=timeout) if provider else None
    available = client.models() if client and model == "auto" else []
    rows: list[dict[str, Any]] = []
    report: dict[str, Any] = {"mode": "same_model_answers" if client else "offline_fact_retention_only", "provider": provider,
                              "note": "原文与实际选中的处理后提示词使用同一模型；估算和提供商 usage 分开记录。", "cases": rows}
    report["model_settings"] = {"max_tokens": max_tokens, "ollama_context": ollama_context if provider == "ollama" else None,
                                "ollama_think": False if provider == "ollama" else None}
    for case in cases:
        task, raw = case["task"].strip(), case["content"].strip()
        workflow = run_workflow(task, raw, lambda _kind, text: json.dumps(chat_messages(task, text), ensure_ascii=False))
        processed = workflow["result"]
        row = {"name": case.get("name", "case"), "source_kind": case.get("source_kind", "user_supplied"),
               "source_file": case.get("source_file"), "source_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
               "raw_prompt_tokens": workflow["baseline"]["estimated_tokens"],
               "processed_prompt_tokens": workflow["optimized"]["estimated_tokens"], "selected_saved_tokens": workflow["saved_tokens"],
               "compression_applied": workflow["compression_applied"],
               "token_counting": {key: workflow["token_counting"][key] for key in ("backend", "exact", "scope")},
               "raw_retains_facts": all(fact in raw for fact in case["must_keep"]),
               "processed_retains_facts": all(fact in processed for fact in case["must_keep"])}
        row["candidate_retains_facts"] = row["processed_retains_facts"]  # previous offline consumers
        if client:
            selected = model if model != "auto" else choose_model(available, workflow["task_type"], model)
            row["model"] = selected
            try:
                # Alternate pair order to reduce systematic warm-cache latency bias.
                pair = (("raw", raw), ("processed", processed))
                for label, value in (pair if len(rows) % 2 == 0 else reversed(pair)):
                    started = time.monotonic()
                    options = {"context_window": ollama_context} if provider == "ollama" else {}
                    response = client.chat(selected, chat_messages(task, value), max_tokens=max_tokens, **options)
                    answer = response_text(response)
                    row[label + "_answer"] = answer
                    row[label + "_answer_pass"] = answer_pass(answer, case["expected_answer"], case.get("answer_match", "exact"))
                    row[label + "_latency_ms"] = round((time.monotonic() - started) * 1000)
                    row[label + "_usage"] = reported_usage(response)
            except ProviderError:
                row["error"] = "模型请求失败；未完成的样本不计为成功"
        rows.append(row)
        report["summary"], report["quality_gate"] = summarize(rows, client is not None, min_cases, max_accuracy_drop, min_saving, min_accuracy)
        report["expected_cases"] = len(cases)
        if len(rows) < len(cases):
            if report["quality_gate"]["status"] == "passed":
                report["quality_gate"]["status"] = "not_evaluated"
            report["quality_gate"]["reasons"].append("样本集尚未运行完毕")
        if progress:
            progress(report)
    return report


def markdown_report(report: dict[str, Any]) -> str:
    summary, gate = report["summary"], report["quality_gate"]
    fence = chr(96) * 3
    lines = ["# TokenFlow quality comparison", "", report["note"], "", f"Gate: **{gate['status']}** ({gate['scope']})", "",
             "Repository QA and synthetic tasks are not representative production task logs. Usage is provider-reported, not a bill.", "",
             fence + "json", json.dumps(summary, ensure_ascii=False, indent=2), fence, "",
             "| Case | Source | Compressed | Facts retained | Raw answer pass | Processed answer pass |", "|---|---|---|---|---|---|"]
    for row in report["cases"]:
        name = str(row["name"]).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {name} | {row['source_kind']} | {row['compression_applied']} | {row['processed_retains_facts']} | {row.get('raw_answer_pass', '—')} | {row.get('processed_answer_pass', '—')} |")
    lines.extend(["", "Gate reasons: " + ("; ".join(gate["reasons"]) or "thresholds met for these cases"), ""])
    return "\n".join(lines)


def self_check() -> None:
    from unittest.mock import patch

    report = evaluate(sample_cases())
    assert all(row["processed_retains_facts"] for row in report["cases"])
    assert any(row["compression_applied"] for row in report["cases"])
    assert report["quality_gate"]["status"] == "not_evaluated"
    assert answer_pass("7。", "7") and not answer_pass("17", "7") and not answer_pass("不是7", "7")
    assert not answer_pass("17", ["7"], "contains_all")
    python_source = ("# unrelated\n" * 300) + "def target():\n    value = 42\n    if value < 0:\n        return None\n    return value\n" + ("# unrelated\n" * 300)
    assert "return value" in run_workflow("target 函数返回什么？", python_source)["result"]
    markdown_source = "# Other\n" + "无关内容\n" * 600 + "# 退款期限\n说明。\n说明。\n不得超过7天。\n"
    assert "不得超过7天" in run_workflow("退款期限的限制是什么？", markdown_source)["result"]
    with patch(__name__ + ".OpenAICompatibleProvider") as client_class:
        client_class.return_value.chat.return_value = {"choices": [{"message": {"content": "7"}}],
                                                     "usage": {"prompt_tokens": 100, "total_tokens": 101}}
        compared = evaluate(sample_cases()[:1], "freetoken", "test-model", min_cases=1, min_saving=0)
        assert compared["quality_gate"]["status"] == "passed"
        assert compared["summary"]["reported_raw_prompt_tokens"] == 100
        assert client_class.return_value.chat.call_count == 2
        client_class.return_value.chat.return_value.pop("usage")
        assert evaluate(sample_cases()[:1], "freetoken", "test-model", min_cases=1)["quality_gate"]["status"] == "not_evaluated"
        client_class.reset_mock()
        try:
            evaluate([{"task": "task", "content": "input", "must_keep": ["missing"]}], "freetoken")
        except ValueError:
            pass
        else:
            raise AssertionError("invalid facts must fail before connecting")
        client_class.assert_not_called()
    assert not run_workflow("查找 ZZZ999 的值", "无关记录\n" * 600)["compression_applied"]
    assert not run_workflow("退款期限是几天？", "退款期限为7天。\n" * 600)["compression_applied"]
    print("TokenFlow evaluation self-check: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare raw and selected TokenFlow prompts")
    parser.add_argument("--cases", type=Path, help="JSON cases; inline content or relative source_file")
    parser.add_argument("--provider", choices=tuple(PROVIDERS), help="Explicit provider; omitted means offline-only")
    parser.add_argument("--model", default="auto")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--ollama-context", type=int, default=32768, help="Ollama native eval context; thinking off, temperature 0")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--report", type=Path, help="Write incremental JSON and matching Markdown")
    parser.add_argument("--min-cases", type=int, default=50)
    parser.add_argument("--min-accuracy", type=float, default=.9)
    parser.add_argument("--max-accuracy-drop", type=float, default=.02)
    parser.add_argument("--min-saving", type=float, default=.05)
    parser.add_argument("--enforce-gate", action="store_true", help="Exit 2 unless gate passes")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if args.report and args.report.suffix.lower() != ".json":
        parser.error("report 必须使用 .json 后缀；Markdown 会自动生成")
    if args.timeout <= 0 or args.max_tokens < 1 or not 2048 <= args.ollama_context <= 131072 or (args.limit is not None and args.limit < 1):
        parser.error("timeout、max-tokens、limit 必须为正数")

    def progress(report):
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            args.report.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
            print(f"Completed {len(report['cases'])} pairs/cases", flush=True)
    try:
        cases = load_cases(args.cases) if args.cases else sample_cases()
        report = evaluate(cases[:args.limit] if args.limit else cases, args.provider, args.model,
                          timeout=args.timeout, max_tokens=args.max_tokens, min_cases=args.min_cases,
                          max_accuracy_drop=args.max_accuracy_drop, min_saving=args.min_saving, min_accuracy=args.min_accuracy,
                          ollama_context=args.ollama_context, progress=progress)
    except (ValueError, OSError, ProviderError) as exc:
        parser.error(str(exc))
    print(json.dumps(report["summary"] if args.report else report, ensure_ascii=False, indent=2))
    if args.enforce_gate and report["quality_gate"]["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
