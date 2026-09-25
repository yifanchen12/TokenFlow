"""Unified OpenAI-compatible providers for local and cloud model endpoints."""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import dataclass
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


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
    "omniroute": ProviderSpec("omniroute", "TOKENFLOW_OMNIROUTE_URL", "TOKENFLOW_OMNIROUTE_API_KEY", "http://127.0.0.1:20128/v1", "local_or_remote"),
    "laya": ProviderSpec("laya", "TOKENFLOW_LAYA_URL", "TOKENFLOW_LAYA_API_KEY", None, "local_or_remote"),
    "cloud": ProviderSpec("cloud", "TOKENFLOW_CLOUD_URL", "TOKENFLOW_CLOUD_API_KEY", None, "cloud"),
}

_omniroute_session: tuple[str, str] | None = None
_omniroute_lock = Lock()


def _base_url(spec: ProviderSpec) -> str | None:
    value = os.environ.get(spec.url_env, spec.default_url)
    return value.strip().rstrip("/") if value else None


def is_loopback_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        return parsed.hostname.lower() == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _validate_omniroute(url: str | None, key: str) -> str:
    if not url:
        raise ProviderError("未配置 OmniRoute 地址：TOKENFLOW_OMNIROUTE_URL")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
                 and parsed.port != 0 and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment and parsed.path == "/v1"
                 and not any(char.isspace() for char in url) and "\\" not in url)
    except ValueError:
        valid = False
    if not valid or (not is_loopback_url(url) and parsed.scheme != "https"):
        raise ProviderError("OmniRoute 地址必须是本机 HTTP(S) 或远程 HTTPS 的 /v1 地址，且不能包含凭据、查询或片段")
    if not is_loopback_url(url) and not key:
        raise ProviderError("远程 OmniRoute 必须设置 TOKENFLOW_OMNIROUTE_API_KEY")
    return url


def omniroute_key() -> str:
    with _omniroute_lock:
        session = _omniroute_session
    return session[1] if session is not None else os.environ.get("TOKENFLOW_OMNIROUTE_API_KEY", "").strip()


def omniroute_config() -> tuple[str, str]:
    with _omniroute_lock:
        session = _omniroute_session
    if session is not None:
        return session
    key = os.environ.get("TOKENFLOW_OMNIROUTE_API_KEY", "").strip()
    return _validate_omniroute(_base_url(PROVIDERS["omniroute"]), key), key


def omniroute_base_url() -> str:
    return omniroute_config()[0]


def configure_omniroute(url: str, key: str | None = None) -> None:
    if not isinstance(url, str) or len(url) > 2048 or not url.strip():
        raise ValueError("OmniRoute 地址不能为空且不能超过 2048 字符")
    if key is not None and (not isinstance(key, str) or len(key) > 8192):
        raise ValueError("OmniRoute Key 格式无效")
    url = url.strip().rstrip("/")
    global _omniroute_session
    with _omniroute_lock:
        previous_key = _omniroute_session[1] if _omniroute_session is not None else os.environ.get("TOKENFLOW_OMNIROUTE_API_KEY", "").strip()
        selected_key = key.strip() if key and key.strip() else previous_key
        try:
            _validate_omniroute(url, selected_key)
        except ProviderError as exc:
            raise ValueError(str(exc)) from exc
        _omniroute_session = (url, selected_key)


def _auto_candidates(order: list[str]) -> list[str]:
    return [name for name in order if name in PROVIDERS and name not in {"cloud", "omniroute"} and is_loopback_url(_base_url(PROVIDERS[name]))]


