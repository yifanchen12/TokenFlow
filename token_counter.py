"""Optional exact token counting with an explicit heuristic fallback."""

from __future__ import annotations

import importlib.util
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


def _heuristic_count(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _configured_path() -> Path | None:
    value = os.environ.get("TOKENFLOW_TOKENIZER_PATH", "").strip()
    return Path(value).expanduser() if value else None


@lru_cache(maxsize=4)
def _load_tokenizers_file(path: str) -> Any:
    from tokenizers import Tokenizer

    return Tokenizer.from_file(path)


@lru_cache(maxsize=4)
def _load_transformers_tokenizer(path: str) -> Any:
    from transformers import AutoTokenizer

    return AutoTokenizer.from_pretrained(path, local_files_only=True, use_fast=True)


@lru_cache(maxsize=4)
def _load_tiktoken(encoding: str) -> Any:
    import tiktoken

    return tiktoken.get_encoding(encoding)


def _count_with_local_path(text: str, path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    tokenizer_json = path if path.is_file() else path / "tokenizer.json"
    if tokenizer_json.is_file() and importlib.util.find_spec("tokenizers"):
        tokenizer = _load_tokenizers_file(str(tokenizer_json.resolve()))
        return {
            "count": len(tokenizer.encode(text).ids),
            "backend": "tokenizers",
            "exact": True,
            "tokenizer": str(tokenizer_json.resolve()),
        }
    if path.is_dir() and importlib.util.find_spec("transformers"):
        tokenizer = _load_transformers_tokenizer(str(path.resolve()))
        return {
            "count": len(tokenizer.encode(text, add_special_tokens=False)),
            "backend": "transformers",
            "exact": True,
            "tokenizer": str(path.resolve()),
        }
    return None


def count_tokens(text: str, model: str | None = None) -> dict[str, Any]:
    """Count tokens using an explicitly configured local tokenizer when possible.

    No network access is attempted. Without an optional tokenizer package and a
    local tokenizer path, the result is deliberately marked as heuristic.
    """
    path = _configured_path()
    if path:
        try:
            result = _count_with_local_path(text, path)
            if result:
                return result
        except Exception as exc:  # tokenizer packages expose varied exception types
            path_error = f"{type(exc).__name__}: {exc}"
        else:
            path_error = "未找到可用的本地 tokenizer 文件"
    else:
        path_error = None

    encoding = os.environ.get("TOKENFLOW_TIKTOKEN_ENCODING", "cl100k_base").strip()
    backend = os.environ.get("TOKENFLOW_TOKENIZER_BACKEND", "auto").strip().lower()
    should_try_tiktoken = backend == "tiktoken" or (backend == "auto" and bool(model and model.startswith("gpt-")))
    if should_try_tiktoken and importlib.util.find_spec("tiktoken"):
        try:
            tokenizer = _load_tiktoken(encoding)
            return {"count": len(tokenizer.encode(text)), "backend": "tiktoken", "exact": True, "tokenizer": encoding}
        except Exception as exc:
            tiktoken_error = f"{type(exc).__name__}: {exc}"
        else:
            tiktoken_error = None
    else:
        tiktoken_error = None

    result = {
        "count": _heuristic_count(text),
        "backend": "heuristic",
        "exact": False,
        "tokenizer": None,
    }
    errors = [value for value in (path_error, tiktoken_error) if value]
    if errors:
        result["warning"] = "; ".join(errors)
    return result


def status() -> dict[str, Any]:
    path = _configured_path()
    packages = {
        "tokenizers": bool(importlib.util.find_spec("tokenizers")),
        "transformers": bool(importlib.util.find_spec("transformers")),
        "tiktoken": bool(importlib.util.find_spec("tiktoken")),
    }
    return {
        "configured_path": str(path) if path else None,
        "packages": packages,
        "backend": count_tokens("status probe")["backend"],
        "exact": count_tokens("status probe")["exact"],
    }


def self_check() -> None:
    result = count_tokens("TokenFlow tokenizer check")
    assert result["count"] > 0
    assert result["backend"] in {"heuristic", "tokenizers", "transformers", "tiktoken"}
    assert isinstance(result["exact"], bool)

