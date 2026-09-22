"""Windows-side FreeToken discovery and user-triggered lifecycle helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


DEFAULT_MODEL_NAME = "Qwen3.6-35B-A3B-NVFP4"
DEFAULT_PORT = 1919


def _windows_localappdata() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))


def find_ft_executable() -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get("TOKENFLOW_FT_PATH")
    if configured:
        candidates.append(Path(configured))
    marker = _windows_localappdata() / "FreeToken" / "ft-bin.txt"
    if marker.is_file():
        try:
            candidates.append(Path(marker.read_text(encoding="utf-8").strip()))
        except OSError:
            pass
    candidates.extend(
        [
            _windows_localappdata() / "FreeToken" / "venv" / "Scripts" / "ft.exe",
            Path("ft.exe"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def find_installer() -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get("TOKENFLOW_FREETOKEN_INSTALLER")
    if configured:
        candidates.append(Path(configured))
    project_dir = Path(__file__).resolve().parent
    candidates.extend(
        [
            project_dir / "FreeToken-Setup-win-x64.exe",
            Path.home() / "Downloads" / "FreeToken-Setup-win-x64.exe",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def find_model() -> Path | None:
    candidates: list[Path] = []
    configured = os.environ.get("TOKENFLOW_FREETOKEN_MODEL")
    if configured:
        candidates.append(Path(configured))
    candidates.extend(
        [
            Path(__file__).resolve().parent / "models" / DEFAULT_MODEL_NAME,
        ]
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return None


def _health(base_url: str = "http://127.0.0.1:1919") -> bool:
    try:
        with urlopen(base_url.rstrip("/") + "/health", timeout=1.5) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def _version(ft_path: Path | None) -> str | None:
    if not ft_path:
        return None
    try:
        completed = subprocess.run(
            [str(ft_path), "--version"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = (completed.stdout or completed.stderr).strip()
    return value or None


def status() -> dict[str, Any]:
    ft_path = find_ft_executable()
    installer = find_installer()
    model = find_model()
    return {
        "installed": ft_path is not None,
        "ft_path": str(ft_path) if ft_path else None,
        "version": _version(ft_path),
        "installer_available": installer is not None,
        "installer_path": str(installer) if installer else None,
        "model_available": model is not None,
        "model_path": str(model) if model else None,
        "running": _health(),
        "port": DEFAULT_PORT,
    }


def launch_installer() -> dict[str, Any]:
    installer = find_installer()
    if not installer:
        raise RuntimeError(
            "未找到 FreeToken 安装包。请将 FreeToken-Setup-win-x64.exe 放到 TokenFlow 目录，"
            "或设置 TOKENFLOW_FREETOKEN_INSTALLER。"
        )
    if sys.platform != "win32":
        raise RuntimeError("FreeToken Desktop 安装包只能在 Windows 上运行")
    subprocess.Popen([str(installer)], cwd=str(installer.parent), shell=False)
    return {"ok": True, "action": "installer_launched", "installer": str(installer)}


def start_server() -> dict[str, Any]:
    if _health():
        return {"ok": True, "action": "already_running", "port": DEFAULT_PORT}
    ft_path = find_ft_executable()
    model = find_model()
    if not ft_path:
        raise RuntimeError("未检测到 ft.exe，请先点击安装 FreeToken")
    if not model:
        raise RuntimeError("未找到 FreeToken 模型，请设置 TOKENFLOW_FREETOKEN_MODEL")
    command = [str(ft_path), "serve", "--model", str(model), "--port", str(DEFAULT_PORT)]
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(
        command,
        cwd=str(model.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
        creationflags=creationflags,
    )
    return {"ok": True, "action": "server_started", "command": command, "model": str(model)}


def self_check() -> None:
    assert DEFAULT_PORT == 1919
    assert DEFAULT_MODEL_NAME.endswith("NVFP4")
