import asyncio


class AsyncTTLCache:
    """进程内 TTL 缓存，并合并相同键的并发计算。"""

    def __init__(self):
        self._values = {}
        self._inflight = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, key, ttl, factory):
        loop = asyncio.get_running_loop()
        now = loop.time()
        async with self._lock:
            cached = self._values.get(key)
            if cached and cached[0] > now:
                return cached[1]

            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(factory())
                self._inflight[key] = task
                task.add_done_callback(
                    lambda completed, cache_key=key: asyncio.create_task(
                        self._complete(cache_key, completed, ttl)
                    )
                )

        return await asyncio.shield(task)

    async def _complete(self, key, task, ttl):
        async with self._lock:
            if self._inflight.get(key) is not task:
                return
            self._inflight.pop(key, None)
            if not task.cancelled() and task.exception() is None:
                self._values[key] = (
                    asyncio.get_running_loop().time() + ttl,
                    task.result(),
                )

    def invalidate(self, *names):
        if not names:
            self._values.clear()
            return
        targets = set(names)
        self._values = {
            key: value
            for key, value in self._values.items()
            if not isinstance(key, tuple) or key[0] not in targets
        }
