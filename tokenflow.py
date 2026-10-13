"""TokenFlow: a dependency-free local MVP for low-token agent workflows."""

from __future__ import annotations

import argparse
import ast
import ipaddress
import json
import os
import re
import secrets
import signal
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import tempfile
from pathlib import Path
from email.parser import BytesParser
from email.policy import default as email_default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import urlsplit

from freetoken_provider import (
    FreeTokenClient,
    FreeTokenError,
    choose_model as choose_local_model,
    model_ids,
    response_text,
    self_check as freetoken_self_check,
)
from freetoken_manager import (
    launch_installer as launch_freetoken_installer,
    self_check as freetoken_manager_self_check,
    start_server as start_freetoken_server,
    status as freetoken_status,
)
from token_counter import count_tokens, self_check as token_counter_self_check, status as token_counter_status
from document_parser import DocumentParseError, parse_document_bytes, parse_document_file, self_check as document_parser_self_check
from jev_provider import JevError, decide as jev_decide, self_check as jev_self_check
from model_providers import ProviderError, chat_messages, configure_omniroute, is_loopback_url, omniroute_config, omniroute_model, omniroute_preview, provider_status, self_check as provider_self_check, unified_chat
from omniroute_manager import install as install_omniroute, self_check as omniroute_manager_self_check, start as start_omniroute, status as omniroute_status
from pc_agent import PCAgentError, execute_actions, plan_actions, self_check as pc_agent_self_check
from tokenflow_store import DocumentStore, self_check as store_self_check
from runtime_config import RuntimeConfig, native_catalogs, ConnectionClient
from task_orchestrator import start_task, cancel_task, TaskCancelled


MAX_COMPRESSED_CHARS = 2400
MAX_EXEC_SECONDS = 90
MAX_OUTPUT_CHARS = 20000
MAX_OUTPUT_BYTES = MAX_OUTPUT_CHARS * 4
SESSION_TOKEN = secrets.token_urlsafe(32)
HARNESS_NAMES = ("codex", "claude", "dsh")


def estimate_tokens(text: str) -> int:
    """Compatibility wrapper returning the active token counter result."""
    return count_tokens(text)["count"]


def classify_task(task: str, content: str) -> str:
    sample = f"{task}\n{content}".lower()
    if re.search(r"(def |class |import |function|代码|源码|bug|报错|stack trace)", sample):
        return "code"
    if re.search(r"(csv|tsv|表格|数据|统计|列名|行数|均值|平均)", sample):
        return "table"
    if re.search(r"(文档|摘要|总结|报告|文章|合同|说明书|pdf|docx)", sample):
        return "document"
    return "general"


def requires_full_context(task: str) -> bool:
    return any(intent in task.lower() for intent in ("总结", "概括", "摘要", "全文", "完整", "全面", "逐行", "翻译", "改写", "润色", "校对", "重构",
                                                     "统计", "总和", "平均", "对比", "比较", "所有", "summar", "translat", "entire", "all rows"))


def compact_content(content: str, limit: int = MAX_COMPRESSED_CHARS, *, task: str = "", matched_spans: list[tuple[int, int]] | None = None) -> str:
    """Keep task-matching source lines; never guess which unmatched lines matter."""
    source = content.strip()
    if len(source) <= limit:
        return source
    if requires_full_context(task):
        return source

    generic_words = {"python", "code", "csv", "tsv", "json", "pdf", "docx", "sql", "bug"}
    words = {word.lower() for word in re.findall(r"[A-Za-z_][A-Za-z_0-9]*", task)
             if len(word) >= 3 and word.lower() not in generic_words}
    han = {phrase[i:i + size] for phrase in re.findall(r"[\u3400-\u9fff]+", task)
           for size in (3, 4) for i in range(len(phrase) - size + 1)}
    if not words and not han:
        return source

    word_patterns = [re.compile(rf"(?<![A-Za-z_0-9]){re.escape(word)}(?![A-Za-z_0-9])", re.I)
                     for word in words]
    lines = source.splitlines(keepends=True)
    # ponytail: linear line scanning is enough for one input; index only if large-corpus latency demands it.
    hits = [i for i, line in enumerate(lines)
            if any(phrase in line for phrase in han) or any(pattern.search(line) for pattern in word_patterns)]
    if matched_spans:
        offset, retrieved = 0, set()
        for i, line in enumerate(lines):
            if any(offset < end and offset + len(line) > start for start, end in matched_spans):
                retrieved.add(i)
            offset += len(line)
        # Keep complete function/section expansion below; chunk edges are not
        # boundaries. No lexical hit in retrieved chunks means use the full scan.
        selected_hits = [i for i in hits if i in retrieved]
        if selected_hits:
            hits = selected_hits
    if not hits:
        return source

    keep = {index for hit in hits for index in (hit - 1, hit, hit + 1) if 0 <= index < len(lines)}
    if sum(len(lines[index]) for index in keep) > limit:
        return source
    scopes = []
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        tree = None
    if tree is not None:
        scopes = [(node.lineno - 1, node.end_lineno) for node in ast.walk(tree)
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.end_lineno]
    else:
        headings = [(index, len(match.group(1))) for index, line in enumerate(lines)
                    if (match := re.match(r"^(#{1,6})\s", line))]
        if headings:
            scopes = [(start, next((other for other, depth in headings if other > start and depth <= level), len(lines)))
                      for start, level in headings]
        else:
            start = 0
            for index, line in enumerate(lines + ["\n"]):
                if not line.strip():
                    if index > start:
                        scopes.append((start, index))
                    start = index + 1
    for hit in hits:
        containing = [(start, end) for start, end in scopes if start <= hit < end]
        if containing:
            start, end = min(containing, key=lambda scope: scope[1] - scope[0])
            keep.update(range(start, end))
            if sum(len(lines[index]) for index in keep) > limit:
                return source
    if "表格" in task or "csv" in task.lower() or "tsv" in task.lower():
        keep.add(0)
    ordered = sorted(keep)
    parts = []
    marker = "\n[... omitted source ...]\n"
    if ordered[0] > 0:
        parts.append(marker.lstrip())
    for previous, index in zip([ordered[0] - 1] + ordered[:-1], ordered):
        if index > previous + 1:
            parts.append(marker)
        parts.append(lines[index])
    if ordered[-1] < len(lines) - 1:
        parts.append(marker)
    candidate = "".join(parts).strip()
    return candidate if len(candidate) <= limit else source


