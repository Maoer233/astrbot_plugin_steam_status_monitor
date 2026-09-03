import unittest
from unittest.mock import patch

from src.application.services.session_quit import SessionQuitMixin
from src.application.services.status_change_tracking import StatusChangeTrackingMixin


class FakePlugin(SessionQuitMixin, StatusChangeTrackingMixin):
    def __init__(self, status):
        self.status = status
        self.config = {
            "enable_game_end_notify": True,
            "enable_game_start_notify": True,
            "enable_achievement_poll": False,
        }
        self.group_steam_ids = {"g1": ["s1"]}
        self.group_last_states = {"g1": {}}
        self.group_last_quit_times = {}
        self.group_recent_games = {}
        self.playing_sessions = {}
        self._session_meta = {}
        self._pending_end_notifications = {}
        self.achievement_poll_tasks = {}
        self.achievement_snapshots = {}
        self.achievement_monitor = None
        self.group_monitor_enabled = {}
        self.next_poll_time = {}
        self.fixed_poll_interval = 0
        self.smart_poll_intervals = [1, 3, 5, 10, 20, 30]
        self._startup_stale_groups = {}
        self._data_dirty = False
        self.playtime = []
        self.sessions = []

    def _record_playtime(self, sid, gameid, game_name, duration_min):
        self.playtime.append((sid, gameid, game_name, duration_min))

    def _record_session(self, **kwargs):
        self.sessions.append(kwargs)

    def _end_game_tip(self, duration_min):
        return "tip"

    def _save_persistent_data(self):
        return None

    async def fetch_player_status(self, sid):
        return self.status

    async def get_chinese_game_name(self, gameid, fallback):
        return fallback or "未知游戏"

    def _resolve_bind_name(self, sid, fallback):
        return fallback


class StatusChangeQuitTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_closes_previous_game_without_pending(self):
        plugin = FakePlugin({"gameid": "B", "name": "P", "gameextrainfo": "GameB"})
        await plugin.session_service.handle("g1", "s1", "A", 1000, player_name="P", current_game_name="GameA")
        plugin.group_last_states["g1"]["s1"] = {"gameid": "A", "name": "P", "gameextrainfo": "GameA"}
        with patch("src.application.services.status_change_tracking.time.time", return_value=1600):
            await plugin.check_status_change("g1", single_sid="s1")
        self.assertEqual([("s1", "A", "GameA", 10.0)], plugin.playtime)
        self.assertEqual("B", plugin.session_service.get("g1", "s1").gameid)

    async def test_exit_enters_confirming_exit_without_delayed_task(self):
        plugin = FakePlugin({"gameid": None, "name": "P"})
        await plugin.session_service.handle("g1", "s1", "A", 1000, player_name="P", current_game_name="GameA")
        plugin.group_last_states["g1"]["s1"] = {"gameid": "A", "name": "P", "gameextrainfo": "GameA"}
        with patch("src.application.services.status_change_tracking.time.time", return_value=1100):
            await plugin.check_status_change("g1", single_sid="s1")
        self.assertEqual([], plugin.playtime)
        self.assertEqual("confirming_exit", plugin.session_service.get("g1", "s1").state)


if __name__ == "__main__":
    unittest.main()
