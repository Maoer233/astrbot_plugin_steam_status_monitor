import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.application.services.price_candidates import PriceCandidateCache
from src.application.services.price_query import PriceQueryService
from src.infrastructure.cache.inflight import InFlightRequests
from src.infrastructure.clients.itad import ITADGame


class PriceConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_appid_shares_one_upstream_request(self):
        calls = {"count": 0}

        async def fetch_detail(appid, country="CN", deadline=None):
            calls["count"] += 1
            await asyncio.sleep(0.05)
            return {"name": "Dark", "store_appid": appid, "_store_region": country}

        plugin = SimpleNamespace(
            ITAD_CLIENT=SimpleNamespace(get_price_summary=AsyncMock(return_value={})),
            config={"price_currency": "CNY", "price_region": "CN", "price_compare_regions": "NONE"},
            fetch_region_price=AsyncMock(return_value=None),
            fetch_game_details=fetch_detail,
            fetch_game_reviews_both=AsyncMock(return_value=None),
        )
        service = PriceQueryService(plugin)
        game = ITADGame(id="steam:1", title="Dark", appid="1")

        await asyncio.gather(
            service.build_card(game, include_itad=False, include_reviews=False),
            service.build_card(game, include_itad=False, include_reviews=False),
        )

        self.assertEqual(1, calls["count"])

    async def test_different_appids_reuse_one_request_slot_per_key(self):
        inflight = InFlightRequests()
        started = []

        async def request(appid):
            async def run():
                started.append(appid)
                await asyncio.sleep(0.01)
                return appid
            return await inflight.get_or_create(f"detail:{appid}", run)

        results = await asyncio.gather(*(request(str(index % 5)) for index in range(20)))

        self.assertEqual(5, len(started))
        self.assertEqual(20, len(results))
        await inflight.clear()
        self.assertEqual({}, inflight._tasks)

    async def test_region_fallback_stops_inside_budget(self):
        service, plugin = self._service()
        calls = {"count": 0}

        async def slow_summary(game_id, country="CN", timeout=None):
            calls["count"] += 1
            await asyncio.sleep(0.2)
            return {}

        plugin.ITAD_CLIENT.get_price_summary = slow_summary
        service.REGION_ATTEMPT_SECONDS = 0.05
        game = ITADGame(id="itad1", title="Dark", appid="1")

        started = asyncio.get_running_loop().time()
        with self.assertRaises(TimeoutError):
            await service._fetch_itad_summary(
                game,
                service._settings(),
                deadline=asyncio.get_running_loop().time() + 0.12,
            )
        elapsed = asyncio.get_running_loop().time() - started

        self.assertLess(elapsed, 0.5)
        self.assertLess(calls["count"], 5)

    async def test_review_failure_does_not_block_core_price(self):
        service, plugin = self._service()
        plugin.fetch_game_reviews_both = AsyncMock(side_effect=RuntimeError("reviews down"))
        plugin.fetch_game_details = AsyncMock(return_value={
            "name": "Dark",
            "store_appid": "1",
            "_store_region": "CN",
            "price_overview": {"final": 1000, "initial": 2000, "currency": "CNY", "discount_percent": 50},
        })
        plugin.ITAD_CLIENT.get_price_summary = AsyncMock(return_value={
            "current_price": 10.0,
            "currency": "CNY",
            "steam_low": 8.0,
            "steam_low_currency": "CNY",
        })

        card = await service.build_card(ITADGame(id="itad1", title="Dark", appid="1"))

        self.assertEqual(10.0, card.current_price["value"])
        self.assertIsNone(card.reviews)
        self.assertEqual({}, card.card_data["review_all"])

    async def test_expired_candidate_is_not_selected(self):
        now = {"value": 0.0}
        cache = PriceCandidateCache(clock=lambda: now["value"])
        saved = cache.put("group:1", [ITADGame(id="old", title="Old", appid="1")])
        now["value"] = PriceCandidateCache.TTL_SECONDS

        self.assertIsNone(cache.get("group:1"))
        self.assertTrue(cache.has_expired("group:1"))
        self.assertEqual(saved.query_id, cache.take_expired("group:1").query_id)
        self.assertIsNone(cache.get("group:1"))

    async def test_active_candidate_is_not_overwritten(self):
        cache = PriceCandidateCache(clock=lambda: 1.0)
        first = cache.put("group:1", [ITADGame(id="a", title="A", appid="1")])
        second = cache.put("group:1", [ITADGame(id="b", title="B", appid="2")])

        self.assertIs(first, second)
        self.assertEqual(["A"], [item.title for item in cache.get("group:1").games])

    def _service(self):
        plugin = SimpleNamespace(
            ITAD_CLIENT=SimpleNamespace(get_price_summary=AsyncMock(return_value={})),
            config={"price_currency": "CNY", "price_region": "CN", "price_compare_regions": "NONE"},
            fetch_region_price=AsyncMock(return_value=None),
            fetch_game_details=AsyncMock(return_value=None),
            fetch_game_reviews_both=AsyncMock(return_value=None),
        )
        return PriceQueryService(plugin), plugin
