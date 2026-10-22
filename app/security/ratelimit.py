"""In-memory sliding-window rate limiter.

Per process only: with several API replicas each keeps its own counters.
Production would move this to Redis or the API gateway (see SECURITY.md).
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

from app.errors import RateLimited
from app.observability import metrics


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0, max_keys: int = 50_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self.max_keys = max_keys
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, scope: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._hits) > self.max_keys:  # bound memory under key-spraying
                self._hits.clear()
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = max(1, math.ceil(hits[0] + self.window - now))
                metrics.RATE_LIMITED.labels(scope).inc()
                raise RateLimited(
                    "Too many requests. Please slow down.", headers={"Retry-After": str(retry_after)}
                )
            hits.append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