def build_plan(task_type: str) -> list[dict[str, str]]:
    focus = {
        "code": "扫描代码结构并保留关键错误和定义",
        "table": "识别表格结构并保留字段与统计相关内容",
        "document": "清洗文档文本并保留摘要所需段落",
        "general": "清洗输入并保留与任务直接相关的内容",
    }[task_type]
    return [
        {"name": "classify", "status": "completed", "detail": f"任务类型：{task_type}"},
        {"name": "local_filter", "status": "completed", "detail": focus},
        {"name": "measure", "status": "completed", "detail": "计算压缩前后的估算 Token"},
    ]


def run_workflow(task: str, content: str, render_prompt: Callable[[str, str], str] | None = None) -> dict[str, Any]:
    task = task.strip()
    content = content.strip()
    if not task:
        raise ValueError("task 不能为空")
    if not content:
        raise ValueError("content 不能为空")

    task_type = classify_task(task, content)
    candidate = compact_content(content, task=task)
    render = render_prompt or (lambda _task_type, value: value)
    baseline_count = count_tokens(render(task_type, content))
    candidate_count = count_tokens(render(task_type, candidate))
    compression_applied = candidate != content and candidate_count["count"] < baseline_count["count"]
    compressed = candidate if compression_applied else content
    optimized_count = candidate_count if compression_applied else baseline_count
    baseline_tokens = baseline_count["count"]
    optimized_tokens = optimized_count["count"]
    saved_tokens = baseline_tokens - optimized_tokens
    saved_ratio = round(saved_tokens / baseline_tokens, 4) if baseline_tokens else 0

    return {
        "task_type": task_type,
        "route": "local_tool",
        "steps": build_plan(task_type),
        "baseline": {"estimated_tokens": baseline_tokens},
        "optimized": {"estimated_tokens": optimized_tokens},
        "token_counting": {
            "backend": baseline_count["backend"],
            "exact": baseline_count["exact"],
            "tokenizer": baseline_count["tokenizer"],
            "warning": baseline_count.get("warning"),
            "scope": "visible_prompt" if render_prompt else "content",
        },
        "compression_applied": compression_applied,
        "saved_tokens": saved_tokens,
        "saved_ratio": saved_ratio,
        "note": "仅估算可见提示词文本；不包含模型隐藏开销，不等同于实际计费 Token。",
        "result": compressed,
    }


def _valid_model(model: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._:/+-]{1,80}", model))


def _resolve_harness_command(harness: str) -> list[str] | None:
    """Resolve PowerShell wrappers without passing user input to a shell."""
    if harness not in HARNESS_NAMES:
        return None
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        if powershell:
            lookup = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-Command",
                    f"(Get-Command {harness} -ErrorAction SilentlyContinue).Source",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            source = lookup.stdout.strip()
            if source:
                if source.lower().endswith(".ps1"):
                    folder = Path(source).parent
                    if harness == "codex":
                        native = next((folder / "node_modules" / "@openai" / "codex").glob("node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe"), None)
                        if native:
                            return [str(native)]
                    if harness == "dsh":
                        node, script = folder / "node.exe", folder / "node_modules/@deepseek-ai/dsh/lib/bin.js"
                        if node.exists() and script.exists():
                            return [str(node), str(script)]
                    return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", source]
                return [source]
    executable = shutil.which(harness)
    return [executable] if executable else None


def detect_harnesses() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    catalogs = native_catalogs()
    for harness in HARNESS_NAMES:
        command = _resolve_harness_command(harness)
        configured = True
        note = ""
        if harness == "dsh" and not (Path(os.environ.get("DSH_HOME", Path.home() / ".dsh")) / "profiles/headless/package.json").exists():
            configured = False
            note = "建议设置 DSH_HOME，并准备可写的 headless profile"
        result[harness] = {
            "installed": command is not None,
            "configured": configured,
            "command": command[0] if command else None,
            "default_models": {"quality": catalogs[harness]["default_model"],
                               "fast": next((m for m in catalogs[harness]["models"] if any(t in m.lower() for t in ("luna", "haiku", "flash", "mini"))), catalogs[harness]["default_model"])},
            "note": note,
            "models": catalogs[harness]["models"],
            "model_source": catalogs[harness]["source"],
        }
    return result


def choose_harness(task_type: str, content: str) -> str:
    preferred = ["codex", "claude", "dsh"] if task_type == "code" else ["claude", "codex", "dsh"]
    statuses = detect_harnesses()
    for harness in preferred:
        if statuses[harness]["installed"]:
            return harness
    return "none"


def choose_model(harness: str, task_type: str, content: str) -> str:
    if harness not in HARNESS_NAMES:
        raise ValueError(f"不支持的 Harness：{harness}")
    catalog = native_catalogs()[harness]
    models = catalog["models"]
    if task_type != "code" and len(content) <= 1400:
        fast = next((m for m in models if any(tag in m.lower() for tag in ("luna", "haiku", "flash", "mini"))), None)
        if fast:
            return fast
    return catalog["default_model"] if catalog["default_model"] != "auto" else next(iter(models), "auto")


