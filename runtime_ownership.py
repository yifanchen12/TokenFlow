"""OS-backed database/service ownership; locks release when a process exits."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

_mutex = threading.RLock()
_held: dict[str, "FileLease"] = {}


def lease_path(database: str | Path, kind: str) -> Path:
    return Path(str(Path(database).resolve()) + "." + kind + ".lock")


class FileLease:
    def __init__(self, path: Path, stream, metadata: dict):
        self.path, self.stream, self.metadata = path, stream, metadata

    @classmethod
    def acquire(cls, path: str | Path, metadata: dict | None = None):
        path = Path(path).resolve()
        with _mutex:
            if str(path) in _held:
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = path.open("a+b")
            try:
                if path.stat().st_size == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                stream.close()
                return None
            lease = cls(path, stream, metadata or {})
            _held[str(path)] = lease
            if metadata is not None:
                try:
                    lease.update(metadata)
                except Exception:
                    lease.close()
                    raise
            if os.name != "nt":
                path.chmod(0o600)
            return lease

    def update(self, metadata: dict):
        self.metadata = metadata.copy()
        self.stream.seek(1)
        self.stream.truncate()
        self.stream.write(json.dumps(metadata).encode("utf-8"))
        self.stream.flush()

    def close(self):
        with _mutex:
            if self.stream.closed:
                return
            try:
                self.stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
            finally:
                self.stream.close()
                _held.pop(str(self.path), None)


def lease_state(path: Path) -> tuple[bool, dict]:
    """Probe the OS lock; stale metadata alone never establishes ownership."""
    if not path.exists():
        return False, {}
    with _mutex:
        current = _held.get(str(path.resolve()))
        if current:
            return True, current.metadata.copy()
        probe = FileLease.acquire(path)
        if probe:
            probe.close()
            return False, {}
        try:
            with path.open("rb") as stream:
                stream.seek(1)  # Windows locks only byte zero, not the metadata.
                metadata = json.loads(stream.read(4096).decode("utf-8"))
            return True, metadata if isinstance(metadata, dict) else {}
        except (OSError, ValueError):
            return True, {}  # A concurrently written/unknown owner stays protected.


def process_alive(pid: int) -> bool:
    if type(pid) is not int or pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5  # Access denied is not proof of death.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def run_active(database: str | Path, run: dict) -> bool:
    if run.get("status") not in {"running", "cancelling"}:
        return False
    if run.get("managed_owner"):
        locked, owner = lease_state(lease_path(database, "task"))
        return locked and (not owner.get("run_id") or owner["run_id"] == run.get("id"))
    return process_alive(run.get("process_id"))  # Compatibility with older records.
