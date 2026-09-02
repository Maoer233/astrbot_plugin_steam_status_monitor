import unittest
from unittest.mock import patch

from src.application.services.session_quit import SessionQuitMixin
from src.application.services.status_change_tracking import StatusChangeTrackingMixin
from src.domain.monitoring.session import PlayingSession


class FakePlugin(SessionQuitMixin, StatusChangeTrackingMixin):
    def __init__(self):
        self.config = {
            "enable_game_end_notify": True,
            "enable_game_start_notify": True,
            "enable_achievement_poll": False,
            "enable_network_fluctuation_notify": False,
        }
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
        return None

    async def get_chinese_game_name(self, gameid, fallback):
        return fallback or "未知游戏"

    def _resolve_bind_name(self, sid, fallback):
        return fallback


class SessionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_records_previous_session_immediately(self):
        plugin = FakePlugin()
        service = plugin.session_service
        await service.handle("g1", "s1", "A", 1000, player_name="P", current_game_name="GameA")
        await service.handle("g1", "s1", "B", 1600, player_name="P", current_game_name="GameB")

        self.assertEqual([("s1", "A", "GameA", 10.0)], plugin.playtime)
        self.assertEqual("B", service.get("g1", "s1").gameid)
        self.assertEqual(1600, service.started_at("g1", "s1", "B"))
        self.assertIsNone(service.started_at("g1", "s1", "A"))
        kinds = [item["type"] for item in plugin._pending_end_notifications["g1"]]
        self.assertEqual(["start", "end", "start"], kinds)

    async def test_exit_then_resume_does_not_record(self):
        plugin = FakePlugin()
        service = plugin.session_service
        await service.handle("g1", "s1", "A", 1000, player_name="P", current_game_name="GameA")
        await service.handle("g1", "s1", None, 1100, player_name="P")
        self.assertEqual("confirming_exit", service.get("g1", "s1").state)
        await service.handle("g1", "s1", "A", 1200, player_name="P", current_game_name="GameA")

        self.assertEqual([], plugin.playtime)
        self.assertEqual("playing", service.get("g1", "s1").state)
        self.assertEqual(1000, service.started_at("g1", "s1", "A"))

    async def test_tick_due_closes_expired_exit(self):
        plugin = FakePlugin()
        service = plugin.session_service
        await service.handle("g1", "s1", "A", 1000, player_name="P", current_game_name="GameA")
        await service.handle("g1", "s1", None, 1100, player_name="P")
        service.tick_due(1279)
        self.assertEqual([], plugin.playtime)
        service.tick_due(1280)
        self.assertEqual([("s1", "A", "GameA", 100 / 60)], plugin.playtime)
        self.assertIsNone(service.get("g1", "s1"))

    async def test_check_status_change_no_longer_creates_delayed_task(self):
        plugin = FakePlugin()
        plugin.group_steam_ids = {"g1": ["s1"]}
        plugin.group_last_states = {"g1": {"s1": {"gameid": "A", "name": "P", "gameextrainfo": "GameA"}}}
        await plugin.session_service.handle("g1", "s1", "A", 1000, player_name="P", current_game_name="GameA")

        async def fetch(_sid):
            return {"gameid": None, "name": "P"}

        plugin.fetch_player_status = fetch
        with patch("src.application.services.status_change_tracking.time.time", return_value=1100):
            await plugin.check_status_change("g1", single_sid="s1")
        self.assertEqual("confirming_exit", plugin.session_service.get("g1", "s1").state)

    def test_hydrate_legacy_pending_quit(self):
        plugin = FakePlugin()
        plugin.session_service.hydrate_from_legacy(
            pending_all={
                "g1": {
                    "s1": {
                        "A": {
                            "quit_time": 1100,
                            "start_time": 1000,
                            "name": "P",
                            "game_name": "GameA",
                            "notified": False,
                        }
                    }
                }
            }
        )
        session = plugin.session_service.get("g1", "s1")
        self.assertEqual("confirming_exit", session.state)
        self.assertEqual(1280, session.exit_deadline)

    def test_hydrate_legacy_start_play_times(self):
        plugin = FakePlugin()
        plugin.group_last_states = {"g1": {"s1": {"gameid": "A", "name": "P", "gameextrainfo": "GameA"}}}
        plugin.session_service.hydrate_from_legacy(
            start_all={"g1": {"s1": {"A": 1000}}},
            last_all=plugin.group_last_states,
        )
        session = plugin.session_service.get("g1", "s1")
        self.assertEqual("playing", session.state)
        self.assertEqual(1000, session.started_at)

    def test_dump_and_load_roundtrip(self):
        plugin = FakePlugin()
        plugin.playing_sessions[("g1", "s1")] = PlayingSession(
            sid="s1", gameid="A", started_at=1000, state="playing", group_id="g1"
        )
        plugin._session_meta[("g1", "s1")] = {
            "player_name": "P",
            "game_name": "GameA",
            "avatar_url": "http://a",
        }
        payload = plugin.session_service.dump()
        other = FakePlugin()
        other.session_service.load(payload)
        loaded = other.session_service.get("g1", "s1")
        self.assertEqual("playing", loaded.state)
        self.assertEqual(1000, loaded.started_at)
        self.assertEqual("P", other._session_meta[("g1", "s1")]["player_name"])


if __name__ == "__main__":
    unittest.main()
