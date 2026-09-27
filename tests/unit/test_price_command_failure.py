import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.application.services.price_candidates import PriceCandidateCache
from src.infrastructure.clients.itad import ITADGame
from src.presentation.commands.store import price


class _Event:
    def __init__(self, message):
        self.message_str = message
        self.unified_msg_origin = "group:1"
        self.texts = []

    def get_message_str(self):
        return self.message_str

    def plain_result(self, text):
        self.texts.append(text)
        return text


def _plugin(status="EMPTY"):
    return SimpleNamespace(
        price_candidates=PriceCandidateCache(clock=lambda: 1.0),
        proxy=None,
        price_query=SimpleNamespace(
            resolve_games=AsyncMock(return_value={"games": [], "status": status}),
            build_card=AsyncMock(),
            _search_message=lambda code: {
                "NOT_CONFIGURED": "ITAD 未配置，已无法查询完整价格。",
                "TIMEOUT": "价格搜索超时，请稍后重试。",
                "RATE_LIMITED": "价格搜索请求受限，请稍后重试。",
                "AUTH_ERROR": "ITAD 鉴权失败，请管理员检查配置。",
            }.get(code, "未找到匹配游戏。"),
        ),
    )


class PriceCommandFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_failures_use_safe_messages(self):
        cases = {
            "NOT_CONFIGURED": "ITAD 未配置",
            "TIMEOUT": "稍后重试",
            "RATE_LIMITED": "稍后重试",
            "AUTH_ERROR": "检查配置",
        }
        for status, hint in cases.items():
            with self.subTest(status=status):
                plugin = _plugin(status)
                texts = [item async for item in price(plugin, _Event("/steam price dark"), True, "price")]

                self.assertEqual(1, len(texts))
                self.assertIn(hint, texts[0])
                self.assertNotIn("http", texts[0].lower())
                self.assertNotIn("traceback", texts[0].lower())
                plugin.price_query.build_card.assert_not_called()

    async def test_build_card_exception_hides_upstream_details(self):
        game = ITADGame(id="itad1", title="Dark", appid="1")
        plugin = _plugin()
        plugin.price_query.resolve_games = AsyncMock(return_value={"games": [game], "status": "SUCCESS"})
        plugin.price_query.build_card = AsyncMock(
            side_effect=RuntimeError("https://api.isthereanydeal.com failed key=secret-key")
        )

        texts = [item async for item in price(plugin, _Event("/steam price dark"), True, "price")]

        self.assertEqual(["价格服务暂时不可用，请稍后重试。"], texts)
        self.assertNotIn("secret-key", texts[0])
        self.assertNotIn("http", texts[0].lower())

    async def test_renderer_failure_keeps_store_link_without_stack(self):
        game = ITADGame(id="itad1", title="Dark", appid="1")
        card = SimpleNamespace(
            card_data={"name": "Dark"},
            summary={},
            region_prices={},
            current_price={},
            history_low={},
            store_message="https://store.steampowered.com/app/1/",
        )
        plugin = _plugin()
        plugin.price_query.resolve_games = AsyncMock(return_value={"games": [game], "status": "SUCCESS"})
        plugin.price_query.build_card = AsyncMock(return_value=card)

        with patch(
            "src.presentation.commands.store.render_game_detail_image",
            AsyncMock(side_effect=RuntimeError("Traceback font missing")),
        ):
            texts = [item async for item in price(plugin, _Event("/steam px dark"), True, "px")]

        self.assertIn("价格已获取，但卡片生成失败。", texts[0])
        self.assertIn("https://store.steampowered.com/app/1/", texts[0])
        self.assertNotIn("Traceback", texts[0])
