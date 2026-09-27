"""价格查询候选会话。过期和防覆盖只在这里判断。"""
import time
from dataclasses import dataclass, field


@dataclass
class PriceCandidateQuery:
    query_id: str
    created_at: float
    expires_at: float
    session_key: str
    games: list = field(default_factory=list)

    def expired(self, now: float) -> bool:
        return now >= self.expires_at


class PriceCandidateCache:
    """按会话保存一份未选择的候选。有效期内不覆盖。"""

    TTL_SECONDS = 180

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._items: dict[str, PriceCandidateQuery] = {}
        self._seq = 0

    def get(self, session_key: str):
        item = self._items.get(session_key)
        if item is None or item.expired(self._clock()):
            return None
        return item

    def has_expired(self, session_key: str) -> bool:
        item = self._items.get(session_key)
        return item is not None and item.expired(self._clock())

    def take_expired(self, session_key: str):
        item = self._items.get(session_key)
        if item is None or not item.expired(self._clock()):
            return None
        self._items.pop(session_key, None)
        return item

    def put(self, session_key: str, games: list):
        current = self.get(session_key)
        if current is not None:
            return current
        now = self._clock()
        self._seq += 1
        item = PriceCandidateQuery(
            query_id=f"{self._seq:04d}",
            created_at=now,
            expires_at=now + self.TTL_SECONDS,
            session_key=session_key,
            games=list(games),
        )
        self._items[session_key] = item
        return item

    def pop(self, session_key: str):
        return self._items.pop(session_key, None)

    def clear(self):
        self._items.clear()
