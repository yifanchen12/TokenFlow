"""SQLite document and deterministic vector cache."""

from __future__ import annotations

import hashlib
import heapq
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

VECTOR_VERSION = 2
VECTOR_BACKEND = "hashing-64-cjk-v2"


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
    tokens = re.findall(r"[^\W\u3400-\u9fff]+", text.lower())
    for phrase in re.findall(r"[\u3400-\u9fff]+", text):
        tokens.extend(phrase[i:i + size] for size in (2, 3) for i in range(len(phrase) - size + 1))
        if len(phrase) == 1:
            tokens.append(phrase)
    for token in tokens:
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
        connection.execute("PRAGMA foreign_keys=ON")
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
                CREATE TABLE IF NOT EXISTS parsed_files(
                    cache_key TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL,
                    last_used REAL NOT NULL
                );
                """
            )
            if "last_used" not in {row[1] for row in db.execute("PRAGMA table_info(documents)")}:
                db.execute("ALTER TABLE documents ADD COLUMN last_used REAL NOT NULL DEFAULT 0")
                db.execute("UPDATE documents SET last_used=created_at")
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            if db.execute("PRAGMA user_version").fetchone()[0] < VECTOR_VERSION:
                # ponytail: rebuild this small local index in one transaction; batch jobs if measured size requires it.
                rows = db.execute("SELECT id, content FROM chunks")
                while batch := rows.fetchmany(256):
                    db.executemany("UPDATE chunks SET vector_json=? WHERE id=?",
                                   [(json.dumps(_vector(row["content"])), row["id"]) for row in batch])
                db.execute(f"PRAGMA user_version={VECTOR_VERSION}")

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
            stored = db.execute("SELECT name FROM documents WHERE id=?", (document_id,)).fetchone()
            db.execute("UPDATE documents SET last_used=? WHERE id=?", (time.time(), document_id))
            count = db.execute("SELECT COUNT(*) FROM chunks WHERE document_id=?", (document_id,)).fetchone()[0]
        return {"document_id": document_id, "name": stored["name"], "chunks": count, "content_hash": digest,
                "reused": existing is not None}

    def search(self, query: str, limit: int = 5, *, document_id: int | None = None,
               required_terms: list[str] | None = None) -> list[dict[str, Any]]:
        if not query.strip():
            raise ValueError("检索词不能为空")
        target = _vector(query)
        with self._connect() as db:
            rows = db.execute(
                "SELECT documents.id AS document_id, documents.name, chunks.chunk_index, chunks.content, chunks.vector_json, "
                "COALESCE(SUM(LENGTH(chunks.content)) OVER (PARTITION BY documents.id ORDER BY chunks.chunk_index "
                "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS start "
                "FROM chunks JOIN documents ON documents.id=chunks.document_id" +
                (" WHERE documents.id=?" if document_id is not None else ""),
                (document_id,) if document_id is not None else (),
            )
            def scored():
                for row in rows:
                    if required_terms is not None and not any(t.lower() in row["content"].lower() for t in required_terms):
                        continue
                    score = _cosine(target, json.loads(row["vector_json"]))
                    if required_terms:
                        # Exact source matches must survive collisions in the small hash vector.
                        score = max(0.0, score) + sum(t.lower() in row["content"].lower() for t in required_terms) / len(required_terms)
                    if score > 0:
                        yield {"document_id": row["document_id"], "name": row["name"], "chunk_index": row["chunk_index"],
                               "start": row["start"], "content": row["content"], "score": round(score, 6)}
            # ponytail: O(N) scan with O(k) result memory; add an index when corpus latency justifies it.
            return heapq.nlargest(max(1, min(limit, 20)), scored(), key=lambda item: item["score"])

    def parsed(self, cache_key: str, data: dict | None = None) -> dict | None:
        with self._connect() as db:
            if data is not None:
                db.execute("INSERT OR REPLACE INTO parsed_files VALUES(?,?,?)",
                           (cache_key, json.dumps(data, ensure_ascii=False), time.time()))
                return data
            row = db.execute("SELECT data_json FROM parsed_files WHERE cache_key=?", (cache_key,)).fetchone()
            if row:
                db.execute("UPDATE parsed_files SET last_used=? WHERE cache_key=?", (time.time(), cache_key))
                return json.loads(row[0])
        return None

    def document(self, identity: int) -> str:
        with self._connect() as db:
            rows = db.execute("SELECT content FROM chunks WHERE document_id=? ORDER BY chunk_index", (identity,)).fetchall()
        if not rows:
            raise ValueError("原始资料缓存已清理；请重新提供资料")
        return "".join(row[0] for row in rows)

    def entries(self) -> list[dict]:
        with self._connect() as db:
            documents = [{"id": str(r["id"]), "kind": "document", "name": r["name"], "bytes": r["bytes"]}
                         for r in db.execute("SELECT documents.id,name,COALESCE(SUM(LENGTH(CAST(content AS BLOB))+LENGTH(vector_json)),0) AS bytes "
                                             "FROM documents LEFT JOIN chunks ON chunks.document_id=documents.id GROUP BY documents.id ORDER BY last_used DESC")]
            parsed = [{"id": r["cache_key"], "kind": "parsed", "name": json.loads(r["data_json"])["name"],
                       "bytes": len(r["data_json"].encode("utf-8"))} for r in db.execute("SELECT * FROM parsed_files ORDER BY last_used DESC")]
        return documents + parsed

    def manage(self, *, kind: str = "", identity: str = "", clear: bool = False,
               max_mb: int = 0, days: int = 0) -> dict:
        if kind and (kind not in {"document", "parsed"} or not re.fullmatch(r"\d+" if kind == "document" else r"[a-f0-9]{64}", identity)):
            raise ValueError("缓存 ID 或类型无效")
        with self._connect() as db:
            rows = [("document", str(r[0]), r[1], r[2]) for r in db.execute(
                "SELECT documents.id,last_used,COALESCE(SUM(LENGTH(CAST(content AS BLOB))+LENGTH(vector_json)),0) "
                "FROM documents LEFT JOIN chunks ON chunks.document_id=documents.id GROUP BY documents.id")]
            rows += [("parsed", r[0], r[1], r[2]) for r in db.execute("SELECT cache_key,last_used,LENGTH(CAST(data_json AS BLOB)) FROM parsed_files")]
            total, deleted = sum(r[3] for r in rows), 0
            for entry_kind, entry_id, used, size in sorted(rows, key=lambda r: r[2]):
                remove = clear or (kind == entry_kind and identity == entry_id) or (days and used < time.time() - days * 86400)
                remove = remove or (max_mb and total > max_mb * 1024 * 1024)
                if remove:
                    table, column = ("documents", "id") if entry_kind == "document" else ("parsed_files", "cache_key")
                    db.execute(f"DELETE FROM {table} WHERE {column}=?", (entry_id,))
                    total -= size
                    deleted += 1
        return {"deleted": deleted, "content_bytes": total}

    def status(self) -> dict[str, Any]:
        legacy_available = bool(self.legacy_database_paths())
        if not self.path.is_file():
            return {"database": self.path.name, "documents": 0, "chunks": 0, "parsed_files": 0, "content_bytes": 0,
                    "database_bytes": 0, "vector_backend": VECTOR_BACKEND, "legacy_database_available": legacy_available}
        db = sqlite3.connect(f"{self.path.resolve().as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            documents = db.execute("SELECT COUNT(*) AS count FROM documents").fetchone()["count"]
            chunks = db.execute("SELECT COUNT(*) AS count FROM chunks").fetchone()["count"]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            parsed_exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='parsed_files'").fetchone()
            parsed_files = db.execute("SELECT COUNT(*) FROM parsed_files").fetchone()[0] if parsed_exists else 0
            content_bytes = db.execute("SELECT COALESCE(SUM(LENGTH(CAST(content AS BLOB))+LENGTH(vector_json)),0) FROM chunks").fetchone()[0]
            if parsed_exists:
                content_bytes += db.execute("SELECT COALESCE(SUM(LENGTH(CAST(data_json AS BLOB))),0) FROM parsed_files").fetchone()[0]
        finally:
            db.close()
        return {"database": self.path.name, "documents": documents, "chunks": chunks, "parsed_files": parsed_files,
                "content_bytes": content_bytes, "database_bytes": self.path.stat().st_size,
                "vector_backend": VECTOR_BACKEND if version >= VECTOR_VERSION else "hashing-64-legacy", "legacy_database_available": legacy_available}

    def legacy_database_paths(self) -> list[Path]:
        candidates = [Path.cwd() / "tokenflow.db", Path(__file__).resolve().parent / "tokenflow.db"]
        if getattr(sys, "frozen", False):
            candidates.append(Path(sys.executable).resolve().parent / "tokenflow.db")
        current = self.path.resolve()
        return list(dict.fromkeys(path.resolve() for path in candidates if path.is_file() and path.resolve() != current))

    def _import_legacy_database(self, source: Path) -> int:
        db = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        try:
            documents = db.execute("SELECT id, name FROM documents ORDER BY id").fetchall()
            for document in documents:
                chunks = db.execute(
                    "SELECT content FROM chunks WHERE document_id=? ORDER BY chunk_index",
                    (document["id"],),
                ).fetchall()
                self.index(str(document["name"]), "".join(row["content"] or "" for row in chunks))
            return len(documents)
        finally:
            db.close()

    def import_legacy(self) -> dict[str, Any]:
        sources = self.legacy_database_paths()
        imported = 0
        try:
            for source in sources:
                imported += self._import_legacy_database(source)
        except sqlite3.Error:
            raise ValueError("旧缓存无法读取；原数据库未修改") from None
        return {"legacy_databases": len(sources), "documents_processed": imported, "source_preserved": True}


def self_check() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        store = DocumentStore(str(Path(directory) / "test.db"))
        store.index("test.txt", "alpha beta gamma")
        assert store.search("alpha", 1)[0]["name"] == "test.txt"
        store.index("退款规则.txt", "退款期限为7天。逾期不予退款。")
        store.index("天气.txt", "今天晴天，明天气温下降。")
        assert store.search("退款期限", 1)[0]["name"] == "退款规则.txt"
        assert _cosine(_vector("退款期限为7天"), _vector("退款期限")) > 0
        with store._connect() as db:
            db.execute("UPDATE chunks SET vector_json=?", (json.dumps([0.0] * 64),))
            db.execute("PRAGMA user_version=1")
        assert DocumentStore(str(store.path), initialize=False).status()["vector_backend"] == "hashing-64-legacy"
        upgraded = DocumentStore(str(store.path))
        assert upgraded.status()["documents"] == 3
        assert upgraded.search("退款期限", 1)[0]["name"] == "退款规则.txt"
        assert DocumentStore(str(store.path)).status()["vector_backend"] == VECTOR_BACKEND
        absent = Path(directory) / "not-created.db"
        assert DocumentStore(str(absent), initialize=False).status()["documents"] == 0
        assert not absent.exists()
        old_path = Path(directory) / "tokenflow.db"
        old_store = DocumentStore(str(old_path))
        old_store.index("legacy.txt", "keep this legacy document")
        migrated = DocumentStore(str(Path(directory) / "new.db"))
        previous_cwd = Path.cwd()
        try:
            os.chdir(directory)
            assert migrated.status()["legacy_database_available"]
            assert migrated.import_legacy()["documents_processed"] == 1
            assert migrated.import_legacy()["documents_processed"] == 1
            assert migrated.status()["documents"] == 1
        finally:
            os.chdir(previous_cwd)
        assert old_path.is_file()
