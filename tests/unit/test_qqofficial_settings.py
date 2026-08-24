import pytest

from src.presentation.web.qqofficial_settings import (
    QQ_OFFICIAL_DEFAULTS,
    mask_secret,
    normalise_qq_official_settings,
)


def test_normalise_valid_settings_and_deduplicate_openids():
    settings = normalise_qq_official_settings(
        {
            "qq_official_enabled": True,
            "qq_official_appid": "123456789",
            "qq_official_secret": "secret-value",
            "qq_official_callback_url": "https://example.com/qq/webhook",
            "qq_official_message_format": "markdown",
            "qq_menu_enabled": True,
            "qq_menu_scope": "group",
            "qq_menu_group_openids": "GROUP_OPENID_1\nGROUP_OPENID_1,GROUP_OPENID_2",
            "qq_menu_commands": [
                {"command": " /steam list ", "description": " 查看本群状态 "},
                {"command": "/steam rank 7", "description": "最近七天排行"},
            ],
        }
    )

    assert settings["qq_official_enabled"] is True
    assert settings["qq_menu_group_openids"] == [
        "GROUP_OPENID_1",
        "GROUP_OPENID_2",
    ]
    assert settings["qq_menu_commands"] == [
        {"command": "/steam list", "description": "查看本群状态"},
        {"command": "/steam rank 7", "description": "最近七天排行"},
    ]


def test_masked_secret_keeps_current_secret():
    settings = normalise_qq_official_settings(
        {
            **QQ_OFFICIAL_DEFAULTS,
            "qq_official_appid": "123456789",
            "qq_official_secret": "******alue",
        },
        current_secret="secret-value",
    )

    assert settings["qq_official_secret"] == "secret-value"
    assert mask_secret("secret-value") == "******alue"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"qq_official_appid": "abc"}, "AppID"),
        ({"qq_official_callback_url": "ftp://example.com/hook"}, "回调地址"),
        ({"qq_official_message_format": "html"}, "消息格式"),
        ({"qq_menu_scope": "channel"}, "指令面板场景"),
        ({"qq_menu_group_openids": ["bad id"]}, "OpenID"),
        ({"qq_menu_commands": [{"command": "steam list", "description": "状态"}]}, "菜单指令格式"),
        ({"qq_menu_commands": [{"command": "/steam list", "description": ""}]}, "菜单指令说明"),
        ({"qq_menu_commands": [{"command": "/steam list", "description": "一"}, {"command": "/steam list", "description": "二"}]}, "不能重复"),
    ],
)
def test_invalid_settings_are_rejected(overrides, message):
    with pytest.raises(ValueError, match=message):
        normalise_qq_official_settings({**QQ_OFFICIAL_DEFAULTS, **overrides})


def test_enabled_requires_appid_and_secret():
    with pytest.raises(ValueError, match="必须填写 AppID 和密钥"):
        normalise_qq_official_settings(
            {**QQ_OFFICIAL_DEFAULTS, "qq_official_enabled": True}
        )
