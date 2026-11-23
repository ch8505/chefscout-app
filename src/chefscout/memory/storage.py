"""Small JSON-file helpers: atomic writes and a per-file lock for read-modify-write."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Serializes updates to one file across server threads."""
    key = Path(path).resolve()
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        yield


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """Write to a temp file, then replace: a crash never leaves a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def normalize(value: str) -> str:
    """One spelling per item: trimmed, single spaces, lowercase (affects Latin letters only)."""
    return " ".join((value or "").split()).lower()
