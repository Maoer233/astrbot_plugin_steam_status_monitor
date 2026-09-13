import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.application.services.monitor_control import MonitorControlService


class PluginStub:
    def __init__(self):
        self.API_KEY = "KEY"
        self.group_steam_ids = {"111": ["s1"]}
        self.group_monitor_enabled = {}
        self.group_achievement_enabled = {}
        self.running_groups = set()
        self.notify_sessions = {}
        self.group_last_states = {}
        self.next_poll_time = {"111": {"s1": 1}}
        self._pending_end_notifications = {"111": [{"type": "end"}]}
        self.achievement_poll_tasks = {("111", "s1", "10"): SimpleNamespace(cancel=lambda: None)}
        self.switch_saves = 0
        self.notify_saves = 0
        self.session_service = SimpleNamespace(handle=AsyncMock())
        self.fetch_player_statuses_batch = AsyncMock(
            return_value={"s1": {"gameid": "10", "name": "玩家", "gameextrainfo": "Game"}}
        )

    def _save_group_switches(self):
        self.switch_saves += 1

    def _save_notify_session(self):
        self.notify_saves += 1


class MonitorControlServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_hydrates_sessions_with_skip_push(self):
        plugin = PluginStub()
        service = MonitorControlService(plugin)

        result = await service.start("111", notify_session="platform:GroupMessage:x_111")

        self.assertTrue(result.ok)
        self.assertEqual("started", result.code)
        self.assertIn("111", plugin.running_groups)
        self.assertTrue(plugin.group_monitor_enabled["111"])
        self.assertEqual("platform:GroupMessage:x_111", plugin.notify_sessions["111"])
        plugin.session_service.handle.assert_awaited_once()
        kwargs = plugin.session_service.handle.await_args.kwargs
        self.assertTrue(kwargs["skip_push"])

    async def test_start_rejects_private_chat_and_missing_watchlist(self):
        plugin = PluginStub()
        plugin.group_steam_ids = {}
        service = MonitorControlService(plugin)

        result = await service.start("default")
        self.assertFalse(result.ok)
        self.assertEqual("invalid_group", result.code)

        result = await service.start("111")
        self.assertFalse(result.ok)
        self.assertEqual("empty_watchlist", result.code)

    def test_stop_clears_runtime_and_cancels_achievement_tasks(self):
        plugin = PluginStub()
        plugin.running_groups.add("111")
        plugin.group_monitor_enabled["111"] = True
        cancelled = {"n": 0}

        class Task:
            def cancel(self):
                cancelled["n"] += 1

        plugin.achievement_poll_tasks[("111", "s1", "10")] = Task()
        plugin.achievement_poll_tasks[("222", "s2", "10")] = Task()
        service = MonitorControlService(plugin)

        result = service.stop("111")

        self.assertTrue(result.ok)
        self.assertEqual("stopped", result.code)
        self.assertNotIn("111", plugin.running_groups)
        self.assertFalse(plugin.group_monitor_enabled["111"])
        self.assertNotIn("111", plugin.next_poll_time)
        self.assertNotIn("111", plugin._pending_end_notifications)
        self.assertNotIn(("111", "s1", "10"), plugin.achievement_poll_tasks)
        self.assertIn(("222", "s2", "10"), plugin.achievement_poll_tasks)
        self.assertEqual(1, cancelled["n"])

    def test_ensure_running_is_idempotent(self):
        plugin = PluginStub()
        service = MonitorControlService(plugin)

        self.assertTrue(service.ensure_running("111", notify_session="sess"))
        self.assertFalse(service.ensure_running("111", notify_session="sess"))
        self.assertEqual({"111"}, plugin.running_groups)
        self.assertEqual(1, plugin.notify_saves)

    def test_set_achievement_persists_switch(self):
        plugin = PluginStub()
        service = MonitorControlService(plugin)

        on = service.set_achievement("111", True)
        off = service.set_achievement("111", False)

        self.assertEqual("achievement_on", on.code)
        self.assertEqual("achievement_off", off.code)
        self.assertFalse(plugin.group_achievement_enabled["111"])
        self.assertEqual(2, plugin.switch_saves)
