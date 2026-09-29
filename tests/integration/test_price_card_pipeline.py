import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.application.services.price_candidates import PriceCandidateCache
from src.application.services.price_query import PriceQueryService
from src.infrastructure.clients.itad import ITADClient, ITADGame, ProviderError
from src.presentation.commands.store import game, price


class _Event:
    def __init__(self, message):
        self.message_str = message
        self.unified_msg_origin = "group:1"
        self.texts = []
        self.images = []
        self.stopped = False

    def get_message_str(self):
        return self.message_str

    def plain_result(self, text):
        self.texts.append(text)
        return text

    def image_result(self, path):
        self.images.append(path)
        return path

    def stop_event(self):
        self.stopped = True

    def make_result(self):
        return self

    def base64_image(self, payload):
        self.images.append(payload)
        return self

    def message(self, text):
        self.texts.append(text)
        return self


def _itad_payload():
    return [{
        "id": "itad-dark",
        "deals": [
            {
                "shop": {"id": 61, "name": "Steam"},
                "price": {"amount": 149.0, "currency": "CNY"},
                "regular": {"amount": 298.0, "currency": "CNY"},
                "cut": 50,
                "storeLow": {"amount": 89.0, "currency": "CNY"},
            },
            {
                "shop": {"id": 9, "name": "Fanatical"},
                "price": {"amount": 12.0, "currency": "USD"},
                "regular": {"amount": 20.0, "currency": "USD"},
                "cut": 40,
            },
        ],
        "historyLow": {"all": {"amount": 8.0, "currency": "USD"}},
    }]


def _detail(region="CN", price=14900):
    return {
        "name": "Dark Game",
        "store_appid": "1245620",
        "_store_region": region,
        "_store_fallback": region != "CN",
        "price_overview": {
            "final": price,
            "initial": 29800,
            "currency": "CNY" if region == "CN" else "USD",
            "discount_percent": 50,
        },
        "short_description": "A locked region test.",
        "genres": [],
        "developers": ["FromSoftware"],
        "release_date": {"date": "2022"},
    }


