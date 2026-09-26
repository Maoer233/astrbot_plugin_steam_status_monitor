import asyncio
import unittest
from unittest.mock import AsyncMock

from src.infrastructure.cache.inflight import InFlightRequests


class InFlightRequestsTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_key_reuses_one_task(self):
        merged = InFlightRequests()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return "value"

        values = await asyncio.gather(
            merged.get_or_create("same", factory),
            merged.get_or_create("same", factory),
        )
        self.assertEqual(["value", "value"], values)
        self.assertEqual(1, calls)

    async def test_failed_task_is_removed(self):
        merged = InFlightRequests()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            raise RuntimeError("failed")

        with self.assertRaises(RuntimeError):
            await merged.get_or_create("same", factory)
        with self.assertRaises(RuntimeError):
            await merged.get_or_create("same", factory)
        self.assertEqual(2, calls)


if __name__ == "__main__":
    unittest.main()
