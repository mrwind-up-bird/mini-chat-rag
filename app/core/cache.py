"""Lightweight in-memory TTL cache for expensive queries.

Designed for stats endpoints where identical requests from the same tenant
within a short window should return cached results instead of re-running
aggregate SQL queries.
"""

import time
from collections.abc import Hashable
from typing import Any

_cache: dict[Hashable, tuple[float, Any]] = {}

# Default TTL in seconds
DEFAULT_TTL = 30


def get(key: Hashable, ttl: float = DEFAULT_TTL) -> Any | None:
    """Return cached value if present and not expired, else None."""
    entry = _cache.get(key)
    if entry is None:
        return None
    stored_at, value = entry
    if time.monotonic() - stored_at > ttl:
        _cache.pop(key, None)
        return None
    return value


def put(key: Hashable, value: Any) -> None:
    """Store a value in the cache."""
    _cache[key] = (time.monotonic(), value)


def invalidate(key: Hashable) -> None:
    """Remove a specific cache entry."""
    _cache.pop(key, None)


def clear() -> None:
    """Clear all cached entries."""
    _cache.clear()
