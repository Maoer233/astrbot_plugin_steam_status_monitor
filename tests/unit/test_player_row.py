from src.application.services.player_status_view import build_player_row


def test_build_player_row_error_when_status_missing():
    row = build_player_row("s1", None, name="玩家")
    assert row["status"] == "error"
    assert row["play_str"] == "获取失败"


def test_build_player_row_playing_uses_session_start():
    row = build_player_row(
        "s1",
        {"gameid": "10", "avatarfull": "http://a", "lastlogoff": 1},
        name="玩家",
        zh_game_name="艾尔登法环",
        start_time=1000,
        now=2800,
    )
    assert row["status"] == "playing"
    assert row["game"] == "艾尔登法环"
    assert row["play_str"] == "30.0分钟"


def test_build_player_row_keeps_extra_fields_for_alllist():
    row = build_player_row(
        "s1",
        None,
        name="玩家",
        group_id="111",
        poll_str="下次轮询30秒后",
    )
    assert row["group_id"] == "111"
    assert row["poll_str"] == "下次轮询30秒后"
    assert row["status"] == "error"


def test_build_player_row_offline_uses_lastlogoff():
    row = build_player_row(
        "s1",
        {"personastate": 0, "lastlogoff": 1000, "avatar": "http://a"},
        name="玩家",
        now=4600,
    )
    assert row["status"] == "offline"
    assert row["play_str"] == "上次在线 1.0 小时前"
