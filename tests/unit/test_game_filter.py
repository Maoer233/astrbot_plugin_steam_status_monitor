from src.domain.monitoring.game_filter import should_skip_game


def test_should_skip_game_all_mode_never_skips():
    assert should_skip_game({"game_filter_mode": "全部游戏", "game_filter_ids": "10"}, "10") is False
    assert should_skip_game({}, None) is False


def test_should_skip_game_blacklist_and_whitelist():
    blacklist = {"game_filter_mode": "黑名单", "game_filter_ids": "10,20"}
    whitelist = {"game_filter_mode": "白名单", "game_filter_ids": "10,20"}

    assert should_skip_game(blacklist, "10") is True
    assert should_skip_game(blacklist, "30") is False
    assert should_skip_game(whitelist, "10") is False
    assert should_skip_game(whitelist, "30") is True
    assert should_skip_game({"game_filter_mode": "黑名单", "game_filter_ids": ""}, "10") is False
