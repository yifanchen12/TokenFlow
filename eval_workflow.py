"""Small, opt-in comparison of TokenFlow's raw and candidate prompts."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from model_providers import OpenAICompatibleProvider, PROVIDERS, chat_messages, choose_model, response_text
from token_counter import count_tokens
from tokenflow import classify_task, compact_content, run_workflow


def sample_cases() -> list[dict[str, Any]]:
    filler = "这段背景与问题无关，仅供测试长上下文保留情况。\n" * 130
    return [
        {"name": "document", "task": "退款期限是几天？只回答数字。", "content": filler + "关键事实：退款期限为7天。\n" + filler,
         "must_keep": ["退款期限为7天"], "expected_answer": "7"},
        {"name": "code", "task": "代码中 ANSWER 的值是多少？只回答数字。", "content": ("# 无关代码注释\n" * 240) + "ANSWER = 42\n" + ("# 无关代码注释\n" * 240),
         "must_keep": ["ANSWER = 42"], "expected_answer": "42"},
        {"name": "table", "task": "表格中 A17 行的数量是多少？只回答数字。", "content": ("B01,0\n" * 400) + "A17,42\n" + ("B02,0\n" * 400),
         "must_keep": ["A17,42"], "expected_answer": "42"},
    ]


def evaluate(cases: list[dict[str, Any]], provider: str | None = None, model: str = "auto") -> dict[str, Any]:
    client = OpenAICompatibleProvider(provider) if provider else None
    available_models = client.models() if client else []
    rows = []
    for case in cases:
        task, content = case["task"], case["content"]
        facts = case["must_keep"]
        if not isinstance(task, str) or not isinstance(content, str) or not isinstance(facts, list) or not facts or not all(isinstance(fact, str) and fact for fact in facts):
            raise ValueError("评测样本需要 task、content 字符串和非空 must_keep 字符串数组")
        raw = content.strip()
        candidate = compact_content(raw, task=task)
        prompt = lambda _task_type, value: json.dumps(chat_messages(task.strip(), value), ensure_ascii=False)
        workflow = run_workflow(task, content, prompt)
        row = {
            "name": case.get("name", "case"),
            "raw_prompt_tokens": workflow["baseline"]["estimated_tokens"],
            "candidate_prompt_tokens": count_tokens(prompt("", candidate))["count"],
            "selected_saved_tokens": workflow["saved_tokens"],
            "compression_applied": workflow["compression_applied"],
            "raw_retains_facts": all(fact in raw for fact in facts),
            "candidate_retains_facts": all(fact in candidate for fact in facts),
        }
        if client:
            expected = case.get("expected_answer")
            if not isinstance(expected, str) or not expected:
                raise ValueError("模型评测样本需要非空 expected_answer")
            selected_model = choose_model(available_models, classify_task(task, raw), model)
            row["model"] = selected_model
            for label, value in (("raw", raw), ("candidate", candidate)):
                started = time.monotonic()
                answer = response_text(client.chat(selected_model, chat_messages(task.strip(), value)))
                row[f"{label}_answer_pass"] = expected in answer
                row[f"{label}_latency_ms"] = round((time.monotonic() - started) * 1000)
        rows.append(row)
    return {
        "mode": "same_model_answers" if client else "offline_fact_retention_only",
        "note": "示例基准不代表普遍答案质量；Token 为可见提示词估算值，不是实际计费量。",
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare raw and candidate TokenFlow prompts")
    parser.add_argument("--cases", type=Path, help="JSON array of task/content/must_keep/expected_answer cases")
    parser.add_argument("--provider", choices=tuple(PROVIDERS), help="Explicit provider; omitted means offline-only")
    parser.add_argument("--model", default="auto")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8")) if args.cases else sample_cases()
    if not isinstance(cases, list):
        parser.error("评测样本顶层必须是数组")
    report = evaluate(cases, args.provider, args.model)
    if args.self_check:
        from unittest.mock import patch

        assert len(report["cases"]) == 3
        assert all(row["raw_retains_facts"] for row in report["cases"])
        assert all(row["candidate_retains_facts"] and row["compression_applied"] for row in report["cases"])
        no_match = run_workflow("查找 ZZZ999 的值", "无关记录\n" * 600)
        assert not no_match["compression_applied"] and no_match["saved_tokens"] == 0
        too_many_matches = run_workflow("退款期限是几天？", "退款期限为7天。\n" * 600)
        assert not too_many_matches["compression_applied"]
        table = run_workflow("表格中 A17 行的数量是多少？", "编号,数量\n" + "B01,0\n" * 400 + "A17,42\n" + "B02,0\n" * 400)
        assert table["compression_applied"] and "编号,数量" in table["result"] and "A17,42" in table["result"]
        with patch(f"{__name__}.OpenAICompatibleProvider") as client_class:
            client_class.return_value.models.return_value = ["test-model"]
            client_class.return_value.chat.return_value = {"choices": [{"message": {"content": "7"}}]}
            compared = evaluate(sample_cases()[:1], "freetoken", "test-model")
            assert compared["mode"] == "same_model_answers"
            assert compared["cases"][0]["raw_answer_pass"]
            assert client_class.return_value.chat.call_count == 2
        print("TokenFlow evaluation self-check: PASS")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
