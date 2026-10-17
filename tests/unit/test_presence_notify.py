"""用户上线/下线（presence）推送：纯函数判定、轮询入队/防抖与文本行发送。"""

import ast
import asyncio
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.domain.monitoring.presence import presence_transition
from src.presentation.formatters.status import format_play_duration
from src.shared.utils.notify_session import is_sendable_group_session
from tests.unit.test_status_change_quit import FakePlugin

PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_PATH = PROJECT_ROOT / "src/application/services/notification_tracking.py"
CHECK_TIME = "src.application.services.status_change_tracking.time.time"


class _Plain:
    def __init__(self, text):
        self.text = text


class _Image:
    @staticmethod
    def fromFileSystem(path):
        return ("image", path)


class _MessageChain:
    def __init__(self, chain):
        self.chain = chain


class _StubLogger:
    def exception(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


def _load_notification_methods(names):
    """AST 抽取 NotificationTrackingMixin 的指定方法（astrbot 不可导入，同 test_notification_deduplication）。"""
    tree = ast.parse(IMPLEMENTATION_PATH.read_text(encoding="utf-8"))
    mixin = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "NotificationTrackingMixin"
    )
    methods = [
        node
        for node in mixin.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    module = ast.Module(body=methods, type_ignores=[])
    namespace = {
        "asyncio": asyncio,
        "time": time,
        "logger": _StubLogger(),
        "Plain": _Plain,
        "Image": _Image,
        "MessageChain": _MessageChain,
        "is_sendable_group_session": is_sendable_group_session,
        "format_play_duration": format_play_duration,
    }
    exec(compile(ast.fix_missing_locations(module), str(IMPLEMENTATION_PATH), "exec"), namespace)
    return namespace


SEND_METHODS = (
    "_get_notify_sessions",
    "_should_send_notification",
    "_finish_notification",
    "_notification_event_key",
    "_send_merged_notification",
    "_render_notification_image",
)


def _build_send_plugin():
    methods = _load_notification_methods(SEND_METHODS)
    plugin_type = type("SendFakePlugin", (), {name: methods[name] for name in SEND_METHODS})
    plugin = plugin_type()
    plugin.config = {"notify_send_text": True, "notify_send_image": False}
    plugin.notify_sessions = {"g1": "test:GroupMessage:123"}
    plugin.push_groups = {}
    plugin.group_monitor_enabled = {}
    plugin.sent = []

    async def send_message(session, chain):
        plugin.sent.append((session, chain))

    plugin.context = type("Ctx", (), {"send_message": staticmethod(send_message)})()
    return plugin


class PresenceTransitionTests(unittest.TestCase):
    def test_first_observation_returns_none(self):
        self.assertIsNone(presence_transition(None, {"personastate": 1}))

    def test_offline_to_online(self):
        self.assertEqual("online", presence_transition({"personastate": 0}, {"personastate": 1}).kind)

    def test_online_to_offline(self):
        self.assertEqual("offline", presence_transition({"personastate": 1}, {"personastate": 0}).kind)

    def test_busy_and_away_count_as_online(self):
        self.assertIsNone(presence_transition({"personastate": 1}, {"personastate": 3}))

    def test_invisible_while_gaming_is_not_offline(self):
        self.assertIsNone(presence_transition({"personastate": 1}, {"personastate": 0, "gameid": "730"}))

    def test_invisible_gaming_then_online_is_no_change(self):
        self.assertIsNone(presence_transition({"personastate": 0, "gameid": "730"}, {"personastate": 1}))

    def test_legacy_baseline_without_personastate_is_unknown(self):
        self.assertIsNone(presence_transition({"gameid": None, "name": "P"}, {"personastate": 1}))

    def test_current_without_presence_fields_is_unknown(self):
        self.assertIsNone(presence_transition({"personastate": 1}, {}))


class PresenceNotifyTests(unittest.IsolatedAsyncioTestCase):
    async def _check(self, status, baseline, **kwargs):
        plugin = FakePlugin(status)
        plugin.config.update(kwargs.pop("config", {}))
        if baseline is not None:
            plugin.group_last_states["g1"]["s1"] = baseline
        with patch(CHECK_TIME, return_value=1700):
            await plugin.check_status_change("g1", single_sid="s1", status_override=status, **kwargs)
        return plugin

    async def test_online_transition_enqueues_notification(self):
        plugin = await self._check({"personastate": 1, "name": "P"}, {"personastate": 0})
        pending = plugin._pending_end_notifications["g1"]
        self.assertEqual(1, len(pending))
        self.assertEqual("online", pending[0]["type"])
        self.assertEqual("s1", pending[0]["sid"])
        self.assertEqual("P", pending[0]["name"])
        self.assertEqual(1700, pending[0]["event_time"])

    async def test_offline_transition_creates_debounce_pending_not_push(self):
        plugin = await self._check({"personastate": 0, "name": "P"}, {"personastate": 1})
        # 下线不即推：进 180 秒防抖待定
        self.assertNotIn("g1", plugin._pending_end_notifications)
        pending = plugin._presence_pending_offline["s1"]
        self.assertEqual(1700, pending["detected_at"])
        self.assertEqual(1700 + 180, pending["deadline"])
        self.assertIsNone(pending["online_minutes"])
        self.assertNotIn("s1", plugin._presence_online_since)

    async def test_offline_notification_carries_online_duration_after_debounce(self):
        plugin = FakePlugin({"personastate": 1, "name": "P"})
        plugin.group_last_states["g1"]["s1"] = {"personastate": 0}
        with patch(CHECK_TIME, return_value=1000):
            await plugin.check_status_change("g1", single_sid="s1", status_override={"personastate": 1, "name": "P"})
        self.assertEqual(1000, plugin._presence_online_since["s1"])
        plugin._pending_end_notifications.clear()  # 模拟 flush：合法的「已上线」已发出
        with patch(CHECK_TIME, return_value=2200):
            await plugin.check_status_change("g1", single_sid="s1", status_override={"personastate": 0, "name": "P"})
        self.assertNotIn("g1", plugin._pending_end_notifications)
        self.assertAlmostEqual(20.0, plugin._presence_pending_offline["s1"]["online_minutes"])
        # 180 秒窗口结束仍离线 → 结算推送（时长用检测时刻算好的值）
        plugin._settle_pending_offline_presence(2200 + 180)
        offline = next(n for n in plugin._pending_end_notifications["g1"] if n["type"] == "offline")
        self.assertAlmostEqual(20.0, offline["online_minutes"])
        self.assertEqual(2200, offline["event_time"])
        self.assertNotIn("s1", plugin._presence_pending_offline)
        self.assertNotIn("s1", plugin._presence_online_since)

    async def test_recheck_within_window_keeps_pending_and_clamps_poll(self):
        plugin = FakePlugin({"personastate": 0, "name": "P"})
        plugin.group_last_states["g1"]["s1"] = {"personastate": 1}
        with patch(CHECK_TIME, return_value=1000):
            await plugin.check_status_change("g1", single_sid="s1", status_override={"personastate": 0, "name": "P"})
        # 复查仍离线：待定保留，且下次轮询被钳到 60 秒内
        with patch(CHECK_TIME, return_value=1100):
            await plugin.check_status_change("g1", single_sid="s1", status_override={"personastate": 0, "name": "P"})
        self.assertIn("s1", plugin._presence_pending_offline)
        self.assertLessEqual(plugin.next_poll_time["g1"]["s1"], 1100 + 60)
        self.assertNotIn("g1", plugin._pending_end_notifications)

    async def test_online_return_within_window_cancels_pending(self):
        plugin = FakePlugin({"personastate": 1, "name": "P"})
        plugin.group_last_states["g1"]["s1"] = {"personastate": 0}
        with patch(CHECK_TIME, return_value=1000):
            await plugin.check_status_change("g1", single_sid="s1", status_override={"personastate": 1, "name": "P"})
        plugin._pending_end_notifications.clear()  # 模拟 flush：合法的「已上线」已发出
        with patch(CHECK_TIME, return_value=1100):
            await plugin.check_status_change("g1", single_sid="s1", status_override={"personastate": 0, "name": "P"})
        # 窗口内回归上线：取消待定，不推离线也不推上线，在线计时从原始起算点连续
        with patch(CHECK_TIME, return_value=1200):
            await plugin.check_status_change("g1", single_sid="s1", status_override={"personastate": 1, "name": "P"})
        self.assertNotIn("s1", plugin._presence_pending_offline)
        self.assertNotIn("g1", plugin._pending_end_notifications)
        self.assertEqual(1000, plugin._presence_online_since["s1"])

    async def test_settle_before_deadline_does_not_push(self):
        plugin = await self._check({"personastate": 0, "name": "P"}, {"personastate": 1})
        plugin._settle_pending_offline_presence(1700 + 179)
        self.assertNotIn("g1", plugin._pending_end_notifications)
        self.assertIn("s1", plugin._presence_pending_offline)

    async def test_settle_enqueues_for_every_monitoring_group(self):
        plugin = FakePlugin({"personastate": 0, "name": "P"})
        plugin.group_steam_ids = {"g1": ["s1"], "g2": ["s1"]}
        plugin.group_last_states = {"g1": {"s1": {"personastate": 1}}, "g2": {"s1": {"personastate": 1}}}
        with patch(CHECK_TIME, return_value=1700):
            await plugin.check_status_change("g1", single_sid="s1", status_override={"personastate": 0, "name": "P"})
        with patch(CHECK_TIME, return_value=1700):
            await plugin.check_status_change("g2", single_sid="s1", status_override={"personastate": 0, "name": "P"})
        plugin.group_monitor_enabled = {"g1": True, "g2": False}
        plugin._settle_pending_offline_presence(1700 + 180)
        self.assertEqual(["offline"], [n["type"] for n in plugin._pending_end_notifications["g1"]])
        self.assertNotIn("g2", plugin._pending_end_notifications)

    async def test_settle_respects_toggled_off_switch(self):
        plugin = await self._check({"personastate": 0, "name": "P"}, {"personastate": 1})
        plugin.config["enable_user_offline_notify"] = False
        plugin._settle_pending_offline_presence(1700 + 180)
        self.assertNotIn("g1", plugin._pending_end_notifications)
        self.assertNotIn("s1", plugin._presence_pending_offline)

    async def test_same_online_state_backfills_online_since(self):
        plugin = await self._check({"personastate": 1, "name": "P"}, {"personastate": 3})
        self.assertEqual(1700, plugin._presence_online_since["s1"])
        self.assertNotIn("g1", plugin._pending_end_notifications)

    async def test_first_observation_builds_baseline_without_push(self):
        plugin = await self._check({"personastate": 1, "name": "P"}, None)
        self.assertNotIn("g1", plugin._pending_end_notifications)
        self.assertEqual(1, plugin.group_last_states["g1"]["s1"]["personastate"])

    async def test_same_presence_state_does_not_push(self):
        plugin = await self._check({"personastate": 1, "name": "P"}, {"personastate": 3})
        self.assertNotIn("g1", plugin._pending_end_notifications)

    async def test_skip_push_updates_baseline_only(self):
        plugin = await self._check({"personastate": 1, "name": "P"}, {"personastate": 0}, skip_push=True)
        self.assertNotIn("g1", plugin._pending_end_notifications)
        self.assertEqual(1, plugin.group_last_states["g1"]["s1"]["personastate"])
        # skip_push 只抑制推送，在线起始时间仍要记账（后续离线推送的时长依赖它）
        self.assertEqual(1700, plugin._presence_online_since["s1"])

    async def test_skip_push_offline_transition_skips_debounce_too(self):
        plugin = await self._check({"personastate": 0, "name": "P"}, {"personastate": 1}, skip_push=True)
        # 启动基线/陈旧群：不建防抖待定（避免重放停机期间历史与无谓复查）
        self.assertNotIn("g1", plugin._pending_end_notifications)
        self.assertNotIn("s1", plugin._presence_pending_offline)

    async def test_game_start_same_round_suppresses_online(self):
        status = {"gameid": "730", "personastate": 1, "name": "P", "gameextrainfo": "CS2"}
        plugin = await self._check(status, {"personastate": 0})
        self.assertEqual(["start"], [notification["type"] for notification in plugin._pending_end_notifications["g1"]])

    async def test_offline_toggle_disabled(self):
        plugin = await self._check(
            {"personastate": 0, "name": "P"},
            {"personastate": 1},
            config={"enable_user_offline_notify": False},
        )
        # 开关关闭：既不推送也不建防抖待定
        self.assertNotIn("g1", plugin._pending_end_notifications)
        self.assertNotIn("s1", plugin._presence_pending_offline)


class PresenceSendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = _build_send_plugin()

    async def test_presence_lines_sent_as_merged_text(self):
        failed = await self.plugin._send_merged_notification(
            "g1",
            [
                {"type": "online", "sid": "s1", "name": "小明", "event_time": 1000, "online_minutes": None},
                {"type": "offline", "sid": "s2", "name": "小红", "event_time": 1000, "online_minutes": 133.0},
            ],
        )
        self.assertEqual([], failed)
        self.assertEqual(1, len(self.plugin.sent))
        _, chain = self.plugin.sent[0]
        texts = [component.text for component in chain.chain]
        self.assertIn("🔵【小明】已上线\n", texts)
        offline_line = next(text for text in texts if "已离线" in text)
        self.assertIn("小红", offline_line)
        self.assertIn("本次在线 2.2小时", offline_line)

    async def test_offline_without_duration_has_no_suffix(self):
        failed = await self.plugin._send_merged_notification(
            "g1",
            [{"type": "offline", "sid": "s3", "name": "小刚", "event_time": 1000, "online_minutes": None}],
        )
        self.assertEqual([], failed)
        _, chain = self.plugin.sent[0]
        self.assertEqual(["🌑 小刚 已离线\n"], [component.text for component in chain.chain])

    async def test_presence_event_key_distinguishes_transitions(self):
        first = self.plugin._notification_event_key({"type": "offline", "sid": "s1", "event_time": 1000}, "sess")
        second = self.plugin._notification_event_key({"type": "offline", "sid": "s1", "event_time": 2000}, "sess")
        self.assertNotEqual(first, second)

    async def test_render_notification_image_skips_presence_types(self):
        methods = _load_notification_methods({"_render_notification_image"})
        plugin_type = type(
            "RenderFakePlugin", (), {"_render_notification_image": methods["_render_notification_image"]}
        )
        plugin = plugin_type()
        self.assertIsNone(await plugin._render_notification_image({"type": "online"}))
        self.assertIsNone(await plugin._render_notification_image({"type": "offline"}))


if __name__ == "__main__":
    unittest.main()