def build_harness_prompt(task: str, task_type: str, compressed: str) -> str:
    return (
        "你正在执行 TokenFlow 处理后的任务。\n"
        f"任务类型：{task_type}\n"
        f"用户任务：{task}\n\n"
        "请基于以下内容完成任务。当前调用默认处于只读或计划模式，"
        "除非用户明确要求，不要修改文件、删除数据或执行高风险操作。\n\n"
        "--- 输入内容 ---\n"
        f"{compressed}\n"
        "--- 输入内容结束 ---"
    )


def build_harness_args(
    harness: str, model: str, prompt: str, cwd: str, executable: list[str], backend: str = "direct",
    gateway: tuple[str, str] | None = None,
) -> list[str]:
    if harness not in HARNESS_NAMES:
        raise ValueError(f"不支持的 Harness：{harness}")
    if backend not in {"direct", "omniroute"} or (backend == "omniroute" and harness != "codex"):
        raise ValueError("OmniRoute Harness 后端仅支持显式选择 Codex")
    if not _valid_model(model):
        raise ValueError("模型名只能包含字母、数字、点、下划线、冒号、斜线、加号和连字符")
    if harness == "codex":
        config: list[str] = []
        if backend == "omniroute":
            model = omniroute_model(model)
            base_url, gateway_key = gateway if gateway is not None else omniroute_config()
            settings = {
                "model_provider": "omniroute",
                "model_providers.omniroute.name": "OmniRoute",
                "model_providers.omniroute.base_url": base_url,
                "model_providers.omniroute.wire_api": "responses",
            }
            if gateway_key:
                settings["model_providers.omniroute.env_key"] = "TOKENFLOW_OMNIROUTE_API_KEY"
            for key, value in settings.items():
                config.extend(["-c", f"{key}={json.dumps(value)}"])
        return executable + [
            "exec",
            *config,
            "--ephemeral",
            "--skip-git-repo-check",
            *(["-m", model] if model != "auto" else []),
            "-C",
            cwd,
            "-s",
            "read-only",
            prompt,
        ]
    if harness == "claude":
        return executable + [
            "-p",
            *(["--model", model] if model != "auto" else []),
            "--no-session-persistence",
            "--permission-mode",
            "plan",
            "--add-dir",
            cwd,
            "--",
            prompt,
        ]
    profile = os.environ.get("TOKENFLOW_DSH_PROFILE", "headless")
    if model != "auto":
        raise ValueError("DSH headless 通过连接绑定设置模型，不支持 --model 参数")
    return executable + ["--profile", profile, prompt]


def _clip_output(value: str, truncated: bool = False) -> str:
    value = value.strip()
    if len(value) <= MAX_OUTPUT_CHARS and not truncated:
        return value
    return value[:MAX_OUTPUT_CHARS] + "\n...[output truncated]..."


