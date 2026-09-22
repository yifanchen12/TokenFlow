"""Unified OpenAI-compatible providers for local and cloud model endpoints."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ProviderError(RuntimeError):
    """Safe provider error without credentials or request bodies."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    url_env: str
    key_env: str | None
    default_url: str | None
    kind: str


PROVIDERS = {
    "freetoken": ProviderSpec("freetoken", "TOKENFLOW_FREETOKEN_URL", None, "http://127.0.0.1:1919", "local"),
    "ollama": ProviderSpec("ollama", "TOKENFLOW_OLLAMA_URL", None, "http://127.0.0.1:11434/v1", "local"),
    "laya": ProviderSpec("laya", "TOKENFLOW_LAYA_URL", "TOKENFLOW_LAYA_API_KEY", None, "local_or_remote"),
    "cloud": ProviderSpec("cloud", "TOKENFLOW_CLOUD_URL", "TOKENFLOW_CLOUD_API_KEY", None, "cloud"),
}


def _base_url(spec: ProviderSpec) -> str | None:
    value = os.environ.get(spec.url_env, spec.default_url)
    return value.strip().rstrip("/") if value else None


def _read_json(response: Any) -> dict[str, Any]:
    try:
        value = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProviderError("提供商返回了无效 JSON") from exc
    if not isinstance(value, dict):
        raise ProviderError("提供商返回的 JSON 顶层必须是对象")
    return value


class OpenAICompatibleProvider:
    def __init__(self, name: str, timeout: float = 20.0):
        if name not in PROVIDERS:
            raise ProviderError(f"未知提供商：{name}")
        self.spec = PROVIDERS[name]
        self.base_url = _base_url(self.spec)
        self.timeout = timeout

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.base_url:
            raise ProviderError(f"未配置 {self.spec.name} 的端点：{self.spec.url_env}")
        headers = {"Accept": "application/json"}
        body = None
        method = "GET"
        if payload is not None:
            method = "POST"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.spec.key_env:
            key = os.environ.get(self.spec.key_env, "").strip()
            if key:
                headers["Authorization"] = f"Bearer {key}"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return _read_json(response)
        except HTTPError as exc:
            raise ProviderError(f"{self.spec.name} HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ProviderError(f"无法连接 {self.spec.name}：{self.base_url}") from exc

    def models(self) -> list[str]:
        value = self._request("/models")
        data = value.get("data", [])
        return [item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]

    def chat(self, model: str, messages: list[dict[str, Any]], max_tokens: int = 1024) -> dict[str, Any]:
        if not model or any(char.isspace() for char in model):
            raise ProviderError("模型名不能为空且不能包含空格")
        return self._request(
            "/chat/completions",
            {"model": model, "messages": messages, "max_tokens": max_tokens, "stream": False},
        )


def provider_status() -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name, spec in PROVIDERS.items():
        url = _base_url(spec)
        configured = bool(url)
        key_configured = bool(os.environ.get(spec.key_env, "")) if spec.key_env else True
        result[name] = {
            "configured": configured,
            "key_configured": key_configured,
            "url": url.split("?", 1)[0] if url else None,
            "kind": spec.kind,
            "models": [],
        }
        if configured:
            try:
                result[name]["models"] = OpenAICompatibleProvider(name, timeout=2).models()
                result[name]["available"] = True
            except ProviderError as exc:
                result[name]["available"] = False
                result[name]["error"] = str(exc)
        else:
            result[name]["available"] = False
            result[name]["error"] = f"未配置 {spec.url_env}"
    return result


def choose_model(models: list[str], task_type: str, requested: str = "auto") -> str:
    if requested != "auto":
        if requested not in models:
            raise ProviderError(f"提供商未返回模型：{requested}")
        return requested
    if not models:
        raise ProviderError("提供商没有返回可用模型")
    preferences = {
        "code": ("code", "coder", "deepseek", "qwen"),
        "document": ("qwen", "llama", "mistral"),
        "table": ("qwen", "llama", "mistral"),
        "general": ("qwen", "llama", "mistral"),
    }.get(task_type, ("qwen", "llama", "mistral"))
    for preference in preferences:
        for model in models:
            if preference in model.lower():
                return model
    return models[0]


def response_text(response: dict[str, Any]) -> str:
    choices = response.get("choices", [])
    if not isinstance(choices, list) or not choices:
        raise ProviderError("提供商响应缺少 choices")
    message = choices[0].get("message", {})
    content = message.get("content", "") if isinstance(message, dict) else ""
    return content if isinstance(content, str) else str(content)


def self_check() -> None:
    assert choose_model(["Qwen3.6", "deepseek-coder"], "code") == "deepseek-coder"
    assert response_text({"choices": [{"message": {"content": "ok"}}]}) == "ok"


def unified_chat(task: str, content: str, task_type: str, provider: str = "auto", model: str = "auto") -> dict[str, Any]:
    order = [item.strip() for item in os.environ.get("TOKENFLOW_PROVIDER_ORDER", "freetoken,ollama,laya,cloud").split(",") if item.strip()]
    candidates = [provider] if provider != "auto" else order
    errors: list[str] = []
    for name in candidates:
        if name not in PROVIDERS:
            errors.append(f"未知提供商：{name}")
            continue
        try:
            client = OpenAICompatibleProvider(name, timeout=20)
            selected = choose_model(client.models(), task_type, model)
            response = client.chat(
                selected,
                [
                    {"role": "system", "content": "你是 TokenFlow 的模型路由后端，请简洁、准确地完成任务。"},
                    {"role": "user", "content": f"任务：{task}\n\n内容：\n{content}"},
                ],
            )
            return {"provider": name, "model": selected, "result": response_text(response), "base_url": client.base_url}
        except ProviderError as exc:
            errors.append(f"{name}: {exc}")
    raise ProviderError("没有可用的模型提供商：" + "；".join(errors))
