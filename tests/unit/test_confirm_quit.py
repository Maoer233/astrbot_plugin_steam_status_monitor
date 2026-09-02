import unittest

from src.application.services.session_quit import SessionQuitMixin


class FakePlugin(SessionQuitMixin):
    def __init__(self):
        self.config = {"enable_game_end_notify": True, "enable_achievement_poll": False}
        self.group_last_quit_times = {}
        self.group_last_states = {}
        self.group_recent_games = {}
        self.playing_sessions = {}
        self._session_meta = {}
        self._pending_end_notifications = {}
        self.achievement_poll_tasks = {}
        self.achievement_snapshots = {}
        self.achievement_monitor = None
        self.group_monitor_enabled = {}
        self._data_dirty = False
        self.playtime = []
        self.sessions = []

    def _record_playtime(self, sid, gameid, game_name, duration_min):
        self.playtime.append((sid, gameid, duration_min))

    def _record_session(self, **kwargs):
        self.sessions.append(kwargs)

    def _end_game_tip(self, duration_min):
        return "tip"


class ConfirmQuitTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_is_idempotent_for_same_session(self):
        plugin = FakePlugin()
        service = plugin.session_service
        await service.handle("g1", "s1", "A", 1000, player_name="P", current_game_name="GameA")
        await service.handle("g1", "s1", "B", 1600, player_name="P", current_game_name="GameB")
        first = list(plugin.playtime)
        await service.handle("g1", "s1", "B", 1700, player_name="P", current_game_name="GameB")
        self.assertEqual(first, plugin.playtime)

    async def test_missing_session_is_noop(self):
        plugin = FakePlugin()
        await plugin.session_service.handle("g1", "s1", None, 1000)
        self.assertEqual([], plugin.playtime)
        self.assertEqual([], plugin.sessions)


if __name__ == "__main__":
    unittest.main()
