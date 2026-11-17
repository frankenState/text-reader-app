"""Small in-memory LRU so repeated text (and Stop/Play cycles) are instant."""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict

import numpy as np

from .config import MAX_CACHE_ENTRIES

Key = tuple[str, str, str]  # voice, speed, text


class AudioCache:
    def __init__(self, max_entries: int = MAX_CACHE_ENTRIES) -> None:
        self._data: OrderedDict[Key, np.ndarray] = OrderedDict()
        self._max = max_entries
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(text: str, voice: str, speed: float) -> Key:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        return (voice, f"{speed:.2f}", digest)

    def get(self, key: Key) -> np.ndarray | None:
        with self._lock:
            audio = self._data.get(key)
            if audio is None:
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return audio

    def put(self, key: Key, audio: np.ndarray) -> None:
        with self._lock:
            self._data[key] = audio
            self._data.move_to_end(key)
            while len(self._data) > self._max:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._data), "hits": self.hits, "misses": self.misses}