class PriceCardPipelineTests(unittest.IsolatedAsyncioTestCase):
    def _service(self, client, **plugin_methods):
        plugin = SimpleNamespace(
            ITAD_CLIENT=client,
            config={"price_currency": "CNY", "price_region": "CN", "price_compare_regions": "NONE"},
            fetch_region_price=plugin_methods.get("fetch_region_price", AsyncMock(return_value=None)),
            fetch_game_details=plugin_methods.get("fetch_game_details", AsyncMock(return_value=None)),
            fetch_game_reviews_both=plugin_methods.get("fetch_game_reviews_both", AsyncMock(return_value=None)),
        )
        return PriceQueryService(plugin), plugin

    async def test_raw_itad_response_reaches_price_card_and_renderer(self):
        client = ITADClient(api_key="test")
        service, plugin = self._service(
            client,
            fetch_game_details=AsyncMock(return_value=_detail()),
            fetch_region_price=AsyncMock(return_value={
                "currency": "CNY",
                "current_price": 149.0,
                "current_regular": 298.0,
                "cut": 50,
                "region": "CN",
            }),
        )
        game_item = ITADGame(id="itad-dark", title="Dark Game", appid="1245620")

        with patch.object(client, "_post", AsyncMock(return_value=_itad_payload())) as post:
            card = await service.build_card(game_item, include_reviews=False)
            image = await self._render(card)

        post.assert_awaited()
        self.assertEqual("/games/prices/v3", post.await_args.args[0])
        self.assertEqual(149.0, card.summary["current_price"])
        self.assertEqual("CNY", card.summary["currency"])
        self.assertEqual(89.0, card.summary["steam_low"])
        self.assertAlmostEqual(80.7, card.summary["cdk_amount"], places=2)
        self.assertEqual("CNY", card.summary["cdk_currency"])
        self.assertEqual("itad_steam", card.current_price["source"])
        self.assertEqual("itad_steam_store_low", card.history_low["source"])
        self.assertTrue(image.startswith(b"\x89PNG"))
        plugin.fetch_game_reviews_both.assert_not_called()

    async def test_locked_region_fallback_and_history_low_render_together(self):
        client = ITADClient(api_key="test")
        service, _ = self._service(
            client,
            fetch_game_details=AsyncMock(return_value=_detail("HK", 1999)),
            fetch_region_price=AsyncMock(return_value={
                "currency": "HKD",
                "current_price": 128.0,
                "current_regular": 256.0,
                "cut": 50,
                "region": "HK",
                "requested_region": "CN",
                "is_fallback": True,
            }),
        )
        game_item = ITADGame(id="itad-dark", title="Dark Game", appid="1245620")
        payload = [{
            "id": "itad-dark",
            "deals": [{
                "shop": {"id": 61, "name": "Steam"},
                "price": {"amount": 128.0, "currency": "HKD"},
                "regular": {"amount": 256.0, "currency": "HKD"},
                "cut": 50,
                "storeLow": {"amount": 99.0, "currency": "HKD"},
            }],
        }]

        with (
            patch.object(client, "_post", AsyncMock(side_effect=[[], payload])),
            patch("src.presentation.renderers.game_detail._download_image", AsyncMock(return_value=None)),
        ):
            card = await service.build_card(game_item, include_reviews=False)
            image = await self._render(card)

        self.assertTrue(card.locked)
        self.assertTrue(card.current_price["is_fallback"])
        self.assertEqual("HK", card.current_price["actual_region"])
        self.assertEqual("CN", card.current_price["requested_region"])
        self.assertIn("锁国区", card.store_message)
        self.assertIn("港区回退", card.store_message)
        self.assertEqual("itad_steam_store_low", card.history_low["source"])
        self.assertTrue(image.startswith(b"\x89PNG"))

    async def test_steam_and_itad_prices_are_both_kept(self):
        client = ITADClient(api_key="test")
        service, plugin = self._service(
            client,
            fetch_region_price=AsyncMock(return_value={
                "currency": "CNY",
                "current_price": 160.0,
                "current_regular": 298.0,
                "cut": 46,
                "region": "CN",
            }),
            fetch_game_details=AsyncMock(return_value=_detail()),
        )

        with patch.object(client, "_post", AsyncMock(return_value=_itad_payload())):
            card = await service.build_card(
                ITADGame(id="itad-dark", title="Dark Game", appid="1245620"),
                include_reviews=False,
            )

        self.assertEqual(149.0, card.summary["current_price"])
        self.assertEqual(160.0, card.region_prices["CN"]["current_price"])
        self.assertEqual("itad_steam", card.current_price["source"])
        self.assertEqual("steam_store", card.region_prices["CN"]["source"])
        plugin.fetch_region_price.assert_awaited()

    async def test_price_command_selection_reaches_renderer(self):
        first = ITADGame(id="itad1", title="Dark", appid="1")
        second = ITADGame(id="itad2", title="Darker", appid="2")
        plugin = SimpleNamespace(
            price_candidates=PriceCandidateCache(clock=lambda: 1.0),
            proxy=None,
            price_query=SimpleNamespace(
                resolve_games=AsyncMock(return_value={"games": [first, second], "status": "SUCCESS"}),
                build_card=AsyncMock(return_value=SimpleNamespace(
                    card_data={"name": "Darker"},
                    summary={"current_price": 10, "currency": "CNY"},
                    region_prices={},
                    current_price={"value": 10, "currency": "CNY", "source": "itad_steam"},
                    history_low={"value": 8, "currency": "CNY", "source": "itad_steam_store_low"},
                    store_message="https://store.steampowered.com/app/2/",
                )),
            ),
        )
        event = _Event("/steam price dark")

        listed = [item async for item in price(plugin, event, False, "price")]
        selected_event = _Event("2")
        with patch(
            "src.presentation.commands.store.render_game_detail_image",
            AsyncMock(return_value=b"\x89PNG"),
        ) as render:
            selected = [item async for item in price(plugin, selected_event, False, "price")]

        self.assertIn("查询 #0001", listed[0])
        render.assert_awaited_once()
        self.assertEqual("itad2", plugin.price_query.build_card.await_args.args[0].id)
        self.assertTrue(selected)
        self.assertIn("https://store.steampowered.com/app/2/", selected_event.texts)

    async def test_game_command_does_not_call_itad(self):
        plugin = SimpleNamespace(
            proxy=None,
            price_query=SimpleNamespace(build_store_card=AsyncMock(return_value=SimpleNamespace(
                detail={"name": "Dark"},
                card_data={"name": "Dark"},
            ))),
            ITAD_CLIENT=SimpleNamespace(get_prices=AsyncMock(), get_price_summary=AsyncMock()),
        )

        with patch(
            "src.presentation.commands.store.render_game_detail_image",
            AsyncMock(return_value=b"\x89PNG"),
        ):
            results = [item async for item in game(plugin, _Event("/steam game 1245620"), "1245620")]

        self.assertTrue(results)
        plugin.price_query.build_store_card.assert_awaited_once_with("1245620")
        plugin.ITAD_CLIENT.get_prices.assert_not_called()
        plugin.ITAD_CLIENT.get_price_summary.assert_not_called()

    async def test_store_url_lookup_failure_builds_steam_only_card(self):
        client = ITADClient(api_key="test")
        service, plugin = self._service(
            client,
            fetch_game_details=AsyncMock(return_value=_detail()),
        )

        with patch.object(client, "lookup_steam_appid", AsyncMock(return_value=None)):
            resolved = await service.resolve_games("https://store.steampowered.com/app/1245620/")
            card = await service.build_card(resolved["games"][0])

        self.assertEqual("steam:1245620", resolved["games"][0].id)
        self.assertEqual("steam_store", card.current_price["source"])
        plugin.fetch_game_details.assert_awaited()
        self.assertFalse(hasattr(client, "_post_called"))

    async def test_itad_mapping_failure_does_not_request_itad_prices(self):
        client = ITADClient(api_key="test")
        service, _ = self._service(client, fetch_game_details=AsyncMock(return_value=_detail()))
        steam_only = ITADGame(id="steam:1245620", title="Dark Game", appid="1245620")

        with patch.object(client, "_post", AsyncMock()) as post:
            card = await service.build_card(steam_only, include_reviews=False)

        post.assert_not_called()
        self.assertEqual("", steam_only.itad_id)
        self.assertEqual("steam_store", card.current_price["source"])

    async def test_itad_failures_keep_actionable_status(self):
        cases = (
            (ProviderError("NOT_CONFIGURED"), "ITAD 未配置"),
            (ProviderError("TIMEOUT", retryable=True), "稍后重试"),
            (ProviderError("RATE_LIMITED", retryable=True), "稍后重试"),
        )
        for error, hint in cases:
            with self.subTest(code=error.code):
                client = ITADClient(api_key="test")
                service, _ = self._service(client)
                with (
                    patch.object(client, "_lookup_steam_items", AsyncMock(return_value=[])),
                    patch.object(client, "_get", AsyncMock(side_effect=error)),
                ):
                    resolved = await service.resolve_games("ELDEN RING")

                message = service._search_message(resolved["status"])
                self.assertEqual(error.code, resolved["status"])
                self.assertIn(hint, message)
                self.assertNotIn("http", message.lower())

    async def _render(self, card):
        from src.presentation.renderers.game_detail import render_game_detail_image

        with patch("src.presentation.renderers.game_detail._download_image", AsyncMock(return_value=None)):
            return await render_game_detail_image(
                card.card_data,
                itad_summary=card.summary,
                region_prices=card.region_prices,
                current_price=card.current_price,
                history_low=card.history_low,
            )
