import logging
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

if "astrbot.api" not in sys.modules:
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event = types.ModuleType("astrbot.api.event")
    components = types.ModuleType("astrbot.api.message_components")

    class MessageChain:
        def __init__(self, items):
            self.items = items

    class Plain:
        def __init__(self, text):
            self.text = text

    event.MessageChain = MessageChain
    components.Plain = Plain
    api.logger = logging.getLogger("steam_status_monitor_test")
    api.event = event
    api.message_components = components
    astrbot.api = api
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event
    sys.modules["astrbot.api.message_components"] = components

from src.application.services.session_quit import SessionQuitMixin
from src.application.services.status_change_tracking import StatusChangeTrackingMixin


class _StatusHarness(StatusChangeTrackingMixin, SessionQuitMixin):
    def __init__(self):
        self.config = {
            "enable_game_end_notify": True,
            "enable_game_start_notify": True,
            "enable_network_fluctuation_notify": True,
            "enable_achievement_poll": False,
        }
        self.group_steam_ids = {"g1": ["sid"]}
        self.group_last_states = {}
        self.group_start_play_times = {}
        self.group_last_quit_times = {}
        self.group_pending_logs = {}
        self.group_pending_quit = {}
        self.group_recent_games = {}
        self.notify_sessions = {}
        self.push_groups = {}
        self._startup_stale_groups = {}
        self._pending_quit_tasks = {}
        self._pending_end_notifications = {}
        self.next_poll_time = {}
        self.fixed_poll_interval = 0
        self.smart_poll_intervals = [1, 3, 5, 10, 20, 30]
        self.achievement_poll_tasks = {}
        self.achievement_snapshots = {}
        self.achievement_monitor = MagicMock()
        self.achievement_monitor.clear_game_achievements = MagicMock()
        self.achievement_delayed_final_check = AsyncMock()
        self.session_records = {}
        self.playtime_calls = []
        self.context = MagicMock()
        self.context.send_message = AsyncMock()

    def _record_playtime(self, sid, gameid, game_name, duration_min):
        self.playtime_calls.append((sid, gameid, duration_min))

    def _record_session(self, sid, gameid, game_name, start_time, end_time, duration_min, group_id):
        if duration_min <= 0 or not gameid:
            return
        session_id = f"2026-09-02_{int(start_time)}_{gameid}"
        sessions = self.session_records.setdefault(str(sid), [])
        if any(item.get("session_id") == session_id for item in sessions):
            return
        sessions.append({
            "session_id": session_id,
            "gameid": str(gameid),
            "group_id": str(group_id),
        })

    def _resolve_bind_name(self, sid, steam_name=None):
        return steam_name or sid

    def _should_skip_game(self, gameid):
        return False

    def _save_persistent_data(self, force=False):
        return None

    async def fetch_player_status(self, sid):
        raise AssertionError("status_override should be used")

    async def get_chinese_game_name(self, gameid, game=None):
        return {"730": "Game A", "570": "Game B"}.get(gameid, game or "未知游戏")


def _status(gameid, name="Player"):
    return {
        "name": name,
        "gameid": gameid,
        "gameextrainfo": {"730": "Game A", "570": "Game B"}.get(gameid, ""),
        "personastate": 1 if gameid else 0,
        "lastlogoff": None,
        "avatarfull": "http://avatar",
    }


class StatusChangeQuitTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin = _StatusHarness()
        self.plugin.group_last_states = {"g1": {"sid": _status("730")}}
        self.plugin.group_start_play_times = {"g1": {"sid": {"730": 1000}}}

    async def test_switch_confirms_previous_game_immediately(self):
        with patch("src.application.services.status_change_tracking.time.time", return_value=1600):
            await self.plugin.check_status_change(
                "g1",
                single_sid="sid",
                status_override=_status("570"),
            )
        self.assertEqual([call[1] for call in self.plugin.playtime_calls], ["730"])
        self.assertEqual(len(self.plugin.session_records["sid"]), 1)
        self.assertEqual(self.plugin.session_records["sid"][0]["gameid"], "730")
        self.assertNotIn("730", self.plugin.group_pending_quit.get("g1", {}).get("sid", {}))
        self.assertNotIn("730", self.plugin.group_start_play_times["g1"]["sid"])
        self.assertEqual(self.plugin.group_start_play_times["g1"]["sid"]["570"], 1600)
        self.assertEqual(self.plugin._pending_quit_tasks, {})
        types = [item["type"] for item in self.plugin._pending_end_notifications["g1"]]
        self.assertEqual(types.count("end"), 1)
        self.assertEqual(types.count("start"), 1)

    async def test_exit_waits_until_timeout_then_records_once(self):
        with patch("src.application.services.status_change_tracking.time.time", return_value=1600):
            await self.plugin.check_status_change(
                "g1",
                single_sid="sid",
                status_override=_status(None),
                skip_push=True,
            )
        self.assertEqual(self.plugin.playtime_calls, [])
        self.assertIn("730", self.plugin.group_pending_quit["g1"]["sid"])
        self.assertEqual(self.plugin.group_start_play_times["g1"]["sid"]["730"], 1000)

        with patch("src.application.services.status_change_tracking.time.time", return_value=1780):
            await self.plugin.check_status_change(
                "g1",
                single_sid="sid",
                status_override=_status(None),
                skip_push=True,
            )
        self.assertEqual([call[1] for call in self.plugin.playtime_calls], ["730"])
        self.assertNotIn("730", self.plugin.group_pending_quit["g1"]["sid"])
        self.assertNotIn("730", self.plugin.group_start_play_times["g1"]["sid"])

        with patch("src.application.services.status_change_tracking.time.time", return_value=1780):
            await self.plugin.check_status_change(
                "g1",
                single_sid="sid",
                status_override=_status(None),
                skip_push=True,
            )
        self.assertEqual(len(self.plugin.playtime_calls), 1)

    async def test_exit_then_same_game_within_window_is_fluctuation(self):
        with patch("src.application.services.status_change_tracking.time.time", return_value=1600):
            await self.plugin.check_status_change(
                "g1",
                single_sid="sid",
                status_override=_status(None),
                skip_push=True,
            )
        with patch("src.application.services.status_change_tracking.time.time", return_value=1630):
            await self.plugin.check_status_change(
                "g1",
                single_sid="sid",
                status_override=_status("730"),
                skip_push=True,
            )
        self.assertEqual(self.plugin.playtime_calls, [])
        self.assertNotIn("730", self.plugin.group_pending_quit.get("g1", {}).get("sid", {}))
        self.assertEqual(self.plugin._pending_end_notifications, {})

    async def test_confirming_exit_then_other_game_closes_immediately(self):
        with patch("src.application.services.status_change_tracking.time.time", return_value=1600):
            await self.plugin.check_status_change(
                "g1",
                single_sid="sid",
                status_override=_status(None),
                skip_push=True,
            )
        with patch("src.application.services.status_change_tracking.time.time", return_value=1630):
            await self.plugin.check_status_change(
                "g1",
                single_sid="sid",
                status_override=_status("570"),
                skip_push=True,
            )
        self.assertEqual([call[1] for call in self.plugin.playtime_calls], ["730"])
        self.assertNotIn("730", self.plugin.group_pending_quit.get("g1", {}).get("sid", {}))
        self.assertEqual(self.plugin.group_start_play_times["g1"]["sid"]["570"], 1630)


if __name__ == "__main__":
    unittest.main()
