import asyncio
import unittest
from datetime import datetime

from src.presentation.web.response_cache import AsyncTTLCache
from src.presentation.web.statistics import (
    build_dashboard_stats,
    build_groups,
    build_heatmap_data,
    build_player_search_index,
    build_report_window,
)


class FixturePlugin:
    def __init__(self):
        self.group_steam_ids = {"group-1": ["sid-1", "sid-2"]}
        self.group_last_states = {
            "group-1": {
                "sid-1": {"name": "Steam One", "gameid": "730", "gameextrainfo": "CS2", "personastate": 1},
                "sid-2": {"name": "Steam Two", "gameid": "", "personastate": 0},
            }
        }
        self._bind_data = {"qq-1": {"sid": "sid-1", "nickname": "Bound One"}}
        self.play_records = {
            "2026-08-21": {
                "sid-1": {"730": {"name": "CS2", "minutes": 60}}
            }
        }
        start = int(datetime(2026, 8, 20, 18).timestamp())
        self.session_records = {
            "sid-1": [{"date": "2026-08-20", "duration_min": 90, "start_time": start}],
            "sid-2": [],
        }


class AsyncTTLCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_requests_share_one_computation(self):
        cache = AsyncTTLCache()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"value": 1}

        values = await asyncio.gather(*[
            cache.get_or_create(("stats",), 10, factory) for _ in range(20)
        ])
        await asyncio.sleep(0)

        self.assertEqual(calls, 1)
        self.assertTrue(all(value == {"value": 1} for value in values))
        self.assertEqual(
            await cache.get_or_create(("stats",), 10, factory), {"value": 1}
        )
        self.assertEqual(calls, 1)

    async def test_invalidate_removes_selected_cache(self):
        cache = AsyncTTLCache()
        calls = 0

        async def factory():
            nonlocal calls
            calls += 1
            return calls

        self.assertEqual(await cache.get_or_create(("groups",), 10, factory), 1)
        await asyncio.sleep(0)
        cache.invalidate("groups")
        self.assertEqual(await cache.get_or_create(("groups",), 10, factory), 2)


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FixturePlugin()

    def test_dashboard_and_groups_keep_expected_shape(self):
        dashboard = build_dashboard_stats(
            self.plugin, "2026-08-21", "2026-08-21 12:00"
        )
        groups = build_groups(self.plugin)

        self.assertEqual(dashboard["total_players"], 2)
        self.assertEqual(dashboard["online_players"], 1)
        self.assertEqual(dashboard["top_players_today"][0]["name"], "Bound One")
        self.assertEqual(groups["group-1"][0]["name"], "Bound One")

    def test_search_index_uses_bound_display_name(self):
        index = build_player_search_index(self.plugin)
        self.assertEqual(index[0]["name"], "Bound One")
        self.assertEqual(index[0]["group_id"], "group-1")

    def test_heatmap_combines_session_and_play_records(self):
        result = build_heatmap_data(
            self.plugin, 3, datetime(2026, 8, 21, 12)
        )

        self.assertEqual(result["heatmap_data"]["2026-08-20"], 90)
        self.assertEqual(result["heatmap_data"]["2026-08-21"], 60)
        self.assertEqual(result["players"][0]["total_minutes"], 150)
        self.assertEqual(result["players"][0]["name"], "Bound One")
        contributor = result["daily_contributors"]["2026-08-21"][0]
        self.assertEqual(contributor["sid"], "sid-1")
        self.assertEqual(contributor["games"][0]["name"], "CS2")
        self.assertEqual(result["groups"], [{"id": "group-1", "player_count": 2}])

    def test_heatmap_filters_players_by_group(self):
        self.plugin.group_steam_ids["group-2"] = ["sid-2"]
        self.plugin.play_records["2026-08-21"]["sid-2"] = {
            "570": {"name": "Dota 2", "minutes": 45}
        }

        result = build_heatmap_data(
            self.plugin, 3, datetime(2026, 8, 21, 12), "group-2"
        )

        self.assertEqual(result["selected_group"], "group-2")
        self.assertEqual(result["heatmap_data"]["2026-08-21"], 45)
        self.assertEqual(
            [item["sid"] for item in result["daily_contributors"]["2026-08-21"]],
            ["sid-2"],
        )

    def test_heatmap_does_not_double_count_same_player_day(self):
        self.plugin.session_records["sid-1"].append({
            "date": "2026-08-21",
            "duration_min": 30,
            "gameid": "730",
            "game_name": "CS2",
        })

        result = build_heatmap_data(
            self.plugin, 3, datetime(2026, 8, 21, 12)
        )

        self.assertEqual(result["heatmap_data"]["2026-08-21"], 30)

    def test_report_window_uses_four_am_boundary(self):
        before_boundary = build_report_window(datetime(2026, 8, 29, 3, 30), 1, 0)
        after_boundary = build_report_window(datetime(2026, 8, 29, 4, 30), 1, 0)

        self.assertEqual(
            (before_boundary[0].strftime("%Y-%m-%d %H:%M"), before_boundary[1].strftime("%Y-%m-%d %H:%M")),
            ("2026-08-28 04:00", "2026-08-29 04:00"),
        )
        self.assertEqual(
            (after_boundary[0].strftime("%Y-%m-%d %H:%M"), after_boundary[1].strftime("%Y-%m-%d %H:%M")),
            ("2026-08-29 04:00", "2026-08-30 04:00"),
        )


if __name__ == "__main__":
    unittest.main()
