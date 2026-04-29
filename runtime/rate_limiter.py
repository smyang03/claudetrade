from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: int
    retry_after_sec: float


class V2RateLimiter:
    def __init__(self, *, max_calls: int, per_seconds: float):
        self.max_calls = max(1, int(max_calls))
        self.per_seconds = max(0.001, float(per_seconds))
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, now: float) -> RateLimitDecision:
        bucket = self._hits[str(key)]
        cutoff = float(now) - self.per_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_calls:
            retry_after = max(0.0, self.per_seconds - (float(now) - bucket[0]))
            return RateLimitDecision(False, 0, retry_after)
        return RateLimitDecision(True, self.max_calls - len(bucket), 0.0)

    def allow(self, key: str, now: float) -> bool:
        decision = self.check(key, now)
        if decision.allowed:
            self._hits[str(key)].append(float(now))
            return True
        return False

