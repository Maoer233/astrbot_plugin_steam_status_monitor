import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.application.services.price_query import PriceQueryService
from src.infrastructure.clients.itad import ITADClient, ProviderError


class SearchFailureStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_itad_search_is_not_empty(self):
        client = ITADClient()
        with patch.object(client, "_lookup_steam_items", AsyncMock(return_value=[])):
            result = await client.search_games("ELDEN RING")

        self.assertEqual([], result["games"])
        self.assertEqual("NOT_CONFIGURED", result["status"])
        self.assertFalse(result["retryable"])
        self.assertTrue(result["used_fallback"])

    async def test_search_keeps_provider_error_codes(self):
        cases = (
            ("AUTH_ERROR", False),
            ("RATE_LIMITED", False),
            ("UPSTREAM_ERROR", True),
            ("INVALID_RESPONSE", True),
        )
        for code, retryable in cases:
            with self.subTest(code=code):
                client = ITADClient(api_key="secret-key")
                with (
                    patch.object(client, "_lookup_steam_items", AsyncMock(return_value=[])),
                    patch.object(client, "_get", AsyncMock(side_effect=ProviderError(code, retryable=retryable))),
                ):
                    result = await client.search_games("ELDEN RING")

                self.assertEqual(code, result["status"])
                self.assertEqual(retryable, result["retryable"])
                self.assertNotIn("secret-key", str(result))

    async def test_steam_search_timeout_falls_back_to_html(self):
        client = ITADClient(api_key="test")
        html_hit = [{"appid": "1245620", "name": "ELDEN RING", "type": "app"}]
        with (
            patch.object(client, "_steam_storesearch", AsyncMock(return_value=([], "TIMEOUT"))),
            patch.object(client, "_steam_search_html", AsyncMock(return_value=html_hit)) as html,
        ):
            items = await client._steam_search("ELDEN RING")

        self.assertEqual(html_hit, items)
        html.assert_awaited_once()


class SearchFailureMessageTests(unittest.IsolatedAsyncioTestCase):
    def test_user_messages_are_actionable_and_hide_internals(self):
        expected = {
            "NOT_CONFIGURED": "ITAD 未配置",
            "TIMEOUT": "稍后重试",
            "RATE_LIMITED": "稍后重试",
            "AUTH_ERROR": "检查配置",
            "UPSTREAM_ERROR": "稍后重试",
            "INVALID_RESPONSE": "稍后重试",
        }
        for status, hint in expected.items():
            message = PriceQueryService._search_message(status)
            self.assertIn(hint, message)
            self.assertNotIn("http", message.lower())
            self.assertNotIn("traceback", message.lower())
            self.assertNotIn("api key", message.lower())

    async def test_timeout_and_rate_limit_do_not_trigger_translation(self):
        translator = AsyncMock(return_value="ELDEN RING")
        for status in ("TIMEOUT", "RATE_LIMITED"):
            with self.subTest(status=status):
                client = SimpleNamespace(search_games=AsyncMock(return_value={
                    "games": [],
                    "status": status,
                    "provider": "itad",
                    "retryable": status == "TIMEOUT",
                    "used_fallback": True,
                }))
                plugin = SimpleNamespace(ITAD_CLIENT=client, config={})
                service = PriceQueryService(plugin, translator=translator)

                resolved = await service.resolve_games("艾尔登法环")

                self.assertEqual(status, resolved["status"])
                translator.assert_not_awaited()
                client.search_games.assert_awaited_once()
