import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from src.application.services.rank_view import (
    RankViewService,
    lookup_top_game_id,
    parse_rank_period,
)
from src.application.services.ranking import RankingService


class RankViewTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_rank_period(self):
        self.assertEqual((1, "今日"), parse_rank_period(""))
        self.assertEqual((7, "最近7天"), parse_rank_period("week"))
        self.assertEqual((30, "最近30天"), parse_rank_period("month"))
        self.assertEqual((3, "最近3天"), parse_rank_period("3"))
        self.assertEqual((1, "最近1天"), parse_rank_period("0"))

    def test_lookup_top_game_id_uses_offset_day(self):
        ranking = RankingService(now=lambda: datetime(2026, 9, 12, 12, 0))
        play_records = {
            "2026-09-11": {"s1": {"730": {"name": "CS2", "minutes": 10}}},
            "2026-09-12": {"s1": {"570": {"name": "Dota", "minutes": 20}}},
        }
        self.assertEqual(
            "730",
            lookup_top_game_id(play_records, ranking, "s1", "CS2", 1, -1),
        )
        self.assertEqual(
            "570",
            lookup_top_game_id(play_records, ranking, "s1", "Dota", 1, 0),
        )

    async def test_enrich_fills_bind_name_and_top_game(self):
        ranking = RankingService(now=lambda: datetime(2026, 9, 12, 12, 0))
        plugin = SimpleNamespace(
            ranking_service=ranking,
            play_records={
                "2026-09-12": {"s1": {"730": {"name": "CS2", "minutes": 40}}},
            },
            fetch_player_statuses_batch=None,
            get_chinese_game_name=None,
            _resolve_bind_name=lambda sid, name: f"bind-{sid}",
        )

        async def fetch(sids):
            return {"s1": {"name": "SteamName", "avatarfull": "http://a"}}

        async def zh(gameid, name):
            return "反恐精英2" if str(gameid) == "730" else name

        plugin.fetch_player_statuses_batch = fetch
        plugin.get_chinese_game_name = zh
        view = RankViewService(plugin)
        rows = [
            {
                "sid": "s1",
                "games": [{"name": "CS2", "minutes": 40, "gameid": "730"}],
            }
        ]
        await view.enrich(rows, days=1, base_day_offset=0)
        self.assertEqual("bind-s1", rows[0]["name"])
        self.assertEqual("http://a", rows[0]["avatar_url"])
        self.assertEqual("730", rows[0]["top_game_id"])
        self.assertEqual("反恐精英2", rows[0]["games"][0]["name"])

    def test_configure_push_enables_group_and_global(self):
        plugin = SimpleNamespace(
            rank_push_groups=[],
            rank_push_all=False,
            saves=0,
            _save_rank_push_groups=lambda: setattr(plugin, "saves", plugin.saves + 1),
        )
        view = RankViewService(plugin)

        enabled = view.configure_push("", "111")
        self.assertEqual("已开启本群每日排行榜自动推送。", enabled.message)
        self.assertFalse(enabled.should_push)
        self.assertEqual(["111"], plugin.rank_push_groups)
        self.assertFalse(plugin.rank_push_all)

        listed = view.configure_push("list", "111")
        self.assertIn("分群", listed.message)

        global_mode = view.configure_push("all", "111")
        self.assertEqual("已开启每日排行榜自动推送（全局排行）", global_mode.message)
        self.assertTrue(plugin.rank_push_all)
        self.assertEqual(["111"], plugin.rank_push_groups)

        test_mode = view.configure_push("test", "111")
        self.assertTrue(test_mode.should_push)

        deleted = view.configure_push("del 111", "111")
        self.assertEqual("已关闭群 111 的每日排行榜推送。", deleted.message)
        self.assertEqual([], plugin.rank_push_groups)

    def test_configure_push_rejects_private_chat(self):
        plugin = SimpleNamespace(
            rank_push_groups=[],
            rank_push_all=False,
            _save_rank_push_groups=lambda: None,
        )
        result = RankViewService(plugin).configure_push("", "default")
        self.assertEqual("请在群聊中开启排行榜推送。", result.message)
        self.assertEqual([], plugin.rank_push_groups)

    async def test_push_daily_skips_empty_and_sends_once_per_scope(self):
        sent = []

        async def send_message(session, chain):
            sent.append((session, chain))

        plugin = SimpleNamespace(
            rank_push_groups=["111", "222"],
            rank_push_all=False,
            notify_sessions={
                "111": "qq:GroupMessage:0_111",
                "222": "qq:GroupMessage:0_222",
            },
            context=SimpleNamespace(send_message=send_message),
        )
        view = RankViewService(plugin)
        calls = []

        async def render_aggregated(*, days, period_label, group_id, base_day_offset):
            calls.append(group_id)
            if group_id == "111":
                return None
            return "rank.png"

        view.render_aggregated = render_aggregated
        view.send_rank_image = send_message
        with patch("src.application.services.rank_view.os.unlink"):
            await view.push_daily()
        self.assertEqual(["111", "222"], calls)
        self.assertEqual(1, len(sent))
        self.assertEqual("qq:GroupMessage:0_222", sent[0][0])
