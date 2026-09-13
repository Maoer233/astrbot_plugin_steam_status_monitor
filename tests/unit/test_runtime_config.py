from types import SimpleNamespace
from unittest.mock import patch

from src.plugin.runtime_config import apply_hot_update, apply_runtime_config, migrate_legacy_steam_ids


def test_migrate_legacy_steam_ids_moves_flat_list():
    config = migrate_legacy_steam_ids({"steam_ids": "111,222"})
    assert config["group_steam_ids"] == {"default": ["111", "222"]}
    assert "steam_ids" not in config


def test_apply_runtime_config_sets_runtime_fields():
    plugin = SimpleNamespace()
    config = {
        "steam_api_key": "KEY",
        "group_steam_ids": {"111": ["s1"]},
        "smart_poll_intervals": "1,2,3",
        "rank_push_hour": 9,
        "rank_push_minute": 15,
        "enable_proxy": False,
    }
    with patch("src.plugin.runtime_config.configure_tls"), patch(
        "src.plugin.runtime_config.ITADClient"
    ) as client_cls, patch("src.plugin.runtime_config.register_sensitive_values"):
        apply_runtime_config(plugin, config)

    assert plugin.API_KEY == "KEY"
    assert plugin.group_steam_ids == {"111": ["s1"]}
    assert plugin.smart_poll_intervals == [1, 2, 3]
    assert plugin.config["smart_poll_intervals"] == "1,2,3"
    assert plugin.rank_push_hour == 9
    assert plugin.rank_push_minute == 15
    assert plugin.proxy is None
    client_cls.assert_called_once()


def test_apply_hot_update_parses_intervals_and_ints():
    plugin = SimpleNamespace(
        config={"smart_poll_intervals": "1,3,5", "fixed_poll_interval": 0, "steam_api_key": "K"},
        smart_poll_intervals=[1, 3, 5],
    )

    ok, msg = apply_hot_update(plugin, "smart_poll_intervals", "2,4,6")
    assert ok is True
    assert plugin.smart_poll_intervals == [2, 4, 6]
    assert plugin.config["smart_poll_intervals"] == "2,4,6"
    assert "已设置" in msg

    ok, msg = apply_hot_update(plugin, "fixed_poll_interval", "600")
    assert ok is True
    assert plugin.fixed_poll_interval == 600

    ok, msg = apply_hot_update(plugin, "missing", "1")
    assert ok is False
    assert "无效参数" in msg