def chat_messages(task: str, content: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "你是 TokenFlow 的模型路由后端，请简洁、准确地完成任务。"},
        {"role": "user", "content": f"任务：{task}\n\n内容：\n{content}"},
    ]


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
        if name == "omniroute":
            self.base_url, self._omniroute_key = omniroute_config()
        else:
            self.base_url, self._omniroute_key = _base_url(self.spec), None
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
            key = self._omniroute_key if self.spec.name == "omniroute" else os.environ.get(self.spec.key_env, "").strip()
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
        if name == "omniroute":
            try:
                url, key = omniroute_config()
            except ProviderError as exc:
                result[name] = {"configured": False, "key_configured": bool(omniroute_key()),
                                "url": None, "kind": spec.kind, "models": [], "available": False, "error": str(exc)}
                continue
        configured = bool(url)
        key_configured = bool(key) if name == "omniroute" else bool(os.environ.get(spec.key_env, "")) if spec.key_env else True
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
    from unittest.mock import patch

    assert choose_model(["Qwen3.6", "deepseek-coder"], "code") == "deepseek-coder"
    assert response_text({"choices": [{"message": {"content": "ok"}}]}) == "ok"
    with patch.dict(os.environ, {
        "TOKENFLOW_FREETOKEN_URL": "https://example.com",
        "TOKENFLOW_OLLAMA_URL": "http://127.0.0.1:11434/v1",
        "TOKENFLOW_LAYA_URL": "https://example.com",
        "TOKENFLOW_CLOUD_URL": "http://127.0.0.1:9443/v1",
        "TOKENFLOW_OMNIROUTE_URL": "http://127.0.0.1:20128/v1",
    }):
        assert _auto_candidates(["freetoken", "ollama", "laya", "cloud", "omniroute"]) == ["ollama"]
        with patch("model_providers.OpenAICompatibleProvider") as client_class:
            client_class.return_value.models.return_value = ["local-model"]
            client_class.return_value.chat.return_value = {"choices": [{"message": {"content": "ok"}}]}
            assert unified_chat("task", "content", "general")["provider"] == "ollama"
            client_class.assert_called_once_with("ollama", timeout=20)
            client_class.reset_mock()
            assert unified_chat("task", "content", "general", provider="cloud")["provider"] == "cloud"
            client_class.assert_called_once_with("cloud", timeout=20)
            client_class.reset_mock()
            assert unified_chat("task", "content", "general", provider="omniroute")["provider"] == "omniroute"
            client_class.assert_called_once_with("omniroute", timeout=90)
            client_class.reset_mock()
            routed = unified_chat("task", "content", "general", provider="omniroute", model="ollama-local/qwen3.5:9b")
            assert routed["model"] == "ollama-local/qwen3.5:9b"
            client_class.return_value.models.assert_not_called()
            client_class.return_value.chat.assert_called_once()
        assert omniroute_base_url() == "http://127.0.0.1:20128/v1"
    with patch.dict(os.environ, {"TOKENFLOW_OMNIROUTE_URL": "http://remote.example/v1", "TOKENFLOW_OMNIROUTE_API_KEY": ""}):
        try:
            omniroute_base_url()
        except ProviderError:
            pass
        else:
            raise AssertionError("remote OmniRoute HTTP must fail")
    with patch(__name__ + "._omniroute_session", None):
        configure_omniroute("http://127.0.0.1:20128/v1", "session-secret")
        client = OpenAICompatibleProvider("omniroute")
        assert client.base_url == "http://127.0.0.1:20128/v1" and client._omniroute_key == "session-secret"
        configure_omniroute("https://gateway.example/v1", "")
        assert omniroute_config() == ("https://gateway.example/v1", "session-secret")
        assert client.base_url == "http://127.0.0.1:20128/v1"  # a request keeps one URL/Key snapshot
        try:
            configure_omniroute("http://gateway.example/v1", "another-secret")
        except ValueError:
            pass
        else:
            raise AssertionError("remote OmniRoute HTTP must fail")
    assert is_loopback_url("http://[::1]:1919/v1")
    assert not is_loopback_url("http://127.0.0.1.example.com/v1")


def unified_chat(task: str, content: str, task_type: str, provider: str = "auto", model: str = "auto") -> dict[str, Any]:
    order = [item.strip() for item in os.environ.get("TOKENFLOW_PROVIDER_ORDER", "freetoken,ollama,laya,cloud").split(",") if item.strip()]
    candidates = [provider] if provider != "auto" else _auto_candidates(order)
    if provider == "auto" and not candidates:
        raise ProviderError("自动路由未检测到本机模型端点；远程调用请明确选择提供商")
    errors: list[str] = []
    for name in candidates:
        if name not in PROVIDERS:
            errors.append(f"未知提供商：{name}")
            continue
        try:
            client = OpenAICompatibleProvider(name, timeout=90 if name == "omniroute" else 20)
            selected = model if name == "omniroute" and model != "auto" else choose_model(client.models(), task_type, model)
            response = client.chat(selected, chat_messages(task, content))
            return {"provider": name, "model": selected, "result": response_text(response), "base_url": client.base_url}
        except ProviderError as exc:
            errors.append(f"{name}: {exc}")
    raise ProviderError("没有可用的模型提供商：" + "；".join(errors))
