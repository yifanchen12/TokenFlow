"""Explicit, allow-listed PC actions with dry-run as the safe default."""

from __future__ import annotations

import os
import time
from typing import Any


ALLOWED_ACTIONS = {"move", "click", "type", "key", "wait"}
ALLOWED_BUTTONS = {"left", "middle", "right"}


class PCAgentError(ValueError):
    pass


def validate_actions(actions: Any) -> list[dict[str, Any]]:
    if not isinstance(actions, list) or len(actions) > 50:
        raise PCAgentError("actions 必须是最多 50 项的数组")
    validated = []
    for action in actions:
        if not isinstance(action, dict) or action.get("type") not in ALLOWED_ACTIONS:
            raise PCAgentError("存在不支持的 PC action")
        item = dict(action)
        kind = item["type"]
        if kind in {"move", "click"}:
            if not all(isinstance(item.get(key), (int, float)) for key in ("x", "y")):
                raise PCAgentError(f"{kind} 需要 x/y 坐标")
            if not (0 <= item["x"] <= 10000 and 0 <= item["y"] <= 10000):
                raise PCAgentError("坐标超出安全范围")
        if kind == "click" and item.get("button", "left") not in ALLOWED_BUTTONS:
            raise PCAgentError("click button 不在白名单中")
        if kind == "type" and (not isinstance(item.get("text"), str) or len(item["text"]) > 2000):
            raise PCAgentError("type 需要不超过 2000 字符的 text")
        if kind == "key" and (not isinstance(item.get("key"), str) or len(item["key"]) > 40):
            raise PCAgentError("key 需要有限长度的按键名")
        if kind == "wait" and (not isinstance(item.get("seconds", 0), (int, float)) or not 0 <= item["seconds"] <= 10):
            raise PCAgentError("wait 时间必须在 0 到 10 秒之间")
        validated.append(item)
    return validated


def plan_actions(actions: Any) -> dict[str, Any]:
    validated = validate_actions(actions)
    return {"dry_run": True, "requires_confirmation": True, "actions": validated}


def execute_actions(actions: Any) -> dict[str, Any]:
    validated = validate_actions(actions)
    if os.environ.get("TOKENFLOW_PC_AGENT_EXECUTE") != "1":
        return {"ok": False, "dry_run": True, "requires_confirmation": True, "actions": validated, "error": "设置 TOKENFLOW_PC_AGENT_EXECUTE=1 后才允许执行"}
    try:
        import pyautogui
    except ImportError as exc:
        raise PCAgentError("PC 执行需要可选依赖 pyautogui") from exc
    for action in validated:
        kind = action["type"]
        if kind == "move":
            pyautogui.moveTo(action["x"], action["y"], duration=0.1)
        elif kind == "click":
            pyautogui.click(action["x"], action["y"], button=action.get("button", "left"))
        elif kind == "type":
            pyautogui.write(action["text"], interval=0.01)
        elif kind == "key":
            pyautogui.press(action["key"])
        elif kind == "wait":
            time.sleep(action["seconds"])
    return {"ok": True, "dry_run": False, "actions_executed": len(validated)}


def self_check() -> None:
    assert plan_actions([{"type": "key", "key": "enter"}])["requires_confirmation"]
    try:
        validate_actions([{"type": "shell", "command": "whoami"}])
    except PCAgentError:
        pass
    else:
        raise AssertionError("shell actions must be rejected")
