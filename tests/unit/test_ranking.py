from datetime import datetime
from types import SimpleNamespace

from src.application.services.ranking import RankingService


def test_day_key_rolls_over_at_4am():
    ranking = RankingService(now=lambda: datetime(2026, 9, 12, 3, 59))
    assert ranking.day_key() == "2026-09-11"

    ranking = RankingService(now=lambda: datetime(2026, 9, 12, 4, 0))
    assert ranking.day_key() == "2026-09-12"
    assert ranking.day_key(-1) == "2026-09-11"


def test_record_playtime_dedupes_within_five_minutes():
    clock = {"now": 1000.0}
    ranking = RankingService(clock=lambda: clock["now"], now=lambda: datetime(2026, 9, 12, 12, 0))

    ranking.record_playtime("s1", "10", "GameA", 30)
    ranking.record_playtime("s1", "10", "GameA", 15)
    clock["now"] = 1301.0
    ranking.record_playtime("s1", "10", "GameA", 15)

    day = ranking.play_records["2026-09-12"]["s1"]["10"]
    assert day["minutes"] == 45
    assert day["name"] == "GameA"


def test_aggregate_sums_days_and_filters_sids():
    ranking = RankingService(now=lambda: datetime(2026, 9, 12, 12, 0))
    ranking.play_records = {
        "2026-09-12": {
            "s1": {"10": {"name": "A", "minutes": 20}},
            "s2": {"20": {"name": "B", "minutes": 50}},
        },
        "2026-09-11": {
            "s1": {"10": {"name": "A", "minutes": 10}, "11": {"name": "C", "minutes": 5}},
        },
    }

    rows = ranking.aggregate(days=2, sids=["s1"])
    assert [row["sid"] for row in rows] == ["s1"]
    assert rows[0]["total_minutes"] == 35
    assert [game["name"] for game in rows[0]["games"]] == ["A", "C"]


def test_target_sids_include_push_routes():
    plugin = SimpleNamespace(
        group_steam_ids={"111": ["s1"], "222": ["s3"]},
        push_groups={"s2": ["111"]},
    )
    ranking = RankingService(plugin)
    assert ranking.target_sids("111") == {"s1", "s2"}
    assert ranking.target_sids() == {"s1", "s3"}


def test_record_closed_writes_session_and_playtime():
    ranking = RankingService(now=lambda: datetime(2026, 9, 12, 12, 0), clock=lambda: 2000.0)
    ranking.record_closed("s1", "10", "GameA", 1000, 1600, 10, "111")

    sessions = ranking.session_records["s1"]
    assert sessions[0]["session_id"] == "2026-09-12_1000_10"
    assert sessions[0]["duration_min"] == 10
    assert ranking.play_records["2026-09-12"]["s1"]["10"]["minutes"] == 10
    ranking.record_closed("s1", "10", "GameA", 1000, 1600, 10, "111")
    assert len(ranking.session_records["s1"]) == 1
