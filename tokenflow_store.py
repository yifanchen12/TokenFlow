"""SQLite document and deterministic vector cache."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _db_path() -> Path:
    override = os.environ.get("TOKENFLOW_DB_PATH")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "TokenFlow" / "tokenflow.db"


def _vector(text: str, dimensions: int = 64) -> list[float]:
    values = [0.0] * dimensions
    for token in re.findall(r"[\w]+", text.lower()):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        values[index] += 1.0 if digest[4] % 2 else -1.0
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [round(value / norm, 6) for value in values]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


class DocumentStore:
    def __init__(self, path: str | None = None, initialize: bool = True):
        self.path = Path(path).expanduser() if path else _db_path()
        if initialize:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._init()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _init(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents(
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chunks(
                    id INTEGER PRIMARY KEY,
                    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    vector_json TEXT NOT NULL,
                    UNIQUE(document_id, chunk_index)
                );
                """
            )

    def index(self, name: str, content: str, chunk_size: int = 1200) -> dict[str, Any]:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        chunks = [content[index : index + chunk_size] for index in range(0, len(content), chunk_size)] or [""]
        with self._connect() as db:
            existing = db.execute("SELECT id FROM documents WHERE content_hash=?", (digest,)).fetchone()
            if existing:
                document_id = existing["id"]
            else:
                cursor = db.execute("INSERT INTO documents(name, content_hash, created_at) VALUES(?,?,?)", (name, digest, time.time()))
                document_id = cursor.lastrowid
                for index, chunk in enumerate(chunks):
                    db.execute(
                        "INSERT INTO chunks(document_id, chunk_index, content, vector_json) VALUES(?,?,?,?)",
                        (document_id, index, chunk, json.dumps(_vector(chunk))),
                    )
        return {"document_id": document_id, "name": name, "chunks": len(chunks), "content_hash": digest}

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        target = _vector(query)
        with self._connect() as db:
            rows = db.execute(
                "SELECT documents.name, chunks.chunk_index, chunks.content, chunks.vector_json "
                "FROM chunks JOIN documents ON documents.id=chunks.document_id"
            ).fetchall()
        ranked = []
        for row in rows:
            score = _cosine(target, json.loads(row["vector_json"]))
            ranked.append({"name": row["name"], "chunk_index": row["chunk_index"], "content": row["content"], "score": round(score, 6)})
        return sorted(ranked, key=lambda item: item["score"], reverse=True)[: max(1, min(limit, 20))]

    def status(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"database": self.path.name, "documents": 0, "chunks": 0, "vector_backend": "hashing-64"}
        db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            documents = db.execute("SELECT COUNT(*) AS count FROM documents").fetchone()["count"]
            chunks = db.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"]
        finally:
            db.close()
        return {"database": self.path.name, "documents": documents, "chunks": chunks, "vector_backend": "hashing-64"}


def self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        store = DocumentStore(str(Path(directory) / "test.db"))
        store.index("test.txt", "alpha beta gamma")
        assert store.search("alpha", 1)[0]["name"] == "test.txt"
