"""实例级同键异步请求合并。"""
import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class InFlightRequests:
    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, key: str, factory: Callable[[], Awaitable[Any]]):
        async with self._lock:
            task = self._tasks.get(key)
            if task is None or task.done():
                task = asyncio.create_task(factory(), name=f"price-request:{key}")
                self._tasks[key] = task
                task.add_done_callback(
                    lambda completed, request_key=key: self._cleanup(request_key, completed)
                )
        return await asyncio.shield(task)

    def _cleanup(self, key: str, task: asyncio.Task):
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)

    async def clear(self):
        async with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
