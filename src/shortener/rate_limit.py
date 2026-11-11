"""Process-local sliding window used on link creation."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class LimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: int):
        if limit < 1:
            raise ValueError("limit must be positive")
        if window_seconds < 1:
            raise ValueError("window_seconds must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> LimitDecision:
        now = time.monotonic()
        with self._lock:
            fresh = [stamp for stamp in self._hits.get(key, []) if now - stamp < self.window_seconds]
            if len(fresh) >= self.limit:
                retry = max(1, int(self.window_seconds - (now - fresh[0])) + 1)
                self._hits[key] = fresh
                return LimitDecision(False, self.limit, 0, retry)
            fresh.append(now)
            self._hits[key] = fresh
            return LimitDecision(True, self.limit, self.limit - len(fresh), 0)
