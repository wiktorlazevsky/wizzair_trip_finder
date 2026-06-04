"""Disk cache for route quotes.

Queries are slow and hammering Google risks IP blocks, so we persist every
(origin, dest, date, adults, max_stops) lookup to a JSON file and reuse it.
Delete the file (or pass --no-cache) to force fresh prices.
"""

import json
import os
import threading
from typing import Optional


class Cache:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    self._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._data = {}

    @staticmethod
    def key(*parts) -> str:
        return "|".join("" if p is None else str(p) for p in parts)

    def get(self, key: str) -> Optional[dict]:
        return self._data.get(key)

    def set(self, key: str, value: dict) -> None:
        with self._lock:
            self._data[key] = value

    def save(self) -> None:
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)
