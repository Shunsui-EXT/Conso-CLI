"""
Adaptive concurrency engine.

Auto-scales worker concurrency: backs off on 429/403/503 and ramps back up as
stable 200s resume. Thread-safe, with a token-bucket style pacing delay.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass


@dataclass
class AdaptiveConcurrency:
    initial: int = 4
    minimum: int = 1
    maximum: int = 16
    backoff_factor: float = 0.5
    ramp_step: int = 1
    ramp_after_successes: int = 10

    _limit: int = 0
    _successes: int = 0
    _lock: threading.Lock = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._limit = max(self.minimum, min(self.initial, self.maximum))
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        with self._lock:
            return self._limit

    def report_success(self) -> None:
        with self._lock:
            self._successes += 1
            if self._successes >= self.ramp_after_successes and self._limit < self.maximum:
                self._limit = min(self._limit + self.ramp_step, self.maximum)
                self._successes = 0

    def report_rate_limit(self) -> None:
        with self._lock:
            self._limit = max(self.minimum, int(self._limit * self.backoff_factor) or self.minimum)
            self._successes = 0

    def report_error(self) -> None:
        self.report_rate_limit()


class Pacer:
    """Jittered sleep between requests to avoid fixed-interval detection."""

    def __init__(self, min_seconds: float, max_seconds: float) -> None:
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            target = random.uniform(self.min_seconds, self.max_seconds)
            elapsed = now - self._last
            if elapsed < target:
                time.sleep(target - elapsed)
            self._last = time.monotonic()
