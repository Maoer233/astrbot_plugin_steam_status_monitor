import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from src.application.services.price_query import PriceQueryService
from src.infrastructure.clients.itad import ITADGame


class PriceQueryServiceTests(unittest.IsolatedAsyncioTestCase):
    def _service(self, **client_methods):
        client = SimpleNamespace(**client_methods)
        plugin = SimpleNamespace(
            ITAD_CLIENT=client,
            config={"price_currency": "CNY", "price_region": "CN", "price_compare_regions": "NONE"},
            fetch_region_price=AsyncMock(return_value=None),
            fetch_game_details=AsyncMock(return_value=None),
            fetch_game_reviews_both=AsyncMock(return_value=None),
        )
        return PriceQueryService(plugin), plugin

    async def test_settings_returns_typed_named_values(self):
        service, _ = self._service()

        settings = service._settings()

        self.assertEqual("CNY", settings.currency)
        self.assertEqual("CN", settings.region)
        self.assertEqual("NONE", settings.compare_region)

    async def test_resolve_games_uses_store_url_lookup(self):
        game = ITADGame(id="itad1", title="Elden Ring", appid="1245620")
        service, plugin = self._service(
            lookup_steam_appid=AsyncMock(return_value=game),
            search_games=AsyncMock(return_value=[]),
        )

        games = await service.resolve_games("https://store.steampowered.com/app/1245620/")

        self.assertEqual([game], games)
        plugin.ITAD_CLIENT.lookup_steam_appid.assert_awaited_once_with("1245620")
        plugin.ITAD_CLIENT.search_games.assert_not_called()

    async def test_resolve_games_translates_chinese_when_search_empty(self):
        translated = ITADGame(id="itad1", title="Elden Ring", appid="1245620")
        translator = AsyncMock(return_value="Elden Ring")
        client = SimpleNamespace(
            lookup_steam_appid=AsyncMock(),
            search_games=AsyncMock(side_effect=[[], [translated]]),
        )
        plugin = SimpleNamespace(ITAD_CLIENT=client, config={})
        service = PriceQueryService(plugin, translator=translator)

        games = await service.resolve_games("艾尔登法环")

        self.assertEqual([translated], games)
        translator.assert_awaited_once_with("艾尔登法环")
        self.assertEqual(
            [call("艾尔登法环"), call("Elden Ring")],
            client.search_games.await_args_list,
        )

    async def test_build_card_falls_back_when_primary_itad_region_has_no_price(self):
        game = ITADGame(id="itad1", title="Locked", appid="1")
        service, plugin = self._service(
            get_price_summary=AsyncMock(side_effect=[
                {"current_price": None},
                {"current_price": 99, "region": "HK"},
            ]),
        )
        plugin.fetch_game_details = AsyncMock(return_value={"name": "Locked", "_store_region": "HK", "store_appid": "1"})
        plugin.fetch_game_reviews_both = AsyncMock(return_value={"all": {}, "schinese": {}})
        plugin.fetch_region_price = AsyncMock(return_value=None)

        card = await service.build_card(game)

        self.assertEqual(99, card.summary["current_price"])
        self.assertTrue(card.locked)
        self.assertIn("当前游戏锁", card.store_message)
        self.assertEqual(
            [call("itad1", "CN", timeout=4), call("itad1", "HK", timeout=4)],
            plugin.ITAD_CLIENT.get_price_summary.await_args_list,
        )

    async def test_core_timeout_does_not_wait_for_enhancements(self):
        game = ITADGame(id="itad1", title="Slow", appid="1")
        service, plugin = self._service(get_price_summary=AsyncMock(return_value={}))

        async def slow_detail(*args, **kwargs):
            await asyncio.sleep(0.05)
            return {"name": "Slow"}

        plugin.fetch_game_details = slow_detail
        plugin.fetch_game_reviews_both = AsyncMock(return_value={"all": {"text": "好评"}})
        service.CORE_BUDGET_SECONDS = 0.01
        service.TOTAL_BUDGET_SECONDS = 0.2

        with self.assertRaises(TimeoutError):
            await service.build_card(game)
        plugin.fetch_game_reviews_both.assert_not_called()

    async def test_enhancement_timeout_keeps_core_card(self):
        game = ITADGame(id="itad1", title="Core", appid="1")
        service, plugin = self._service(
            get_price_summary=AsyncMock(return_value={"current_price": 42, "currency": "CNY"}),
        )
        plugin.fetch_game_details = AsyncMock(return_value={"name": "Core", "store_appid": "1"})
        async def region_price(appid, region, deadline=None):
            if region == "UA":
                await asyncio.sleep(0.3)
                return {"current_price": 10, "region": "UA", "currency": "UAH"}
            return {"current_price": 42, "region": region, "currency": "CNY"}

        plugin.fetch_region_price = region_price

        async def slow_reviews(*args, **kwargs):
            await asyncio.sleep(0.3)
            return {"all": {"text": "好评"}}

        plugin.fetch_game_reviews_both = slow_reviews
        plugin.config["price_compare_regions"] = "UA"
        service.CORE_BUDGET_SECONDS = 0.2
        service.TOTAL_BUDGET_SECONDS = 0.23
        service.REGION_ATTEMPT_SECONDS = 0.05

        card = await service.build_card(game)

        self.assertEqual(42, card.summary["current_price"])
        self.assertEqual("Core", card.detail["name"])
        self.assertIsNone(card.reviews)
        self.assertNotIn("UA", card.region_prices)

    async def test_current_price_prefers_itad_over_store(self):
        game = ITADGame(id="itad1", title="Priced", appid="1")
        service, plugin = self._service(
            get_price_summary=AsyncMock(return_value={
                "current_price": 42,
                "current_regular": 60,
                "currency": "CNY",
                "cut": 30,
                "steam_low": 21,
                "steam_low_currency": "CNY",
                "steam_low_cut": 65,
                "history_low": 9,
            }),
        )
        plugin.fetch_game_details = AsyncMock(return_value={
            "name": "Priced",
            "price_overview": {"final": 9900, "initial": 12800, "currency": "CNY", "discount_percent": 23},
        })

        card = await service.build_card(game, include_reviews=False)

        self.assertEqual("itad_steam", card.current_price["source"])
        self.assertEqual(42, card.current_price["value"])
        self.assertEqual("itad_steam_store_low", card.history_low["source"])
        self.assertEqual(21, card.history_low["value"])

    async def test_current_price_uses_store_when_itad_has_no_price(self):
        game = ITADGame(id="itad1", title="Store", appid="1")
        service, plugin = self._service(get_price_summary=AsyncMock(return_value={}))
        plugin.fetch_game_details = AsyncMock(return_value={
            "name": "Store",
            "price_overview": {"final": 9900, "initial": 12800, "currency": "CNY", "discount_percent": 23},
        })

        card = await service.build_card(game, include_reviews=False)

        self.assertEqual("steam_store", card.current_price["source"])
        self.assertEqual(99, card.current_price["value"])
        self.assertEqual("none", card.history_low["source"])
        self.assertIsNone(card.history_low["value"])

    async def test_itad_region_fallback_uses_remaining_attempt_budget(self):
        game = ITADGame(id="itad1", title="Fallback", appid="1")
        service, plugin = self._service(get_price_summary=AsyncMock(return_value={"current_price": None}))
        plugin.fetch_game_details = AsyncMock(return_value={"name": "Fallback"})

        card = await service.build_card(game, include_reviews=False)

        self.assertLessEqual(
            plugin.ITAD_CLIENT.get_price_summary.await_args.kwargs.get("timeout", 99),
            service.REGION_ATTEMPT_SECONDS,
        )
        self.assertEqual("none", card.current_price["source"])