def _run_bounded(command: list[str], cwd: str, timeout: float, env: dict[str, str] | None = None,
                 *, stdin_text: str | None = None, cancel_event: threading.Event | None = None) -> tuple[int, str, str, bool, bool, bool]:
    if cancel_event and cancel_event.is_set():
        raise TaskCancelled("任务已取消")
    if os.name == "nt" and len(subprocess.list2cmdline(command)) >= 32767:
        raise ValueError("Harness 启动参数超过 Windows 长度限制")
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, bufsize=0, env=env,
                               start_new_session=os.name != "nt")
    buffers = [bytearray(), bytearray()]
    truncated = [False, False]

    def drain(stream: Any, index: int) -> None:
        try:
            with stream:
                while chunk := stream.read(8192):
                    remaining = MAX_OUTPUT_BYTES - len(buffers[index])
                    buffers[index].extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        truncated[index] = True
        except (OSError, ValueError):
            pass

    threads = [threading.Thread(target=drain, args=(stream, index), daemon=True)
               for index, stream in enumerate((process.stdout, process.stderr))]
    for thread in threads:
        thread.start()
    def feed():
        try:
            with process.stdin:
                remaining = memoryview(stdin_text.encode("utf-8"))
                while remaining:
                    written = process.stdin.write(remaining)
                    if not written:
                        break
                    remaining = remaining[written:]
        except (BrokenPipeError, OSError, ValueError):
            pass
    if stdin_text is not None:
        writer = threading.Thread(target=feed, daemon=True)
        threads.append(writer)
        writer.start()
    timed_out = False
    cancelled = False
    deadline = time.monotonic() + timeout
    while process.poll() is None:
        cancelled = bool(cancel_event and cancel_event.is_set())
        timed_out = time.monotonic() >= deadline
        if cancelled or timed_out:
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=False,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            process.wait()
            break
        try:
            process.wait(timeout=min(.1, max(.001, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
    for thread in threads:
        thread.join(timeout=1)
    for stream in (process.stdout, process.stderr):
        if stream and not stream.closed:
            stream.close()
    if cancelled or (cancel_event and cancel_event.is_set()):
        raise TaskCancelled("任务已取消，Harness 已终止")
    return (process.returncode, buffers[0].decode(errors="replace"), buffers[1].decode(errors="replace"),
            truncated[0], truncated[1], timed_out)


def _run_harness(command: list[str], harness: str, prompt: str, env: dict[str, str] | None,
                 cancel_event: threading.Event | None = None):
    """Keep complete prompts out of argv; DSH headless accepts a task overlay."""
    command = list(command)
    if harness != "dsh":
        command[-1:] = ["-"] if harness == "codex" else []
        return _run_bounded(command, os.getcwd(), MAX_EXEC_SECONDS, env,
                            stdin_text=prompt, cancel_event=cancel_event)
    with tempfile.TemporaryDirectory(prefix="tokenflow-task-") as directory:
        task_file = Path(directory) / "task.json"
        task_file.write_text(json.dumps([{"id": "headless-runner", "config": {"task": prompt}}], ensure_ascii=False), encoding="utf-8")
        if os.name != "nt":
            task_file.chmod(0o600)
        command[-1] = "TokenFlow task from temporary overlay"
        position = command.index("--profile")
        command[position:position] = ["--patch", str(task_file)]
        return _run_bounded(command, os.getcwd(), MAX_EXEC_SECONDS, env, cancel_event=cancel_event)


def execute_harness(harness: str, model: str, prompt: str, backend: str = "direct",
                    *, cancel_event: threading.Event | None = None) -> dict[str, Any]:
    if backend == "omniroute":
        model = omniroute_model(model)
    executable = _resolve_harness_command(harness)
    if not executable:
        raise RuntimeError(f"未检测到可用的 {harness} 命令")
    gateway = omniroute_config() if backend == "omniroute" else None
    command = build_harness_args(harness, model, prompt, os.getcwd(), executable, backend, gateway)
    if harness == "codex" and backend == "direct" and native_catalogs()["codex"].get("provider", "openai") == "openai":
        # Preserve native account auth while using HTTPS on networks blocking WebSockets.
        settings = {"model_provider": "tokenflow_native", "model_providers.tokenflow_native.name": "Native Codex HTTPS",
                    "model_providers.tokenflow_native.requires_openai_auth": True,
                    "model_providers.tokenflow_native.supports_websockets": False}
        endpoint = native_catalogs()["codex"].get("base_url")
        if endpoint:
            settings["model_providers.tokenflow_native.base_url"] = endpoint
        command[len(executable) + 1:len(executable) + 1] = [a for k, v in settings.items() for a in ("-c", k + "=" + json.dumps(v))]
    child_env = os.environ.copy() if gateway is not None else None
    if child_env is not None:
        child_env.pop("TOKENFLOW_OMNIROUTE_API_KEY", None)
        if gateway[1]:
            child_env["TOKENFLOW_OMNIROUTE_API_KEY"] = gateway[1]
    started = time.monotonic()
    exit_code, output, error, output_truncated, error_truncated, timed_out = _run_harness(command, harness, prompt, child_env, cancel_event)
    if gateway is not None and gateway[1]:
        output = output.replace(gateway[1], "<REDACTED>")
        error = error.replace(gateway[1], "<REDACTED>")
    if timed_out:
        return {
            "ok": False,
            "harness": harness,
            "model": model,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "error": f"执行超过 {MAX_EXEC_SECONDS} 秒，已终止",
            "output": _clip_output(output, output_truncated),
        }
    return {
        "ok": exit_code == 0,
        "harness": harness,
        "model": model,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "exit_code": exit_code,
        "output": _clip_output(output, output_truncated),
        "error": _clip_output(error, error_truncated),
        "output_truncated": output_truncated,
    }


def execute_configured_harness(harness: str, model: str, prompt: str, store: RuntimeConfig | None = None,
                               *, cancel_event: threading.Event | None = None) -> dict[str, Any]:
    store = store or RuntimeConfig()
    if harness not in HARNESS_NAMES:
        raise ValueError("不支持的 Harness")
    binding = store.settings()["bindings"][harness]
    selected = model if model != "auto" else binding["model"]
    if binding["connection"] == "native":
        selected = choose_model(harness, "code", prompt) if selected == "auto" else selected
        return execute_harness(harness, selected, prompt, cancel_event=cancel_event)
    profile = store.profile(binding["connection"], True)
    from model_providers import choose_model as pick_model
    from runtime_config import HARNESS_PROTOCOLS
    if profile["protocol"] != HARNESS_PROTOCOLS.get(harness):
        raise ValueError("连接协议与 Harness 不兼容")
    selected = pick_model(profile["models"], "code", selected)
    executable = _resolve_harness_command(harness)
    if not executable:
        raise RuntimeError("Harness 未安装")
    command = build_harness_args(harness, "auto" if harness == "dsh" else selected, prompt, os.getcwd(), executable)
    child_env = os.environ.copy()
    key = profile["api_key"]
    if harness == "codex":
        settings = {"model_provider": "tokenflow_saved", "model_providers.tokenflow_saved.name": profile["name"],
                    "model_providers.tokenflow_saved.base_url": profile["base_url"],
                    "model_providers.tokenflow_saved.wire_api": "responses",
                    "model_providers.tokenflow_saved.requires_openai_auth": False}
        if key:
            settings["model_providers.tokenflow_saved.env_key"] = "TOKENFLOW_SAVED_API_KEY"
        if urlsplit(profile["base_url"]).hostname == "api.deepseek.com":
            # DeepSeek's catalog supports low/high/max, unlike inherited Codex effort defaults.
            settings["model_reasoning_effort"] = "low"
        overrides = [arg for k, v in settings.items() for arg in ("-c", k + "=" + json.dumps(v))]
        command[len(executable) + 1:len(executable) + 1] = overrides
        child_env["TOKENFLOW_SAVED_API_KEY"] = key
    elif harness == "claude":
        command[len(executable):len(executable)] = ["--setting-sources", ""]
        child_env.pop("ANTHROPIC_AUTH_TOKEN", None)
        child_env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        child_env["ANTHROPIC_BASE_URL"] = profile["base_url"]
        child_env["ANTHROPIC_API_KEY"] = key
    started = time.monotonic()
    if harness == "dsh":
        child_env["DSH_HOME"] = os.environ.get("DSH_HOME", str(Path.home() / ".dsh"))
        child_env["TOKENFLOW_SAVED_API_KEY"] = key
        overlay = [
            {"id": "llm-pi-ai", "config": {"providers": {"tokenflow-saved": {
                "displayName": profile["name"], "apiKeyEnv": "TOKENFLOW_SAVED_API_KEY", "api": "openai-completions",
                "baseURL": profile["base_url"], "models": [{"id": selected, "name": selected, "input": ["text"],
                                                          "contextWindow": 32768, "maxTokens": 4096}]}}}},
            {"id": "agent-default-model", "config": {"provider": "tokenflow-saved", "model": selected}},
            {"id": "permission", "config": {"presets": {"read-only": {"sandbox": "read-only", "approval": "ask"}}, "defaultPreset": "read-only"}},
            # Custom connections only need the child env; do not load unrelated native credentials.
            {"id": "credentials", "disabled": True},
        ]
        with tempfile.TemporaryDirectory(prefix="tokenflow-dsh-") as temp:
            patch = Path(temp) / "connection.json"
            patch.write_text(json.dumps(overlay, ensure_ascii=False), encoding="utf-8")
            command[len(executable):len(executable)] = ["--patch", str(patch)]
            code, output, error, cut, err_cut, timed_out = _run_harness(command, harness, prompt, child_env, cancel_event)
    else:
        code, output, error, cut, err_cut, timed_out = _run_harness(command, harness, prompt, child_env, cancel_event)
    if key:
        output, error = output.replace(key, "<REDACTED>"), error.replace(key, "<REDACTED>")
    return {"ok": code == 0 and not timed_out, "harness": harness, "model": selected,
            "connection": profile["id"], "duration_ms": round((time.monotonic() - started) * 1000),
            "exit_code": code, "output": _clip_output(output, cut), "output_truncated": cut,
            "error": "执行超时，已终止" if timed_out else _clip_output(error, err_cut)}


def execute_workflow(task: str, content: str, harness: str = "auto", model: str = "auto", harness_backend: str = "direct") -> dict[str, Any]:
    if harness_backend not in {"direct", "omniroute"} or (harness_backend == "omniroute" and harness != "codex"):
        raise ValueError("OmniRoute Harness 后端仅支持显式选择 Codex")
    if harness_backend == "omniroute":
        model = omniroute_model(model)
    workflow = run_workflow(task, content, lambda task_type, value: build_harness_prompt(task.strip(), task_type, value))
    selected_harness = choose_harness(workflow["task_type"], workflow["result"]) if harness == "auto" else harness
    if selected_harness == "none":
        raise RuntimeError("没有检测到可用的 Codex、Claude 或 DSH Harness")
    selected_model = (
        choose_model(selected_harness, workflow["task_type"], workflow["result"])
        if model == "auto"
        else model
    )
    prompt = build_harness_prompt(task.strip(), workflow["task_type"], workflow["result"])
    execution = (execute_harness(selected_harness, selected_model, prompt, harness_backend) if harness_backend == "omniroute"
                 else execute_configured_harness(selected_harness, model, prompt))
    selected_model = execution.get("model", selected_model)
    return {**workflow, "harness": selected_harness, "model": selected_model, "harness_backend": harness_backend, "execution": execution}


def _freetoken_client() -> FreeTokenClient:
    return FreeTokenClient(os.environ.get("TOKENFLOW_FREETOKEN_URL"))


def local_models_status() -> dict[str, Any]:
    client = _freetoken_client()
    health = client.health()
    if not health["available"]:
        return health | {"models": []}
    try:
        models = client.list_models()
    except FreeTokenError as exc:
        return {"available": False, "base_url": client.base_url, "models": [], "error": str(exc)}
    return {"available": True, "base_url": client.base_url, "models": model_ids(models)}


def _local_messages(task: str, content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "你是 TokenFlow 的本地预处理模型，请简洁、准确地完成任务。"},
        {"role": "user", "content": f"任务：{task}\n\n内容：\n{content}"},
    ]


def local_chat_workflow(task: str, content: str, requested_model: str = "auto") -> dict[str, Any]:
    workflow = run_workflow(task, content, lambda _task_type, value: json.dumps(_local_messages(task.strip(), value), ensure_ascii=False))
    client = _freetoken_client()
    if not is_loopback_url(client.base_url):
        raise FreeTokenError("本地模型接口只允许本机地址；远程 FreeToken 请在统一模型路由中显式选择")
    health = client.health()
    if not health["available"]:
        raise FreeTokenError(health["error"])
    models = client.list_models()
    selected_model = choose_local_model(models, workflow["task_type"], requested_model)
    response = client.chat(selected_model, _local_messages(task.strip(), workflow["result"]))
    return {
        **workflow,
        "provider": "freetoken",
        "base_url": client.base_url,
        "model": selected_model,
        "local_result": response_text(response),
    }


def unified_chat_workflow(task: str, content: str, provider: str = "auto", model: str = "auto") -> dict[str, Any]:
    workflow = run_workflow(task, content, lambda _task_type, value: json.dumps(chat_messages(task.strip(), value), ensure_ascii=False))
    routed = unified_chat(task.strip(), workflow["result"], workflow["task_type"], provider, model)
    return {**workflow, **routed}


def _document_store() -> DocumentStore:
    return DocumentStore()


from ui_page import PAGE


def _valid_write_request(origin: str | None, host: str, token: str, server_port: int) -> bool:
    if not token.isascii() or not secrets.compare_digest(token, SESSION_TOKEN):
        return False
    if not origin:
        return True
    try:
        source = urlsplit(origin)
        request_host = urlsplit(f"//{host}")
        hostname = source.hostname
        source_port = source.port if source.port is not None else 80
        request_port = request_host.port if request_host.port is not None else 80
        if (
            source.scheme != "http"
            or source.path
            or source.query
            or source.fragment
            or source.username
            or source.password
            or request_host.username
            or request_host.password
            or request_host.path
            or not hostname
            or hostname.lower() != (request_host.hostname or "").lower()
            or source_port != server_port
            or request_port != server_port
        ):
            return False
        return hostname.lower() == "localhost" or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _loopback_client(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


class TokenFlowHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path == "/api/ocr":
            from local_ocr import status
            self._send_json(status())
            return
        if self.path == "/api/config" or self.path.startswith("/api/tasks"):
            if not _loopback_client(self.client_address[0]) or not _valid_write_request(
                    self.headers.get("Origin") or "http://" + self.headers.get("Host", ""), self.headers.get("Host", ""),
                    self.headers.get("X-TokenFlow-Token", ""), self.server.server_address[1]):
                self._send_json({"error": "配置与任务记录仅允许本机读取"}, 403)
                return
            try:
                if self.path == "/api/config":
                    result = RuntimeConfig().public()
                elif self.path == "/api/tasks":
                    result = RuntimeConfig().runs()
                elif re.fullmatch(r"/api/tasks/[a-f0-9]{32}", self.path):
                    result = RuntimeConfig().runs(self.path.rsplit("/", 1)[1])
                else:
                    self._send_json({"error": "not found"}, 404)
                    return
                self._send_json(result)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            except (OSError, sqlite3.Error, RuntimeError):
                self._send_json({"error": "无法读取本地配置或任务记录"}, 503)
            return
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return
        if self.path == "/api/session":
            self._send_json({"token": SESSION_TOKEN})
            return
        if self.path == "/api/harnesses":
            self._send_json(detect_harnesses())
            return
        if self.path == "/api/local-models":
            self._send_json(local_models_status())
            return
        if self.path == "/api/freetoken":
            self._send_json(freetoken_status())
            return
        if self.path == "/api/omniroute":
            self._send_json(omniroute_status())
            return
        if self.path == "/api/tokenizer":
            self._send_json(token_counter_status())
            return
        if self.path == "/api/providers":
            self._send_json(provider_status())
            return
        if self.path == "/api/store":
            self._send_json(DocumentStore(initialize=False).status())
            return
        if self.path in ("/", "/index.html"):
            body = PAGE.replace("__TOKENFLOW_SESSION_TOKEN__", SESSION_TOKEN).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not _valid_write_request(
            self.headers.get("Origin"),
            self.headers.get("Host", ""),
            self.headers.get("X-TokenFlow-Token", ""),
            self.server.server_address[1],
        ):
            self._send_json({"error": "请求来源或会话令牌无效"}, 403)
            return
        if (self.path.startswith("/api/config/") or self.path.startswith("/api/tasks/") or self.path in {"/api/omniroute/config", "/api/omniroute/install", "/api/omniroute/start", "/api/omniroute/preview"}) \
                and (not _loopback_client(self.client_address[0]) or not _valid_write_request(
                    self.headers.get("Origin") or "http://" + self.headers.get("Host", ""), self.headers.get("Host", ""),
                    self.headers.get("X-TokenFlow-Token", ""), self.server.server_address[1])):
            self._send_json({"error": "OmniRoute 安装和配置仅允许本机操作"}, 403)
            return
        if self.path == "/api/shutdown":
            self._send_json({"status": "shutting_down"})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if self.path not in (
            "/api/run",
            "/api/execute",
            "/api/local-chat",
            "/api/chat",
            "/api/parse",
            "/api/index",
            "/api/store/migrate",
            "/api/search",
            "/api/pc/plan",
            "/api/pc/execute",
            "/api/jev/decision",
            "/api/freetoken/install",
            "/api/freetoken/start",
            "/api/omniroute/config",
            "/api/omniroute/install",
            "/api/omniroute/start",
            "/api/omniroute/preview",
            "/api/config/connection",
            "/api/config/discover",
            "/api/config/discover-local",
            "/api/config/discover-native",
            "/api/config/import-native",
            "/api/config/settings",
            "/api/tasks/start",
            "/api/tasks/cancel",
        ):
            self._send_json({"error": "not found"}, 404)
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0:
                raise ValueError("Content-Length 无效")
            content_type = self.headers.get("Content-Type", "application/json")
            is_multipart = content_type.lower().startswith("multipart/form-data")
            if is_multipart:
                if self.path not in ("/api/parse", "/api/index"):
                    raise ValueError("multipart/form-data 仅支持文件解析和索引")
                if size > 20 * 1024 * 1024 + 64 * 1024:
                    raise ValueError("上传请求不能超过 20 MB（不含少量封装开销）")
            elif size > 2_000_000:
                raise ValueError("JSON 请求内容不能超过 2 MB")
            raw = self.rfile.read(size)
            if is_multipart:
                envelope = (
                    f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw
                )
                message = BytesParser(policy=email_default).parsebytes(envelope)
                attachment = next((part for part in message.walk() if part is not message and part.get_filename()), None)
                if not attachment:
                    raise ValueError("multipart 请求缺少文件字段")
                file_bytes = attachment.get_payload(decode=True) or b""
                if len(file_bytes) > 20 * 1024 * 1024:
                    raise ValueError("上传文件不能超过 20 MB")
                data = {"filename": attachment.get_filename(), "file_bytes": file_bytes}
            else:
                data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("请求 JSON 顶层必须是对象")
            if self.path == "/api/config/connection":
                result = RuntimeConfig().save_profile(data)
            elif self.path == "/api/config/discover":
                result = RuntimeConfig().discover(str(data.get("id", "")))
            elif self.path == "/api/config/discover-local":
                result = RuntimeConfig().discover_local()
            elif self.path == "/api/config/discover-native":
                result = RuntimeConfig().discover_codex()
            elif self.path == "/api/config/import-native":
                result = RuntimeConfig().import_native(str(data.get("harness", "")))
            elif self.path == "/api/config/settings":
                result = RuntimeConfig().save_settings(data)
            elif self.path == "/api/tasks/start":
                result = start_task(data)
            elif self.path == "/api/tasks/cancel":
                result = cancel_task(data.get("id", ""))
            elif self.path == "/api/freetoken/install":
                result = launch_freetoken_installer()
            elif self.path == "/api/freetoken/start":
                result = start_freetoken_server()
            elif self.path == "/api/omniroute/config":
                configure_omniroute(data.get("url"), data.get("api_key"), data.get("allowed_models"))
                result = omniroute_status()
            elif self.path == "/api/omniroute/preview":
                result = omniroute_preview(data.get("model", "auto"))
            elif self.path == "/api/omniroute/install":
                result = install_omniroute()
            elif self.path == "/api/omniroute/start":
                result = start_omniroute()
            elif self.path == "/api/run":
                result = run_workflow(str(data.get("task", "")), str(data.get("content", "")))
            elif self.path == "/api/execute":
                result = execute_workflow(
                    str(data.get("task", "")),
                    str(data.get("content", "")),
                    str(data.get("harness", "auto")),
                    str(data.get("model", "auto")),
                    str(data.get("harness_backend", "direct")),
                )
            elif self.path == "/api/local-chat":
                result = local_chat_workflow(
                    str(data.get("task", "")),
                    str(data.get("content", "")),
                    str(data.get("model", "auto")),
                )
            elif self.path == "/api/chat":
                if data.get("connection_id"):
                    profile = RuntimeConfig().profile(str(data["connection_id"]), True)
                    from model_providers import choose_model as pick_model, response_text as get_text, reported_usage
                    model = pick_model(profile["models"], classify_task(str(data.get("task", "")), str(data.get("content", ""))), str(data.get("model", "auto")))
                    workflow = run_workflow(str(data.get("task", "")), str(data.get("content", "")))
                    response = ConnectionClient(profile).chat(model, chat_messages(str(data["task"]), workflow["result"]))
                    answer = get_text(response)
                    if profile["api_key"]:
                        answer = answer.replace(profile["api_key"], "<REDACTED>")
                    result = {**workflow, "provider": profile["name"], "model": model, "model_result": answer, "usage": reported_usage(response)}
                else:
                    result = unified_chat_workflow(
                        str(data.get("task", "")),
                        str(data.get("content", "")),
                        str(data.get("provider", "auto")),
                        str(data.get("model", "auto")),
                    )
            elif self.path == "/api/parse":
                if data.get("file_bytes") is not None:
                    result = parse_document_bytes(str(data.get("filename", "upload")), data["file_bytes"])
                else:
                    result = parse_document_file(str(data.get("path", "")))
            elif self.path == "/api/index":
                if data.get("file_bytes") is not None:
                    parsed = parse_document_bytes(str(data.get("filename", "upload")), data["file_bytes"])
                    name, content = parsed["name"], parsed["text"]
                else:
                    name, content = str(data.get("name", "document")), str(data.get("content", ""))
                result = _document_store().index(name, content)
            elif self.path == "/api/store/migrate":
                result = _document_store().import_legacy()
            elif self.path == "/api/search":
                result = {"results": _document_store().search(str(data.get("query", "")), int(data.get("limit", 5)))}
            elif self.path == "/api/pc/plan":
                result = plan_actions(data.get("actions"))
            elif self.path == "/api/pc/execute":
                result = execute_actions(data.get("actions"))
            elif self.path == "/api/jev/decision":
                result = jev_decide(data)
            else:
                self._send_json({"error": "not found"}, 404)
                return
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, 400)
            return
        except RuntimeError as exc:
            self._send_json({"error": str(exc)}, 503)
            return
        except (OSError, sqlite3.Error):
            self._send_json({"error": "无法读写本地配置或任务记录"}, 503)
            return
        self._send_json(result)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[TokenFlow] {format % args}")


def self_check() -> None:
    from http.client import HTTPConnection
    from unittest.mock import patch

    with patch.dict(os.environ, {"TOKENFLOW_FREETOKEN_URL": "https://example.invalid/v1"}):
        try:
            local_chat_workflow("task", "content")
        except FreeTokenError:
            pass
        else:
            raise AssertionError("local chat must reject remote endpoints")
    unchanged = run_workflow("test", "x" * (MAX_COMPRESSED_CHARS + 1))
    assert not unchanged["compression_applied"]
    assert unchanged["baseline"] == unchanged["optimized"] and unchanged["saved_tokens"] == 0
    assert unchanged["result"] == "x" * (MAX_COMPRESSED_CHARS + 1)
    wrapped = run_workflow("代码中 ANSWER 的值是多少？", ("# unrelated\n" * 250) + "ANSWER = 42\n" + ("# unrelated\n" * 250),
                           lambda _task_type, value: "prefix:" + value)
    assert wrapped["compression_applied"] and wrapped["token_counting"]["scope"] == "visible_prompt"
    assert "ANSWER = 42" in wrapped["result"]
    code, out, err, out_cut, err_cut, timed_out = _run_bounded(
        [sys.executable, "-c", "import sys; print('x' * 100000); print('y' * 100000, file=sys.stderr)"],
        os.getcwd(), 5,
    )
    assert code == 0 and not timed_out and out_cut and err_cut
    assert len(out.encode()) <= MAX_OUTPUT_BYTES and len(err.encode()) <= MAX_OUTPUT_BYTES
    code, out, err, out_cut, err_cut, timed_out = _run_bounded(
        [sys.executable, "-c", "import time; time.sleep(2)"], os.getcwd(), 0.1,
    )
    assert timed_out and code != 0
    broad = run_workflow("分析这段 Python 代码", "def add(a, b):\n    return a + b\n" * 200)
    assert not broad["compression_applied"]
    summary = run_workflow("总结这篇文章", ("这篇文章很长。\n" + "其他段落。\n" * 400))
    assert not summary["compression_applied"]
    result = run_workflow("代码中 ANSWER 的值是多少？", ("# unrelated\n" * 250) + "ANSWER = 42\n" + ("# unrelated\n" * 250))
    assert result["task_type"] == "code"
    assert result["saved_tokens"] > 0
    assert 0 < result["saved_ratio"] < 1
    assert len(result["steps"]) == 3
    try:
        run_workflow("", "内容")
    except ValueError:
        pass
    else:
        raise AssertionError("empty task must fail")
    with patch(__name__ + ".native_catalogs", return_value={"codex": {"models": ["detected-quality", "detected-luna"], "default_model": "detected-quality"}}):
        assert choose_model("codex", "code", "short") == "detected-quality"
        assert choose_model("codex", "general", "short") == "detected-luna"
    assert "-m" not in build_harness_args("codex", "auto", "prompt", ".", ["codex"])
    assert _valid_write_request("http://127.0.0.1:8765", "127.0.0.1:8765", SESSION_TOKEN, 8765)
    assert not _valid_write_request("http://example.com:8765", "127.0.0.1:8765", SESSION_TOKEN, 8765)
    assert not _valid_write_request(None, "127.0.0.1:8765", "invalid", 8765)
    assert _loopback_client("127.0.0.1") and _loopback_client("::1") and not _loopback_client("192.0.2.1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), TokenFlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {"Content-Type": "application/json", "X-TokenFlow-Token": SESSION_TOKEN}
        with patch("model_providers._omniroute_session", None), \
             patch("model_providers._omniroute_allowed", None), \
             patch("omniroute_manager._health", return_value=False), \
             patch(__name__ + ".install_omniroute", return_value={"action": "installer_launched"}) as installer, \
             patch(__name__ + ".start_omniroute", return_value={"action": "already_running"}) as starter:
            connection.request("POST", "/api/omniroute/config", '{"url":"http://127.0.0.1:20128/v1"}')
            response = connection.getresponse()
            assert response.status == 403
            response.read()
            connection.request("POST", "/api/omniroute/config", json.dumps({"url": "http://127.0.0.1:20128/v1", "api_key": "dummy-secret", "allowed_models": ["local-model"]}), headers)
            response = connection.getresponse()
            body = response.read()
            assert response.status == 200 and b"dummy-secret" not in body
            assert json.loads(body)["key_configured"]
            connection.request("GET", "/api/omniroute")
            response = connection.getresponse()
            body = response.read()
            assert response.status == 200 and b"dummy-secret" not in body
            connection.request("POST", "/api/omniroute/preview", '{"model":"local-model"}', headers)
            response = connection.getresponse()
            body = response.read()
            assert response.status == 200 and b"dummy-secret" not in body and json.loads(body)["cost"] == "unknown"
            connection.request("POST", "/api/omniroute/preview", '{"model":"auto"}', headers)
            response = connection.getresponse()
            assert response.status == 503
            response.read()
            for path, mocked in (("install", installer), ("start", starter)):
                connection.request("POST", "/api/omniroute/" + path, "{}", headers)
                response = connection.getresponse()
                assert response.status == 200
                response.read()
                mocked.assert_called_once()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    command = build_harness_args("codex", "gpt-5.6-luna", "prompt", ".", ["codex"])
    assert "read-only" in command and "--ephemeral" in command
    assert "model_provider" not in " ".join(command)
    with patch.dict(os.environ, {"TOKENFLOW_OMNIROUTE_URL": "http://127.0.0.1:20128/v1", "TOKENFLOW_OMNIROUTE_API_KEY": "test-secret"}):
        routed = build_harness_args("codex", "local-model", "prompt", ".", ["codex"], "omniroute")
        assert 'model_provider="omniroute"' in routed and 'model_providers.omniroute.wire_api="responses"' in routed
        assert "test-secret" not in " ".join(routed) and "read-only" in routed
    with patch("model_providers._omniroute_session", ("http://127.0.0.1:20128/v1", "session-secret")), \
         patch(__name__ + "._resolve_harness_command", return_value=["codex"]), \
         patch(__name__ + "._run_bounded", return_value=(0, "session-secret", "", False, False, False)) as run_bounded:
        execution = execute_harness("codex", "local-model", "prompt", "omniroute")
        assert run_bounded.call_args.args[0].count("session-secret") == 0
        assert run_bounded.call_args.args[-1]["TOKENFLOW_OMNIROUTE_API_KEY"] == "session-secret"
        assert "session-secret" not in str(execution)
    with patch(__name__ + ".execute_harness") as run_harness:
        run_harness.return_value = {"ok": True}
        routed_workflow = execute_workflow("回答问题", "输入", "codex", "local-model", "omniroute")
        assert routed_workflow["model"] == "local-model" and routed_workflow["harness_backend"] == "omniroute"
        assert run_harness.call_args.args[-1] == "omniroute"
    with patch("model_providers._omniroute_allowed", ("local-model",)), \
         patch(__name__ + "._run_bounded") as run_bounded:
        for blocked in ("auto", "blocked-model"):
            try:
                execute_harness("codex", blocked, "prompt", "omniroute")
            except ProviderError:
                pass
            else:
                raise AssertionError("model policy must fail before spawning a child")
        run_bounded.assert_not_called()
    try:
        execute_workflow("回答问题", "输入", "auto", "auto", "omniroute")
    except ValueError:
        pass
    else:
        raise AssertionError("OmniRoute backend must require explicit Codex selection")
    try:
        build_harness_args("claude", "sonnet", "prompt", ".", ["claude"], "omniroute")
    except ValueError:
        pass
    else:
        raise AssertionError("OmniRoute backend must reject non-Codex harnesses")
    try:
        build_harness_args("codex", "bad model", "prompt", ".", ["codex"])
    except ValueError:
        pass
    else:
        raise AssertionError("invalid model must fail")
    freetoken_self_check()
    freetoken_manager_self_check()
    omniroute_manager_self_check()
    token_counter_self_check()
    provider_self_check()
    store_self_check()
    document_parser_self_check()
    pc_agent_self_check()
    jev_self_check()
    from test_orchestration import self_check as orchestration_self_check
    orchestration_self_check()
    from test_optimizations import self_check as optimization_self_check
    optimization_self_check()
    assert "/api/tokenizer" in "/api/tokenizer"
    print("TokenFlow self-check: PASS")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local TokenFlow MVP")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    run_server(args.host, args.port)


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), TokenFlowHandler)
    print(f"TokenFlow running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTokenFlow stopped")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
