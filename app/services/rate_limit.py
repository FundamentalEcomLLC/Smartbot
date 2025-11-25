import logging
import time
from threading import Lock
from typing import Dict, Tuple

try:
    import redis  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    redis = None

from ..config import get_settings


class RateLimitExceeded(Exception):
    """Raised when a caller exceeds the allowed number of requests."""


class RateLimiter:
    """Naive in-memory rate limiter suitable for single-process dev deployments."""

    def __init__(self):
        self._state: Dict[str, Tuple[int, float]] = {}
        self._lock = Lock()

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        """Increment usage for `key` and raise if the limit is exceeded."""

        now = time.time()
        with self._lock:
            count, reset_ts = self._state.get(key, (0, now + window_seconds))
            if now > reset_ts:
                count = 0
                reset_ts = now + window_seconds
            if count >= limit:
                raise RateLimitExceeded()
            self._state[key] = (count + 1, reset_ts)


class RedisRateLimiter:
    """Distributed limiter backed by Redis INCR/EXPIRE."""

    def __init__(self, client: "redis.Redis") -> None:  # type: ignore[name-defined]
        self._client = client

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        namespaced_key = f"rate:{key}"
        new_count = int(self._client.incr(namespaced_key))
        if new_count == 1:
            self._client.expire(namespaced_key, window_seconds)
        if new_count > limit:
            raise RateLimitExceeded()


_settings = get_settings()
_logger = logging.getLogger(__name__)

if _settings.redis_url and redis is not None:
    _logger.info("Rate limiter using Redis backend")
    _redis_client = redis.Redis.from_url(_settings.redis_url)
    rate_limiter = RedisRateLimiter(_redis_client)
else:
    if _settings.redis_url and redis is None:
        _logger.warning("REDIS_URL provided but redis package is missing; falling back to in-memory limiter")
    rate_limiter = RateLimiter()
