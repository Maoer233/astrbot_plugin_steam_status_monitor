import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.application.services.price_candidates import PriceCandidateCache
from src.infrastructure.clients.itad import ITADGame
from src.presentation.commands.store import handle_selection, price


class _Event:
    def __init__(self, message, origin="group:1"):
        self.message_str = message
        self.unified_msg_origin = origin
        self.texts = []
        self.stopped = False

    def get_message_str(self):
        return self.message_str

    def plain_result(self, text):
        self.texts.append(text)
        return text

    def stop_event(self):
        self.stopped = True


def _plugin(clock, games=None):
    return SimpleNamespace(
        price_candidates=PriceCandidateCache(clock=clock),
        price_query=SimpleNamespace(
            resolve_games=AsyncMock(return_value={"games": games or [], "status": "SUCCESS"}),
            build_card=AsyncMock(),
            _search_message=lambda status: status,
        ),
    )


class PriceCandidateCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_selection_does_not_pick_old_game(self):
        now = {"value": 0.0}
        old = ITADGame(id="old", title="Old", appid="1")
        plugin = _plugin(lambda: now["value"], [old, ITADGame(id="other", title="Other", appid="2")])
        event = _Event("/steam price dark")

        texts = [item async for item in price(plugin, event, False, "price")]

        self.assertIn("查询 #0001", texts[0])
        now["value"] = PriceCandidateCache.TTL_SECONDS
        select = _Event("1")
        expired = [item async for item in handle_selection(plugin, select)]

        self.assertEqual(["候选已过期，请重新查询。"], expired)
        plugin.price_query.build_card.assert_not_called()
        self.assertIsNone(plugin.price_candidates.get("group:1"))

    async def test_active_query_is_not_overwritten(self):
        now = {"value": 10.0}
        first = [ITADGame(id="a", title="A", appid="1"), ITADGame(id="b", title="B", appid="2")]
        second = [ITADGame(id="c", title="C", appid="3"), ITADGame(id="d", title="D", appid="4")]
        plugin = _plugin(lambda: now["value"], first)

        await anext(price(plugin, _Event("/steam price first"), False, "price"))
        plugin.price_query.resolve_games = AsyncMock(return_value={"games": second, "status": "SUCCESS"})
        again = [item async for item in price(plugin, _Event("/steam price second"), False, "price")]

        self.assertIn("已有未完成的查询 #0001", again[0])
        self.assertEqual(["A", "B"], [item.title for item in plugin.price_candidates.get("group:1").games])

    async def test_selection_uses_saved_query_id(self):
        now = {"value": 1.0}
        games = [ITADGame(id="a", title="A", appid="1"), ITADGame(id="b", title="B", appid="2")]
        plugin = _plugin(lambda: now["value"], games)
        plugin.price_query.build_card = AsyncMock(side_effect=TimeoutError)

        await anext(price(plugin, _Event("/steam price dark"), False, "price"))
        selected = [item async for item in handle_selection(plugin, _Event("2"))]

        self.assertEqual(["价格查询超时，请稍后重试。"], selected)
        plugin.price_query.build_card.assert_awaited_once()
        self.assertEqual("B", plugin.price_query.build_card.await_args.args[0].title)
        self.assertIsNone(plugin.price_candidates.get("group:1"))
