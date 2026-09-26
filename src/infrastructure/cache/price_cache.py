"""价格查询的实例级内存 TTL 缓存。"""
import time
from typing import Any


class PriceTTLCache:
    def __init__(self, ttl_seconds: dict[str, float] | None = None, clock=time.monotonic):
        self._ttl = {
            "itad_summary": 300.0,
            "region_price": 300.0,
            "store_detail": 3600.0,
            **(ttl_seconds or {}),
        }
        self._clock = clock
        self._items: dict[tuple[str, str], tuple[float, Any]] = {}

    def get(self, kind: str, key: str):
        cache_key = (str(kind), str(key))
        item = self._items.get(cache_key)
        if item is None:
            return None
        expires_at, value = item
        if self._clock() >= expires_at:
            self._items.pop(cache_key, None)
            return None
        return value

    def set(self, kind: str, key: str, value):
        ttl = float(self._ttl.get(str(kind), 0))
        if ttl <= 0:
            return
        self._items[(str(kind), str(key))] = (self._clock() + ttl, value)

    def clear(self):
        self._items.clear()
