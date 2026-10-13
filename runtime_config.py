"""Saved connections, detected catalogs and Harness bindings; no keys in public data."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from contextlib import contextmanager, closing
from typing import Any

from model_providers import OpenAICompatibleProvider, ProviderError, ProviderSpec, is_loopback_url
from tokenflow_store import _db_path

MODEL_RE = re.compile(r"[A-Za-z0-9._:/+-]{1,160}")
PROTOCOLS = {"openai", "responses", "anthropic"}
HARNESS_PROTOCOLS = {"codex": "responses", "claude": "anthropic", "dsh": "openai"}
ROLES = ("local", "planner", "executor", "reviewer")


def validate_url(value: str) -> str:
    from urllib.parse import urlsplit
    url = value.strip().rstrip("/")
    try:
        p = urlsplit(url)
        valid = p.scheme in {"http", "https"} and p.hostname and p.port != 0 and not (
            p.username or p.password or p.query or p.fragment or "\\" in url or
            any(c.isspace() or ord(c) < 32 for c in url))
    except ValueError:
        valid = False
    if not valid or (not is_loopback_url(url) and p.scheme != "https"):
        raise ValueError("Base URL 必须是本机 HTTP(S) 或远程 HTTPS 地址，不能包含凭据、查询或片段")
    return url


def clean_models(values: Any) -> list[str]:
    if not isinstance(values, list) or len(values) > 1000 or any(
            not isinstance(v, str) or not MODEL_RE.fullmatch(v) or v == "auto" for v in values):
        raise ValueError("模型记录必须是有效模型 ID 数组（最多 1000 项）")
    return list(dict.fromkeys(values))


def _protect(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        # POSIX storage is protected by owner-only directory/file permissions.
        return data
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    dest = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(dest)):
        raise RuntimeError("系统密钥保护失败；请在保存配置的同一用户下运行")
    try:
        return ctypes.string_at(dest.data, dest.size)
    finally:
        kernel.LocalFree(dest.data)


def _json_file(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def native_catalogs() -> dict[str, dict]:
    home = Path.home()
    codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex"))
    config: dict = {}
    try:
        import tomllib
        config = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8-sig"))
    except (ImportError, OSError, ValueError):
        pass
    provider = config.get("model_provider", "openai")
    endpoint = config.get("model_providers", {}).get(provider, {}).get("base_url", "")
    models = []
    # The built-in catalog must not be presented as a custom provider's catalog.
    if provider == "openai":
        models = [m.get("slug", m.get("id")) for m in _json_file(codex_home / "models_cache.json").get("models", [])
                  if isinstance(m, dict) and m.get("visibility", "list") != "hide"]
    if config.get("model"):
        models.insert(0, config["model"])
    default_model, codex_source = config.get("model", "auto"), "本机 config.toml / models_cache.json（缓存目录）"
    database = Path(os.environ.get("TOKENFLOW_CONFIG_DB", _db_path().with_name("connections.db")))
    if database.exists():
        try:
            with closing(sqlite3.connect(database)) as db:
                row = db.execute("SELECT data FROM settings WHERE id='catalog:codex'").fetchone()
            cached = json.loads(row[0]) if row else {}
            if cached.get("provider") == provider and cached.get("models"):
                models = cached["models"]
                default_model = default_model if default_model in models else cached["default_model"]
                codex_source = "本机 Codex CLI model/list（上次检测）"
        except (sqlite3.Error, ValueError, KeyError):
            pass
    settings = _json_file(home / ".claude" / "settings.json")
    env = settings.get("env", {})
    claude_models = [settings.get("model")] + [env.get(k) or os.environ.get(k) for k in (
        "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL")]
    def safe_url(url):
        try:
            return validate_url(url) if url else ""
        except (ValueError, AttributeError):
            return ""
    def ids(items):
        return list(dict.fromkeys(m for m in items if isinstance(m, str) and MODEL_RE.fullmatch(m) and m != "auto"))
    return {
        "codex": {"models": ids(models), "default_model": default_model, "provider": provider,
                  "base_url": safe_url(endpoint or config.get("openai_base_url", "")), "source": codex_source},
        "claude": {"models": ids(claude_models), "default_model": next(iter(ids(claude_models)), "auto"),
                   "base_url": safe_url(env.get("ANTHROPIC_BASE_URL", "")), "source": "本机 Claude settings.json"},
        "dsh": {"models": ids(os.environ.get("TOKENFLOW_DSH_MODELS", "").split(",")),
                "default_model": "auto", "base_url": "", "source": "DSH 原生 profile / TOKENFLOW_DSH_MODELS"},
    }


class RuntimeConfig:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("TOKENFLOW_CONFIG_DB", _db_path().with_name("connections.db")))

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            self.path.parent.chmod(0o700)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY, data TEXT NOT NULL, secret TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, data TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS task_requests(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, run_id TEXT NOT NULL);
            """)
            if os.name != "nt":
                self.path.chmod(0o600)
            with db:
                yield db
        finally:
            db.close()

    def profiles(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.connect() as db:
            return [json.loads(r["data"]) | {"key_configured": bool(r["secret"])}
                    for r in db.execute("SELECT data, secret FROM connections ORDER BY rowid")]

    def profile(self, identity: str, secret: bool = False) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT data, secret FROM connections WHERE id=?", (identity,)).fetchone()
        if not row:
            raise ValueError("未找到保存的提供商连接")
        data = json.loads(row["data"]) | {"key_configured": bool(row["secret"])}
        if secret:
            data["api_key"] = _protect(base64.b64decode(row["secret"]), True).decode() if row["secret"] else ""
        return data

    def save_profile(self, data: dict) -> dict:
        identity = str(data.get("id") or uuid.uuid4().hex)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identity):
            raise ValueError("连接 ID 无效")
        name = str(data.get("name", "")).strip()
        if not name or len(name) > 80:
            raise ValueError("连接名称须为 1 至 80 个字符")
        protocol = data.get("protocol", "openai")
        if not isinstance(protocol, str) or protocol not in PROTOCOLS:
            raise ValueError("请选择 Chat Completions、Responses 或 Anthropic Messages 协议")
        url = validate_url(str(data.get("base_url", "")))
        if data.get("local") is True and not is_loopback_url(url):
            raise ValueError("本机直接推理只能使用回环地址")
        models = clean_models(data.get("models", []))
        key = data.get("api_key", "")
        if not isinstance(key, str) or len(key) > 4096 or any(c.isspace() or ord(c) < 32 for c in key):
            raise ValueError("API Key 不能包含空白或控制字符")
        with self.connect() as db:
            row = db.execute("SELECT data, secret FROM connections WHERE id=?", (identity,)).fetchone()
            old = json.loads(row["data"]) if row else {}
            encrypted = row["secret"] if row else ""
            if encrypted and old.get("base_url") != url and not key and data.get("clear_key") is not True:
                raise ValueError("更换 Base URL 时须重新填写 Key，避免把原 Key 发给其他提供商")
            if key:
                encrypted = base64.b64encode(_protect(key.encode())).decode()
            if data.get("clear_key") is True:
                encrypted = ""
            changed = old.get("base_url") != url or old.get("protocol") != protocol
            record = {"id": identity, "name": name, "base_url": url, "protocol": protocol,
                      "local": is_loopback_url(url) and data.get("local", False) is True,
                      "models": models, "model_history": list(dict.fromkeys(old.get("model_history", []) + old.get("models", []) + models)),
                      "discovered_at": None if changed else old.get("discovered_at"),
                      "discovery_error": "", "updated_at": time.time()}
            db.execute("INSERT OR REPLACE INTO connections VALUES(?,?,?)",
                       (identity, json.dumps(record, ensure_ascii=False), encrypted))
        return record | {"key_configured": bool(encrypted)}

    def discover(self, identity: str) -> dict:
        profile = self.profile(identity, True)
        try:
            models = clean_models(ConnectionClient(profile, timeout=8).models())
            if not models:
                raise ProviderError("服务没有返回模型；可手动登记模型 ID")
            profile["models"] = models
            profile["model_history"] = list(dict.fromkeys(profile.get("model_history", []) + models))
            profile["discovered_at"] = time.time()
            profile["discovery_error"] = ""
        except (ProviderError, ValueError) as exc:
            profile["discovery_error"] = str(exc)
        profile.pop("api_key", None)
        profile.pop("key_configured", None)
        with self.connect() as db:
            db.execute("UPDATE connections SET data=? WHERE id=?", (json.dumps(profile, ensure_ascii=False), identity))
        return self.profile(identity)

    def settings(self) -> dict:
        defaults = {"bindings": {h: {"connection": "native", "model": "auto"} for h in ("codex", "claude", "dsh")},
                    "roles": {"local": "auto", "planner": "native:codex", "executor": "native:codex", "reviewer": "native:codex"},
                    "role_models": {r: "auto" for r in ROLES}, "max_calls": 6,
                    "max_task_tokens": 64000, "context_window_tokens": 32768,
                    "output_limits": {"local": 1024, "planner": 1536, "executor": 2400, "reviewer": 1024},
                    "history_max_runs": 0, "history_retention_days": 0, "cache_max_mb": 0, "cache_retention_days": 0}
        if self.path.exists():
            with self.connect() as db:
                row = db.execute("SELECT data FROM settings WHERE id='runtime'").fetchone()
            if row:
                saved = json.loads(row[0])
                limits = defaults["output_limits"] | saved.get("output_limits", {})
                defaults.update(saved)
                defaults["output_limits"] = limits
        return defaults

    def save_settings(self, data: dict) -> dict:
        result = self.settings()
        if any(not isinstance(data.get(k, {}), dict) for k in ("bindings", "roles", "role_models")):
            raise ValueError("绑定、角色和角色模型须为对象")
        for harness, binding in data.get("bindings", {}).items():
            if harness not in result["bindings"] or not isinstance(binding, dict):
                raise ValueError("Harness 绑定无效")
            connection, model = binding.get("connection", "native"), binding.get("model", "auto")
            if not isinstance(connection, str):
                raise ValueError("连接 ID 无效")
            if model != "auto" and (not isinstance(model, str) or not MODEL_RE.fullmatch(model)):
                raise ValueError("绑定模型 ID 无效")
            if connection != "native":
                profile = self.profile(connection)
                if harness not in HARNESS_PROTOCOLS or profile["protocol"] != HARNESS_PROTOCOLS[harness]:
                    raise ValueError("Codex 需要 Responses；Claude 需要 Anthropic Messages；DSH 需要 Chat Completions")
                if model != "auto" and model not in profile["models"]:
                    raise ValueError("模型不在连接的当前模型记录中")
            result["bindings"][harness] = {"connection": connection, "model": model}
        for role, target in data.get("roles", {}).items():
            if role not in ROLES or not isinstance(target, str):
                raise ValueError("角色配置无效")
            if target not in {"auto", "native:codex", "native:claude", "native:dsh"}:
                profile = self.profile(target)
            if role == "local" and target.startswith("native:"):
                raise ValueError("本地小模型角色需要本地 API 连接")
            if role != "local" and target == "auto":
                raise ValueError("规划、执行、验收角色需要选择已配置的连接或 Harness")
            result["roles"][role] = target
        for role, model in data.get("role_models", {}).items():
            if role not in ROLES or (model != "auto" and (not isinstance(model, str) or not MODEL_RE.fullmatch(model))):
                raise ValueError("角色模型 ID 无效")
            result["role_models"][role] = model
        budget = data.get("max_calls", result["max_calls"])
        if not isinstance(budget, int) or isinstance(budget, bool) or not 3 <= budget <= 8:
            raise ValueError("模型调用预算须为 3 至 8 次")
        result["max_calls"] = budget
        bounds = {"max_task_tokens": (256, 2_000_000), "context_window_tokens": (256, 1_000_000),
                  "history_max_runs": (0, 100000), "history_retention_days": (0, 3650),
                  "cache_max_mb": (0, 100000), "cache_retention_days": (0, 3650)}
        for key, (minimum, maximum) in bounds.items():
            value = data.get(key, result[key])
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{key} 须为 {minimum} 至 {maximum} 的整数")
            result[key] = value
        limits = data.get("output_limits", {})
        if not isinstance(limits, dict) or any(r not in ROLES or type(v) is not int or not 64 <= v <= 32768 for r, v in limits.items()):
            raise ValueError("阶段输出上限须为 64 至 32768 Token")
        result["output_limits"].update(limits)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES('runtime',?)", (json.dumps(result),))
        return result

    def public(self) -> dict:
        return {"connections": self.profiles(), "native": native_catalogs(), **self.settings(),
                "key_storage": "Windows DPAPI（当前系统用户）" if os.name == "nt" else "仅文件所有者可读"}

    def discover_codex(self) -> dict:
        import queue
        import subprocess
        import threading
        from tokenflow import _resolve_harness_command
        command = _resolve_harness_command("codex")
        if not command:
            raise ValueError("未安装 Codex CLI")
        process = subprocess.Popen(command + ["app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        messages = queue.Queue()
        def read():
            for line in process.stdout:
                try:
                    messages.put(json.loads(line))
                except ValueError:
                    pass
        threading.Thread(target=read, daemon=True).start()
        deadline = time.monotonic() + 15
        def send(data):
            process.stdin.write(json.dumps(data) + "\n")
            process.stdin.flush()
        def response(identity):
            while time.monotonic() < deadline:
                try:
                    value = messages.get(timeout=max(.1, deadline - time.monotonic()))
                except queue.Empty:
                    break
                if value.get("id") == identity:
                    if value.get("error"):
                        raise ValueError("Codex 模型目录请求失败")
                    return value["result"]
            raise ValueError("Codex 模型目录检测超时，保留上次记录")
        try:
            send({"id": 0, "method": "initialize", "params": {"clientInfo": {"name": "tokenflow", "version": "0.1.0"}}})
            response(0)
            send({"method": "initialized", "params": {}})
            send({"id": 1, "method": "model/list", "params": {"limit": 50, "includeHidden": False}})
            data = response(1).get("data", [])
            models = clean_models([m.get("model") or m.get("id") for m in data if isinstance(m, dict)])
            if not models:
                raise ValueError("Codex 未返回可选择的模型")
            selected = next((m.get("model") or m.get("id") for m in data if m.get("isDefault")), models[0])
            catalog = {"models": models, "default_model": selected, "provider": native_catalogs()["codex"]["provider"]}
            with self.connect() as db:
                db.execute("INSERT OR REPLACE INTO settings VALUES('catalog:codex',?)", (json.dumps(catalog),))
            return catalog
        finally:
            process.kill()
            process.wait()
            for stream in (process.stdin, process.stdout):
                stream.close()

    def discover_local(self) -> dict:
        from concurrent.futures import ThreadPoolExecutor
        from model_providers import PROVIDERS
        def probe(name):
            spec = PROVIDERS[name]
            url = os.environ.get(spec.url_env, spec.default_url)
            if not is_loopback_url(url):
                return None
            url = url.rstrip("/")
            if name == "freetoken" and not url.endswith("/v1"):
                url += "/v1"
            identity = "detected-" + name
            existing = next((p for p in self.profiles() if p["id"] == identity), None)
            if existing:
                return self.discover(identity)
            profile = {"id": identity, "name": name.capitalize() + " 本地", "base_url": url,
                       "protocol": "openai", "local": True, "api_key": ""}
            try:
                profile["models"] = clean_models(ConnectionClient(profile, timeout=2).models())
                if profile["models"]:
                    self.save_profile(profile)
                    return self.discover(identity)
            except (ProviderError, ValueError):
                pass
            return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            found = [p for p in pool.map(probe, ("ollama", "freetoken")) if p]
        return {"detected": found, "native": native_catalogs()}

    def import_native(self, harness: str) -> dict:
        if harness == "dsh":
            url, key = os.environ.get("BUPT_API_BASE_URL", ""), os.environ.get("BUPT_API_KEY", "")
            if not url or not key:
                raise ValueError("未找到本机 BUPT_API_BASE_URL / BUPT_API_KEY；请手动配置 DSH 连接")
            return self.save_profile({"id": "imported-dsh", "name": "北邮 DSv4 API", "base_url": url,
                                      "protocol": "openai", "api_key": key, "models": [], "local": False})
        home = Path.home()
        catalog = native_catalogs().get(harness)
        if not catalog or not catalog["base_url"]:
            raise ValueError("该 Harness 使用原生登录或未配置 Base URL；直接选择原生连接即可")
        key = ""
        if harness == "claude":
            env = _json_file(home / ".claude" / "settings.json").get("env", {})
            key = env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", "")
        elif harness == "codex":
            try:
                import tomllib
                base = Path(os.environ.get("CODEX_HOME", home / ".codex"))
                config = tomllib.loads((base / "config.toml").read_text(encoding="utf-8-sig"))
                provider = config.get("model_providers", {}).get(config.get("model_provider"), {})
                key = provider.get("experimental_bearer_token") or os.environ.get(provider.get("env_key", ""), "") or _json_file(base / "auth.json").get("OPENAI_API_KEY", "")
            except (ImportError, OSError, ValueError):
                raise ValueError("无法读取本机 Codex 连接配置") from None
        return self.save_profile({"id": "imported-" + harness, "name": harness.capitalize() + " 已有连接",
                                  "base_url": catalog["base_url"], "protocol": HARNESS_PROTOCOLS[harness],
                                  "api_key": key, "models": catalog["models"], "local": False})

    def save_run(self, run: dict, *, request_id: str = "", fingerprint: str = "") -> None:
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?)", (run["id"], json.dumps(run, ensure_ascii=False), time.time()))
            if request_id:
                db.execute("INSERT INTO task_requests VALUES(?,?,?)", (request_id, fingerprint, run["id"]))

    def request_run(self, identity: str, fingerprint: str | None = None) -> dict | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", identity):
            raise ValueError("请求 ID 无效")
        with self.connect() as db:
            row = db.execute("SELECT fingerprint,run_id FROM task_requests WHERE id=?", (identity,)).fetchone()
        if not row:
            return None
        if fingerprint is not None and row["fingerprint"] != fingerprint:
            raise ValueError("请求 ID 已用于不同内容；请新建任务")
        try:
            return self.runs(row["run_id"])
        except ValueError:
            raise ValueError("该请求的任务记录已清理；请新建任务") from None

    def manage_history(self, identity: str = "", *, prune: bool = False) -> dict:
        settings = self.settings()
        with self.connect() as db:
            rows = [(r["id"], json.loads(r["data"]), r["updated"]) for r in db.execute("SELECT * FROM runs ORDER BY updated DESC")]
            eligible = [(i, r, t) for i, r, t in rows if not (r["status"] in {"running", "cancelling"} and r.get("process_id") == os.getpid())]
            if identity:
                if not re.fullmatch(r"[a-f0-9]{32}", identity):
                    raise ValueError("任务 ID 无效")
                if identity not in {i for i, r, t in eligible}:
                    raise ValueError("任务不存在或仍在运行，不能删除")
                remove = [identity]
            elif prune:
                maximum, days = settings["history_max_runs"], settings["history_retention_days"]
                remove = [i for n, (i, r, t) in enumerate(eligible)
                          if (maximum and n >= maximum) or (days and t < time.time() - days * 86400)]
            else:
                remove = []
            db.executemany("DELETE FROM runs WHERE id=?", [(i,) for i in remove])
            count = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        return {"runs": count, "deleted": len(remove), "database_bytes": self.path.stat().st_size if self.path.exists() else 0}

    def runs(self, identity: str = "") -> dict:
        if not self.path.exists():
            return {"runs": []}
        with self.connect() as db:
            if identity:
                row = db.execute("SELECT data FROM runs WHERE id=?", (identity,)).fetchone()
                if not row:
                    raise ValueError("任务记录不存在")
                result = json.loads(row[0])
                if result["status"] in {"running", "cancelling"} and result.get("process_id") != os.getpid():
                    result.update(status="interrupted", error="服务重启中断了任务；可重新布置")
                return result
            items = [json.loads(r[0]) for r in db.execute("SELECT data FROM runs ORDER BY updated DESC LIMIT 20")]
        for item in items:
            if item["status"] in {"running", "cancelling"} and item.get("process_id") != os.getpid():
                item["status"] = "interrupted"
        return {"runs": [{k: r.get(k) for k in ("id", "task", "status", "created_at")} for r in items]}


class ConnectionClient(OpenAICompatibleProvider):
    """Reuse bounded HTTP and safe errors from the existing provider adapter."""
    def __init__(self, profile: dict, timeout: float = 90):
        self.profile = profile
        self.base_url = profile["base_url"]
        self.timeout = timeout
        self.spec = ProviderSpec("saved_connection", "", "TOKENFLOW_CONNECTION_KEY", self.base_url, "saved")
        self._omniroute_key = None

    def models(self) -> list[str]:
        from urllib.parse import urlsplit
        base = self.base_url
        if self.profile["protocol"] == "anthropic":
            # DeepSeek documents a shared catalog at /models; Messages uses /anthropic/v1.
            if urlsplit(base).hostname == "api.deepseek.com" and base.endswith(("/anthropic", "/anthropic/v1")):
                base = base.rsplit("/anthropic", 1)[0]
            elif not base.endswith("/v1"):
                base += "/v1"
        data = self._request("/models", base_url=base).get("data", [])
        return [m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)] if isinstance(data, list) else []

    def _request(self, path: str, payload: dict | None = None, *, base_url: str | None = None) -> dict:
        # Separate per-instance credentials: never mutate the server's environment.
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, build_opener, HTTPRedirectHandler
        from model_providers import _read_json
        key = self.profile.get("api_key", "")
        headers = {"Accept": "application/json"}
        if key:
            headers["Authorization"] = "Bearer " + key
        if self.profile["protocol"] == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
            if key:
                headers["x-api-key"] = key
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request((base_url or self.base_url) + path, data=body, headers=headers)
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            with build_opener(NoRedirect()).open(request, timeout=self.timeout) as response:
                return _read_json(response)
        except HTTPError as exc:
            raise ProviderError(f"连接 HTTP {exc.code}；请检查协议、地址与 Key") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ProviderError("连接失败或超时；请检查提供商服务") from exc

    def chat(self, model: str, messages: list[dict], max_tokens: int = 1800, **kwargs) -> dict:
        if not MODEL_RE.fullmatch(model):
            raise ProviderError("模型 ID 无效")
        protocol = self.profile["protocol"]
        if protocol == "openai":
            return super().chat(model, messages, max_tokens)
        if protocol == "responses":
            value = self._request("/responses", {"model": model, "input": messages, "max_output_tokens": max_tokens})
            if value.get("status") == "incomplete":
                raise ProviderError("Responses 输出未完成，请调整输出上限或缩小任务", usage=value.get("usage"))
            items = value.get("output") or []
            if not isinstance(items, list):
                raise ProviderError("Responses 返回了无效输出结构", usage=value.get("usage"))
            output = "".join(c["text"] for item in items if isinstance(item, dict)
                             for c in (item.get("content") or []) if isinstance(c, dict) and
                             c.get("type") == "output_text" and isinstance(c.get("text"), str))
            usage = value.get("usage")
        else:
            base = self.base_url if self.base_url.endswith("/v1") else self.base_url + "/v1"
            value = self._request("/messages", {"model": model, "system": "\n".join(m["content"] for m in messages if m["role"] == "system"),
                                  "messages": [m for m in messages if m["role"] != "system"], "max_tokens": max_tokens}, base_url=base)
            if value.get("stop_reason") == "max_tokens":
                raise ProviderError("Messages 输出达到上限，不能作为完整结果验收", usage=value.get("usage"))
            output = "".join(c["text"] for c in (value.get("content") or []) if isinstance(c, dict) and
                             c.get("type") == "text" and isinstance(c.get("text"), str))
            usage = value.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        from model_providers import reported_usage
        normalized = reported_usage({"usage": usage})
        return {"choices": [{"message": {"content": output}}], "usage": normalized}
