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
            [call("itad1", "CN"), call("itad1", "HK")],
            plugin.ITAD_CLIENT.get_price_summary.await_args_list,
        )
