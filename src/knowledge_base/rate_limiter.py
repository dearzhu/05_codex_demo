"""In-memory token bucket rate limiter"""

import time
from collections import defaultdict


class TokenBucket:
    """Token bucket for per-key rate limiting"""

    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()

    def consume(self, tokens: float = 1.0) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(float(self.capacity), self.tokens + elapsed * self.rate)
        self.last_refill = now
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


class RateLimiter:
    """Per-IP rate limiter using token buckets"""

    def __init__(self, rate: float = 10, capacity: int = 20):
        self.default_rate = rate
        self.default_capacity = capacity
        self._buckets: dict[str, TokenBucket] = {}

    def check(self, key: str, rate: float = 0, capacity: int = 0) -> bool:
        if key not in self._buckets:
            self._buckets[key] = TokenBucket(
                rate or self.default_rate,
                capacity or self.default_capacity,
            )
        return self._buckets[key].consume()

    def cleanup(self, max_age: float = 300.0):
        now = time.monotonic()
        stale = [k for k, b in self._buckets.items()
                 if now - b.last_refill > max_age]
        for k in stale:
            del self._buckets[k]


# Singleton
_default_limiter = None


def get_rate_limiter() -> RateLimiter:
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RateLimiter()
    return _default_limiter
