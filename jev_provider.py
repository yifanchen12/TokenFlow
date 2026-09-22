"""Explicit Jev decision API adapter; never called implicitly as a chat model."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class JevError(RuntimeError):
    pass


def decide(payload: dict[str, Any]) -> dict[str, Any]:
    key = os.environ.get("JEV_API_KEY", "").strip()
    if not key:
        raise JevError("未配置 JEV_API_KEY")
    url = os.environ.get("TOKENFLOW_JEV_URL", "https://www.jevai.org/api/v1/decisions/tool-guard").strip()
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            value = json.loads(response.read(2 * 1024 * 1024).decode("utf-8"))
    except HTTPError as exc:
        raise JevError(f"Jev HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JevError("无法连接 Jev 决策 API") from exc
    if not isinstance(value, dict):
        raise JevError("Jev 返回的 JSON 顶层必须是对象")
    return value


def self_check() -> None:
    assert "JEV_API_KEY" in "JEV_API_KEY"
