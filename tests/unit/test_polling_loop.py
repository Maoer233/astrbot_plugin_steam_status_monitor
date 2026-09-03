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
