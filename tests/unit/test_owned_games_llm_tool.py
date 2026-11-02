"""LLM 工具 steam_owned_games：仅允许查询发送者本人绑定。"""
import inspect
import unittest

from src.plugin.steam_status_monitor import SteamStatusMonitorV3


class _Svc:
    def __init__(self):
        self.calls = []

    async def list_for_sender(self, sender_id, query="", offset=0, limit=20):
        self.calls.append({"sender_id": sender_id, "query": query, "offset": offset, "limit": limit})
        return {"steam_id": "s1", "total_owned": 5, "games": []}


class _Event:
    def get_sender_id(self):
        return "10001"


class LlmToolSecurityTests(unittest.IsolatedAsyncioTestCase):
    def _raw_handler(self):
        handler = SteamStatusMonitorV3.__dict__["steam_owned_games"]
        return getattr(handler, "__wrapped__", handler)

    def test_signature_has_no_external_identity_params(self):
        params = set(inspect.signature(self._raw_handler()).parameters)
        self.assertEqual(
            params,
            {"self", "event", "query", "offset", "limit", "_ignored"},
        )

    async def test_tool_queries_sender_binding_only(self):
        handler = self._raw_handler()
        stub = type("Stub", (), {})()
        stub.owned_games = _Svc()

        result = await handler(stub, _Event(), query="dot", offset=3, limit=10)

        self.assertEqual(
            stub.owned_games.calls,
            [{"sender_id": "10001", "query": "dot", "offset": 3, "limit": 10}],
        )
        self.assertIn('"total_owned": 5', result)

    async def test_stray_event_kwarg_is_ignored(self):
        """官方机器人链路可能把 event 作为关键字参数传入；
        位置参数里的真实事件对象不能被覆盖。
        """
        handler = self._raw_handler()
        stub = type("Stub", (), {})()
        stub.owned_games = _Svc()

        result = await handler(
            stub,
            _Event(),
            event='{"platform_id": "default", "message_type": "GroupMessage"}',
            query="dot",
        )

        self.assertEqual(
            stub.owned_games.calls,
            [{"sender_id": "10001", "query": "dot", "offset": 0, "limit": 20}],
        )
        self.assertIn('"total_owned": 5', result)


if __name__ == "__main__":
    unittest.main()
