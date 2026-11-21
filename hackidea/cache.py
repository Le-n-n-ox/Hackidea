"""Disk cache for search results and page fetches (JSON files keyed by SHA-256)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


class DiskCache:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, kind: str, key: str) -> Path:
        h = hashlib.sha256(f"{kind}:{key}".encode("utf-8")).hexdigest()[:32]
        sub = self.root / kind
        sub.mkdir(parents=True, exist_ok=True)
        return sub / f"{h}.json"

    def get(self, kind: str, key: str, max_age_s: float | None = None) -> Any | None:
        p = self._path(kind, key)
        if not p.exists():
            return None
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if max_age_s is not None and time.time() - blob.get("ts", 0) > max_age_s:
            return None
        return blob.get("value")

    def set(self, kind: str, key: str, value: Any) -> None:
        p = self._path(kind, key)
        p.write_text(json.dumps({"ts": time.time(), "value": value}, ensure_ascii=False),
                     encoding="utf-8")