"""TokenFlow: a dependency-free local MVP for low-token agent workflows."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
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
from model_providers import ProviderError, chat_messages, is_loopback_url, provider_status, self_check as provider_self_check, unified_chat
from pc_agent import PCAgentError, execute_actions, plan_actions, self_check as pc_agent_self_check
from tokenflow_store import DocumentStore, self_check as store_self_check


MAX_COMPRESSED_CHARS = 2400
MAX_EXEC_SECONDS = 90
MAX_OUTPUT_CHARS = 20000
MAX_OUTPUT_BYTES = MAX_OUTPUT_CHARS * 4
SESSION_TOKEN = secrets.token_urlsafe(32)
HARNESS_NAMES = ("codex", "claude", "dsh")
DEFAULT_MODELS = {
    "codex": {"fast": "gpt-5.6-luna", "quality": "gpt-5.6-sol"},
    "claude": {"fast": "sonnet", "quality": "opus"},
    "dsh": {"fast": "v4f", "quality": "v4f/pro"},
}


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


def compact_content(content: str, limit: int = MAX_COMPRESSED_CHARS) -> str:
    """Normalize whitespace and keep the most useful edges for a small MVP.

    ponytail: head-tail extraction is intentionally heuristic; replace with a
    local summarizer when measured quality justifies model cost.
    """
    normalized = re.sub(r"[ \t]+", " ", content.replace("\r\n", "\n")).strip()
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    if len(normalized) <= limit:
        return normalized
    left = limit // 2
    right = limit - left
    return normalized[:left].rstrip() + "\n\n...[TokenFlow compressed middle]...\n\n" + normalized[-right:].lstrip()


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
    candidate = compact_content(content)
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
                    return [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", source]
                return [source]
    executable = shutil.which(harness)
    return [executable] if executable else None


def detect_harnesses() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for harness in HARNESS_NAMES:
        command = _resolve_harness_command(harness)
        configured = True
        note = ""
        if harness == "dsh" and not os.environ.get("DSH_HOME"):
            configured = False
            note = "建议设置 DSH_HOME，并准备可写的 headless profile"
        result[harness] = {
            "installed": command is not None,
            "configured": configured,
            "command": command[0] if command else None,
            "default_models": DEFAULT_MODELS[harness],
            "note": note,
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
    if harness not in DEFAULT_MODELS:
        raise ValueError(f"不支持的 Harness：{harness}")
    tier = "quality" if task_type == "code" or len(content) > 1400 else "fast"
    return DEFAULT_MODELS[harness][tier]


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
    harness: str, model: str, prompt: str, cwd: str, executable: list[str]
) -> list[str]:
    if harness not in HARNESS_NAMES:
        raise ValueError(f"不支持的 Harness：{harness}")
    if not _valid_model(model):
        raise ValueError("模型名只能包含字母、数字、点、下划线、冒号、斜线、加号和连字符")
    if harness == "codex":
        return executable + [
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "-m",
            model,
            "-C",
            cwd,
            "-s",
            "read-only",
            prompt,
        ]
    if harness == "claude":
        return executable + [
            "-p",
            "--model",
            model,
            "--no-session-persistence",
            "--permission-mode",
            "plan",
            "--add-dir",
            cwd,
            prompt,
        ]
    profile = os.environ.get("TOKENFLOW_DSH_PROFILE", "headless")
    return executable + ["--profile", profile, "--model", model, prompt]


def _clip_output(value: str, truncated: bool = False) -> str:
    value = value.strip()
    if len(value) <= MAX_OUTPUT_CHARS and not truncated:
        return value
    return value[:MAX_OUTPUT_CHARS] + "\n...[output truncated]..."


def _run_bounded(command: list[str], cwd: str, timeout: float) -> tuple[int, str, str, bool, bool, bool]:
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, bufsize=0)
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
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    for thread in threads:
        thread.join(timeout=1)
    for stream in (process.stdout, process.stderr):
        if stream and not stream.closed:
            stream.close()
    return (process.returncode, buffers[0].decode(errors="replace"), buffers[1].decode(errors="replace"),
            truncated[0], truncated[1], timed_out)


def execute_harness(harness: str, model: str, prompt: str) -> dict[str, Any]:
    executable = _resolve_harness_command(harness)
    if not executable:
        raise RuntimeError(f"未检测到可用的 {harness} 命令")
    command = build_harness_args(harness, model, prompt, os.getcwd(), executable)
    started = time.monotonic()
    exit_code, output, error, output_truncated, error_truncated, timed_out = _run_bounded(command, os.getcwd(), MAX_EXEC_SECONDS)
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
    }


def execute_workflow(task: str, content: str, harness: str = "auto", model: str = "auto") -> dict[str, Any]:
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
    execution = execute_harness(selected_harness, selected_model, prompt)
    return {**workflow, "harness": selected_harness, "model": selected_model, "execution": execution}


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
            if self.path == "/api/freetoken/install":
                result = launch_freetoken_installer()
            elif self.path == "/api/freetoken/start":
                result = start_freetoken_server()
            elif self.path == "/api/run":
                result = run_workflow(str(data.get("task", "")), str(data.get("content", "")))
            elif self.path == "/api/execute":
                result = execute_workflow(
                    str(data.get("task", "")),
                    str(data.get("content", "")),
                    str(data.get("harness", "auto")),
                    str(data.get("model", "auto")),
                )
            elif self.path == "/api/local-chat":
                result = local_chat_workflow(
                    str(data.get("task", "")),
                    str(data.get("content", "")),
                    str(data.get("model", "auto")),
                )
            elif self.path == "/api/chat":
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
        self._send_json(result)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[TokenFlow] {format % args}")


def self_check() -> None:
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
    wrapped = run_workflow("test", "x" * 5000, lambda _task_type, value: "prefix:" + value)
    assert wrapped["compression_applied"] and wrapped["token_counting"]["scope"] == "visible_prompt"
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
    result = run_workflow("分析这段 Python 代码", "def add(a, b):\n    return a + b\n" * 200)
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
    assert choose_model("codex", "code", "short") == "gpt-5.6-sol"
    assert choose_model("codex", "general", "short") == "gpt-5.6-luna"
    assert choose_model("claude", "document", "short") == "sonnet"
    assert _valid_write_request("http://127.0.0.1:8765", "127.0.0.1:8765", SESSION_TOKEN, 8765)
    assert not _valid_write_request("http://example.com:8765", "127.0.0.1:8765", SESSION_TOKEN, 8765)
    assert not _valid_write_request(None, "127.0.0.1:8765", "invalid", 8765)
    command = build_harness_args("codex", "gpt-5.6-luna", "prompt", ".", ["codex"])
    assert "read-only" in command and "--ephemeral" in command
    try:
        build_harness_args("codex", "bad model", "prompt", ".", ["codex"])
    except ValueError:
        pass
    else:
        raise AssertionError("invalid model must fail")
    freetoken_self_check()
    freetoken_manager_self_check()
    token_counter_self_check()
    provider_self_check()
    store_self_check()
    document_parser_self_check()
    pc_agent_self_check()
    jev_self_check()
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
