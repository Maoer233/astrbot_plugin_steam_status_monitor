import unittest
from unittest.mock import MagicMock

from src.application.services.session_quit import SessionQuitMixin


class _QuitHarness(SessionQuitMixin):
    def __init__(self):
        self.config = {"enable_game_end_notify": True}
        self.group_pending_quit = {}
        self.group_start_play_times = {}
        self.group_last_states = {}
        self._pending_quit_tasks = {}
        self._pending_end_notifications = {}
        self.achievement_poll_tasks = {}
        self.achievement_snapshots = {}
        self.achievement_monitor = MagicMock()
        self.session_records = {}
        self.playtime_calls = []

    def _record_playtime(self, sid, gameid, game_name, duration_min):
        self.playtime_calls.append((sid, gameid, game_name, duration_min))

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
            "duration_min": duration_min,
        })


def _pending(name="Player", game_name="Game A", start_time=1000, quit_time=1600, duration_min=10):
    return {
        "quit_time": quit_time,
        "name": name,
        "game_name": game_name,
        "duration_min": duration_min,
        "start_time": start_time,
        "notified": False,
    }


class ConfirmQuitImmediatelyTests(unittest.TestCase):
    def setUp(self):
        self.plugin = _QuitHarness()
        self.plugin.group_pending_quit = {
            "g1": {"sid": {"730": _pending()}}
        }
        self.plugin.group_start_play_times = {
            "g1": {"sid": {"730": 1000, "570": 1600}}
        }

    def test_confirm_records_once_and_pops_projection(self):
        ok = self.plugin._confirm_quit_immediately("g1", "sid", "730")
        self.assertTrue(ok)
        self.assertEqual(len(self.plugin.playtime_calls), 1)
        self.assertEqual(self.plugin.playtime_calls[0][1], "730")
        self.assertEqual(len(self.plugin.session_records["sid"]), 1)
        self.assertNotIn("730", self.plugin.group_pending_quit["g1"]["sid"])
        self.assertNotIn("730", self.plugin.group_start_play_times["g1"]["sid"])
        self.assertIn("570", self.plugin.group_start_play_times["g1"]["sid"])
        self.assertEqual(len(self.plugin._pending_end_notifications["g1"]), 1)
        self.assertEqual(self.plugin._pending_end_notifications["g1"][0]["type"], "end")

    def test_confirm_is_idempotent(self):
        self.plugin._confirm_quit_immediately("g1", "sid", "730")
        self.assertFalse(self.plugin._confirm_quit_immediately("g1", "sid", "730"))
        self.assertEqual(len(self.plugin.playtime_calls), 1)
        self.assertEqual(len(self.plugin.session_records["sid"]), 1)
        self.assertEqual(len(self.plugin._pending_end_notifications["g1"]), 1)

    def test_missing_or_notified_pending_is_noop(self):
        self.assertFalse(self.plugin._confirm_quit_immediately("g1", "sid", "missing"))
        self.plugin.group_pending_quit["g1"]["sid"]["730"]["notified"] = True
        self.assertFalse(self.plugin._confirm_quit_immediately("g1", "sid", "730"))
        self.assertEqual(self.plugin.playtime_calls, [])


if __name__ == "__main__":
    unittest.main()
