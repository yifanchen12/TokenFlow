"""User-triggered OmniRoute setup and local lifecycle; never persists Endpoint Keys."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

from model_providers import ProviderError, omniroute_config, omniroute_key


LOCAL_URL = "http://127.0.0.1:20128/v1"
UPSTREAM_URL = "https://github.com/diegosouzapw/OmniRoute"


def _health() -> bool:
    try:
        with build_opener(ProxyHandler({})).open("http://127.0.0.1:20128/api/health", timeout=1.5) as response:
            return 200 <= response.status < 300
    except (OSError, URLError):
        return False


def status() -> dict[str, Any]:
    try:
        url, key = omniroute_config()
        error = None
    except ProviderError as exc:
        url, key, error = None, omniroute_key(), str(exc)
    return {
        "installed": shutil.which("omniroute") is not None,
        "npm_available": shutil.which("npm") is not None,
        "running": _health(),
        "url": url,
        "key_configured": bool(key),
        "local_start_available": url == LOCAL_URL,
        "install_guide": UPSTREAM_URL,
        **({"error": error} if error else {}),
    }


def install() -> dict[str, Any]:
    if shutil.which("omniroute"):
        return {"ok": True, "action": "already_installed"}
    if sys.platform != "win32":
        raise RuntimeError("自动安装目前仅支持 Windows；请打开 OmniRoute 官方安装说明")
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("未找到 npm；请先按官方说明安装兼容的 Node.js")
    command = [os.environ.get("ComSpec", "cmd.exe"), "/d", "/s", "/k",
               f'""{npm}" install -g omniroute"']
    subprocess.Popen(command, cwd=str(Path.home()), shell=False,
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    return {"ok": True, "action": "installer_launched"}


def start() -> dict[str, Any]:
    if omniroute_config()[0] != LOCAL_URL:
        raise RuntimeError("页面启动仅支持默认本机地址；自定义网关请自行启动")
    if _health():
        return {"ok": True, "action": "already_running"}
    executable = shutil.which("omniroute")
    if not executable:
        raise RuntimeError("未检测到 OmniRoute，请先安装")
    environment = os.environ.copy()
    environment.pop("TOKENFLOW_OMNIROUTE_API_KEY", None)
    environment["OMNIROUTE_SERVER_HOST"] = "127.0.0.1"
    subprocess.Popen([executable, "serve", "--no-open", "--no-tray"], cwd=str(Path.home()),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     shell=False, env=environment,
                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                     | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"ok": True, "action": "server_started"}


def self_check() -> None:
    from unittest.mock import patch

    with patch("omniroute_manager.shutil.which", side_effect=lambda name: "C:/node/" + name + ".cmd"), \
         patch("omniroute_manager.omniroute_config", return_value=(LOCAL_URL, "test-secret")), \
         patch("omniroute_manager._health", return_value=False), \
         patch("omniroute_manager.subprocess.Popen") as launch:
        assert status()["installed"] and status()["npm_available"]
        assert "key" not in install()
        launch.assert_not_called()  # installed binaries are never reinstalled
        start()
        command = launch.call_args.args[0]
        assert command[1:] == ["serve", "--no-open", "--no-tray"]
        assert launch.call_args.kwargs["env"]["OMNIROUTE_SERVER_HOST"] == "127.0.0.1"
        assert "TOKENFLOW_OMNIROUTE_API_KEY" not in launch.call_args.kwargs["env"]
        launch.reset_mock()
    with patch("omniroute_manager.sys.platform", "win32"), \
         patch("omniroute_manager.shutil.which", side_effect=lambda name: None if name == "omniroute" else "C:/node/npm.cmd"), \
         patch("omniroute_manager.subprocess.Popen") as launch:
        assert install()["action"] == "installer_launched"
        assert "omniroute" in launch.call_args.args[0][-1]
    with patch("omniroute_manager.shutil.which", return_value=None):
        try:
            install()
        except RuntimeError:
            pass
        else:
            raise AssertionError("missing npm must fail")
