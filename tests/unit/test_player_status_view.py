import unittest

from src.application.services.player_status_view import (
    PlayerStatusViewService,
    format_alllist_text,
    sort_rows_for_image,
)


class _SessionService:
    def started_at(self, group_id, sid, gameid):
        if group_id == "primary" and sid == "sid-primary" and str(gameid) == "730":
            return 100
        return None


class ViewPlugin:
    def __init__(self):
        self.group_steam_ids = {"primary": ["sid-primary"], "push": []}
        self.push_groups = {"sid-primary": ["push"]}
        self.next_poll_time = {"primary": {"sid-primary": 130}}
        self.session_service = _SessionService()

    async def fetch_player_statuses_batch(self, steam_ids):
        return {
            sid: {
                "name": "Player",
                "gameid": "730",
                "gameextrainfo": "Counter-Strike 2",
                "avatarfull": "",
                "personastate": 1,
            }
            for sid in steam_ids
        }

    async def get_chinese_game_name(self, gameid, game):
        return game

    def _resolve_bind_name(self, sid, name):
        return name


class PlayerStatusViewTests(unittest.IsolatedAsyncioTestCase):
    def test_group_ids_include_push_targets(self):
        view = PlayerStatusViewService(ViewPlugin())
        direct, push, combined = view.steam_ids_for_group("push")
        self.assertEqual([], direct)
        self.assertEqual(["sid-primary"], push)
        self.assertEqual(["sid-primary"], combined)
        self.assertEqual("primary", view.primary_group_of("sid-primary", "push"))

    async def test_push_group_rows_read_primary_session(self):
        view = PlayerStatusViewService(ViewPlugin())
        rows = await view.build_group_rows("push", now=160)
        self.assertEqual(1, len(rows))
        self.assertEqual("playing", rows[0]["status"])
        self.assertEqual("1.0分钟", rows[0]["play_str"])

    async def test_all_rows_include_poll_and_group(self):
        view = PlayerStatusViewService(ViewPlugin())
        rows = await view.build_all_rows(now=100)
        self.assertEqual(["primary"], [row["group_id"] for row in rows])
        self.assertEqual("下次轮询30秒后", rows[0]["poll_str"])

    def test_alllist_text_and_image_sort(self):
        rows = [
            {"sid": "s2", "name": "B", "status": "offline", "game": "", "play_str": "", "group_id": "g1"},
            {"sid": "s1", "name": "A", "status": "playing", "game": "Game", "play_str": "10.0分钟", "group_id": "g1"},
        ]
        ordered = sort_rows_for_image(rows)
        self.assertEqual(["s1", "s2"], [row["sid"] for row in ordered])
        text = format_alllist_text(rows)
        self.assertIn("群: g1", text)
        self.assertIn("正在玩：Game", text)
        self.assertIn("在线: 1 / 总数: 2", text)
