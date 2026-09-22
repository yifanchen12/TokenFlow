"""TokenFlow: a dependency-free local MVP for low-token agent workflows."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from email.parser import BytesParser
from email.policy import default as email_default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

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
from document_parser import DocumentParseError, parse_document_bytes, parse_document_file
from jev_provider import JevError, decide as jev_decide, self_check as jev_self_check
from model_providers import ProviderError, provider_status, self_check as provider_self_check, unified_chat
from pc_agent import PCAgentError, execute_actions, plan_actions, self_check as pc_agent_self_check
from tokenflow_store import DocumentStore, self_check as store_self_check


MAX_COMPRESSED_CHARS = 2400
MAX_EXEC_SECONDS = 90
MAX_OUTPUT_CHARS = 20000
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


def run_workflow(task: str, content: str) -> dict[str, Any]:
    task = task.strip()
    content = content.strip()
    if not task:
        raise ValueError("task 不能为空")
    if not content:
        raise ValueError("content 不能为空")

    task_type = classify_task(task, content)
    compressed = compact_content(content)
    baseline_count = count_tokens(content)
    optimized_count = count_tokens(compressed)
    baseline_tokens = baseline_count["count"]
    optimized_tokens = optimized_count["count"]
    saved_tokens = max(0, baseline_tokens - optimized_tokens)
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
        },
        "saved_tokens": saved_tokens,
        "saved_ratio": saved_ratio,
        "note": "Token 数为启发式估算，不等同于具体云端模型的实际计费 Token。",
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
        "你正在执行 TokenFlow 预处理后的任务。\n"
        f"任务类型：{task_type}\n"
        f"用户任务：{task}\n\n"
        "请基于以下已压缩内容完成任务。当前调用默认处于只读或计划模式，"
        "除非用户明确要求，不要修改文件、删除数据或执行高风险操作。\n\n"
        "--- 预处理内容 ---\n"
        f"{compressed}\n"
        "--- 预处理内容结束 ---"
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


def _clip_output(value: str) -> str:
    value = value.strip()
    if len(value) <= MAX_OUTPUT_CHARS:
        return value
    return value[:MAX_OUTPUT_CHARS] + "\n...[output truncated]..."


def execute_harness(harness: str, model: str, prompt: str) -> dict[str, Any]:
    executable = _resolve_harness_command(harness)
    if not executable:
        raise RuntimeError(f"未检测到可用的 {harness} 命令")
    command = build_harness_args(harness, model, prompt, os.getcwd(), executable)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=MAX_EXEC_SECONDS,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "harness": harness,
            "model": model,
            "duration_ms": round((time.monotonic() - started) * 1000),
            "error": f"执行超过 {MAX_EXEC_SECONDS} 秒，已终止",
            "output": _clip_output(str(exc.stdout or "")),
        }
    return {
        "ok": completed.returncode == 0,
        "harness": harness,
        "model": model,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "exit_code": completed.returncode,
        "output": _clip_output(completed.stdout),
        "error": _clip_output(completed.stderr),
    }


def execute_workflow(task: str, content: str, harness: str = "auto", model: str = "auto") -> dict[str, Any]:
    workflow = run_workflow(task, content)
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


def local_chat_workflow(task: str, content: str, requested_model: str = "auto") -> dict[str, Any]:
    workflow = run_workflow(task, content)
    client = _freetoken_client()
    health = client.health()
    if not health["available"]:
        raise FreeTokenError(health["error"])
    models = client.list_models()
    selected_model = choose_local_model(models, workflow["task_type"], requested_model)
    response = client.chat(
        selected_model,
        [
            {"role": "system", "content": "你是 TokenFlow 的本地预处理模型，请简洁、准确地完成任务。"},
            {"role": "user", "content": f"任务：{task.strip()}\n\n内容：\n{workflow['result']}"},
        ],
    )
    return {
        **workflow,
        "provider": "freetoken",
        "base_url": client.base_url,
        "model": selected_model,
        "local_result": response_text(response),
    }


def unified_chat_workflow(task: str, content: str, provider: str = "auto", model: str = "auto") -> dict[str, Any]:
    workflow = run_workflow(task, content)
    routed = unified_chat(task.strip(), workflow["result"], workflow["task_type"], provider, model)
    return {**workflow, **routed}


def _document_store() -> DocumentStore:
    return DocumentStore()


LEGACY_PAGE = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TokenFlow MVP</title>
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;max-width:960px;margin:40px auto;padding:0 20px;color:#172033;background:#f6f8fb}
main{background:#fff;padding:28px;border-radius:14px;box-shadow:0 8px 30px #17203318}
h1{margin-top:0}.muted{color:#5d6879}label{display:block;font-weight:600;margin:16px 0 6px}
input,textarea,button{font:inherit;width:100%;box-sizing:border-box;border:1px solid #cbd3df;border-radius:8px;padding:10px}
textarea{min-height:220px;resize:vertical}button{margin-top:18px;background:#2457d6;color:white;border:0;cursor:pointer}
pre{white-space:pre-wrap;background:#101827;color:#e6edf7;padding:16px;border-radius:8px;overflow:auto}
</style>
<main><h1>TokenFlow</h1><p class="muted">本地低 Token 执行流程 V2。先压缩内容，再按需交给本机 Harness。</p>
<label for="task">任务</label><input id="task" value="总结下面的文档并提取关键结论">
<label for="content">内容</label><textarea id="content" placeholder="粘贴文档、代码或表格内容"></textarea>
<label for="harness">Harness</label><select id="harness"><option value="auto">自动选择</option><option value="codex">Codex</option><option value="claude">Claude</option><option value="dsh">DSH</option></select>
<label for="provider">模型提供商</label><select id="provider"><option value="auto">自动路由</option><option value="freetoken">FreeToken</option><option value="ollama">Ollama</option><option value="laya">Laya 兼容端点</option><option value="cloud">云端兼容端点</option></select>
<label for="model">模型</label><input id="model" value="auto" placeholder="auto 或具体模型名，例如 gpt-5.6-luna">
<button onclick="runLocal()">仅本地处理</button><button onclick="localModel()">调用 FreeToken 本地模型</button><button onclick="unifiedModel()">统一模型路由</button><button onclick="execute()">交给 Harness 执行</button>
<button onclick="installFreeToken()">安装 FreeToken</button><button onclick="startFreeToken()">启动 FreeToken</button>
<p id="status" class="muted">正在检测本机 Harness 和 FreeToken...</p><h2>结果</h2><pre id="result">等待执行...</pre></main>
<script>
const result=document.getElementById('result');
function request(path){return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:document.getElementById('task').value,content:document.getElementById('content').value,harness:document.getElementById('harness').value,provider:document.getElementById('provider').value,model:document.getElementById('model').value||'auto'})}).then(async r=>({status:r.status,data:await r.json()}))}
async function runLocal(){result.textContent='本地处理中...';const r=await request('/api/run');result.textContent=JSON.stringify(r.data,null,2)}
async function localModel(){result.textContent='正在调用 FreeToken 本地模型...';const r=await request('/api/local-chat');result.textContent=JSON.stringify(r.data,null,2)}
async function unifiedModel(){result.textContent='正在按提供商路由模型...';const r=await request('/api/chat');result.textContent=JSON.stringify(r.data,null,2)}
async function execute(){result.textContent='正在交给 Harness 执行...';const r=await request('/api/execute');result.textContent=JSON.stringify(r.data,null,2)}
async function installFreeToken(){result.textContent='正在打开 FreeToken 安装器...';const r=await fetch('/api/freetoken/install',{method:'POST'}).then(async r=>({status:r.status,data:await r.json()}));result.textContent=JSON.stringify(r.data,null,2)}
async function startFreeToken(){result.textContent='正在启动 FreeToken...';const r=await fetch('/api/freetoken/start',{method:'POST'}).then(async r=>({status:r.status,data:await r.json()}));result.textContent=JSON.stringify(r.data,null,2);setTimeout(loadHarnesses,1500)}
async function loadHarnesses(){try{const data=await fetch('/api/harnesses').then(r=>r.json());const local=await fetch('/api/local-models').then(r=>r.json());const ft=await fetch('/api/freetoken').then(r=>r.json());const tokenizer=await fetch('/api/tokenizer').then(r=>r.json());const harnessText=Object.entries(data).map(([k,v])=>k+': '+(v.installed?'已安装':'未找到')+(v.note?'，'+v.note:'')).join(' | ');const localText=local.available?'FreeToken API: '+local.models.length+' 个模型':'FreeToken API: 未连接';const ftText=ft.installed?'ft '+(ft.version||'已安装')+(ft.running?'，服务运行中':'，服务未启动'):'ft 未安装';const tokenizerText='Tokenizer: '+tokenizer.backend+(tokenizer.exact?'（exact）':'（heuristic）');document.getElementById('status').textContent=harnessText+' | '+ftText+' | '+localText+' | '+tokenizerText}catch(e){document.getElementById('status').textContent='本地后端检测失败：'+e}}
loadHarnesses();
</script>
"""

from ui_page import PAGE


class TokenFlowHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path == "/health":
            self._send_json({"status": "ok"})
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
            self._send_json(_document_store().status())
            return
        if self.path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path not in (
            "/api/run",
            "/api/execute",
            "/api/local-chat",
            "/api/chat",
            "/api/parse",
            "/api/index",
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
            if size > 2_000_000:
                raise ValueError("请求内容不能超过 2 MB")
            raw = self.rfile.read(size)
            content_type = self.headers.get("Content-Type", "application/json")
            if content_type.lower().startswith("multipart/form-data"):
                envelope = (
                    f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw
                )
                message = BytesParser(policy=email_default).parsebytes(envelope)
                attachment = next((part for part in message.walk() if part is not message and part.get_filename()), None)
                if not attachment:
                    raise ValueError("multipart 请求缺少文件字段")
                data = {"filename": attachment.get_filename(), "file_bytes": attachment.get_payload(decode=True) or b""}
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
