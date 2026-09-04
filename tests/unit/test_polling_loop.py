import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from src.application.services.polling_tracking import PollingTrackingMixin
from src.infrastructure.persistence.plugin_data import PersistenceMixin


class FakePollingPlugin(PollingTrackingMixin):
    def __init__(self):
        self.ticks = []
        self.flushes = 0

        class _Sessions:
            def __init__(self, outer):
                self._outer = outer

            def tick_due(self, now):
                self._outer.ticks.append(now)

        self.session_service = _Sessions(self)

    async def fetch_player_statuses_batch(self, steam_ids):
        await asyncio.sleep(0.04)
        return {steam_ids[0]: {"gameid": "1"}}

    async def _flush_pending_end_notifications(self):
        self.flushes += 1


class PollingLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_keeps_ticking_while_steam_request_blocks(self):
        plugin = FakePollingPlugin()
        result = await plugin._fetch_statuses_while_ticking(["sid"], tick_interval=0.01)
        self.assertEqual(result, {"sid": {"gameid": "1"}})
        self.assertGreaterEqual(len(plugin.ticks), 1)
        self.assertGreaterEqual(plugin.flushes, 1)


class GroupSwitchPersistenceTests(unittest.TestCase):
    @staticmethod
    def _persistence_plugin(tmp):
        plugin = PersistenceMixin()
        plugin.data_dir = tmp
        plugin.group_steam_ids = {"g1": ["s1"]}
        plugin.group_last_states = {}
        plugin.group_last_quit_times = {}
        plugin.group_pending_logs = {}
        plugin.group_recent_games = {}
        plugin.playing_sessions = {}
        plugin._session_meta = {}
        return plugin

    def test_existing_session_store_prevents_reimporting_legacy_pending_quit(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "playing_sessions.json").write_text("{}", encoding="utf-8")
            Path(tmp, "group_g1_pending_quit.json").write_text(json.dumps({
                "s1": {
                    "A": {
                        "quit_time": 1100,
                        "start_time": 1000,
                        "name": "P",
                        "game_name": "GameA",
                        "notified": False,
                    }
                }
            }), encoding="utf-8")
            plugin = self._persistence_plugin(tmp)

            plugin._load_persistent_data()

            self.assertIsNone(plugin.session_service.get("g1", "s1"))

    def test_first_upgrade_migrates_legacy_sessions_and_creates_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "group_g1_pending_quit.json").write_text(json.dumps({
                "s1": {
                    "A": {
                        "quit_time": 1100,
                        "start_time": 1000,
                        "name": "P",
                        "game_name": "GameA",
                        "notified": False,
                    }
                }
            }), encoding="utf-8")
            plugin = self._persistence_plugin(tmp)

            plugin._load_persistent_data()

            self.assertEqual("confirming_exit", plugin.session_service.get("g1", "s1").state)
            self.assertTrue(Path(tmp, "playing_sessions.json").exists())

    def test_monitor_switch_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = PersistenceMixin()
            plugin.data_dir = tmp
            plugin.group_monitor_enabled = {"415912885": False}
            plugin.group_achievement_enabled = {"415912885": True}
            plugin._save_group_switches()

            payload = json.loads(Path(tmp, "group_switches.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["monitor"]["415912885"], False)

            restored = PersistenceMixin()
            restored.data_dir = tmp
            restored._load_group_switches()
            self.assertFalse(restored.group_monitor_enabled["415912885"])
            self.assertTrue(restored.group_achievement_enabled["415912885"])


if __name__ == "__main__":
    unittest.main()
