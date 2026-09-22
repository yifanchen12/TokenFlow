"""Small standard-library client for a local FreeToken OpenAI-compatible server."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_FREETOKEN_URL = "http://127.0.0.1:1919"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MODEL_NAME_RE = re.compile(r"^[^\s]{1,240}$")


class FreeTokenError(RuntimeError):
    """A readable local-backend error safe to return from the TokenFlow API."""


def normalize_base_url(value: str | None) -> str:
    return (value or DEFAULT_FREETOKEN_URL).strip().rstrip("/")


def _read_response(response: Any) -> dict[str, Any]:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise FreeTokenError("FreeToken 响应超过 4 MB，已拒绝读取")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FreeTokenError("FreeToken 返回的不是有效 JSON") from exc
    if not isinstance(parsed, dict):
        raise FreeTokenError("FreeToken 返回的 JSON 顶层必须是对象")
    return parsed


class FreeTokenClient:
    def __init__(self, base_url: str | None = None, timeout: float = 8.0):
        self.base_url = normalize_base_url(base_url)
        self.timeout = timeout

    def _request_json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        method = "GET"
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            method = "POST"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return _read_response(response)
        except HTTPError as exc:
            detail = exc.read(1200).decode("utf-8", errors="replace")
            raise FreeTokenError(f"FreeToken HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise FreeTokenError(f"无法连接 FreeToken：{self.base_url}") from exc

    def health(self) -> dict[str, Any]:
        try:
            response = self._request_json("/health")
        except FreeTokenError as exc:
            return {"available": False, "base_url": self.base_url, "error": str(exc)}
        return {"available": True, "base_url": self.base_url, "response": response}

    def list_models(self) -> dict[str, Any]:
        return self._request_json("/v1/models")

    def chat(self, model: str, messages: list[dict[str, Any]], max_tokens: int = 1024) -> dict[str, Any]:
        if not MODEL_NAME_RE.fullmatch(model):
            raise FreeTokenError("模型名不能为空且不能包含空格")
        if not messages:
            raise FreeTokenError("messages 不能为空")
        return self._request_json(
            "/v1/chat/completions",
            {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "stream": False,
            },
        )


def model_ids(model_response: dict[str, Any]) -> list[str]:
    data = model_response.get("data", [])
    if not isinstance(data, list):
        return []
    return [item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]


def choose_model(model_response: dict[str, Any], task_type: str = "general", requested: str = "auto") -> str:
    models = model_ids(model_response)
    if requested != "auto":
        if requested not in models:
            raise FreeTokenError(f"FreeToken 未提供模型：{requested}")
        return requested
    if not models:
        raise FreeTokenError("FreeToken 没有返回可用模型")
    preferences = {
        "code": ("deepseek", "qwen", "glm"),
        "document": ("qwen", "glm", "deepseek"),
        "table": ("qwen", "glm", "deepseek"),
        "general": ("qwen", "glm", "deepseek"),
    }.get(task_type, ("qwen", "glm", "deepseek"))
    for preference in preferences:
        for model in models:
            if preference in model.lower():
                return model
    return models[0]


def response_text(response: dict[str, Any]) -> str:
    choices = response.get("choices", [])
    if not isinstance(choices, list) or not choices:
        raise FreeTokenError("FreeToken 响应缺少 choices")
    message = choices[0].get("message", {})
    content = message.get("content", "") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return str(content)


def self_check() -> None:
    assert normalize_base_url("http://localhost:1919/") == "http://localhost:1919"
    response = {"data": [{"id": "Qwen3.6-35B-A3B"}, {"id": "DeepSeek-V4-Flash-0731"}]}
    assert choose_model(response, "code") == "DeepSeek-V4-Flash-0731"
    assert choose_model(response, "document") == "Qwen3.6-35B-A3B"
    assert response_text({"choices": [{"message": {"content": "ok"}}]}) == "ok"
