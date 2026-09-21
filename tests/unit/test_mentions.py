from types import SimpleNamespace

from src.shared.utils.mentions import parse_mentioned_user_id, qq_avatar_url


def test_parse_numeric_qq_from_cq_and_at_text():
    assert parse_mentioned_user_id("[CQ:at,qq=123456789]") == "123456789"
    assert parse_mentioned_user_id("[At:123456789]") == "123456789"
    assert parse_mentioned_user_id("@昵称(123456789)") == "123456789"
    assert parse_mentioned_user_id("@123456789") == "123456789"


def test_parse_qq_official_user_openid():
    uid = "USER_OPENID_1"
    assert parse_mentioned_user_id(f"[CQ:at,qq={uid}]") == uid
    assert parse_mentioned_user_id(f"[At:{uid}]") == uid
    assert parse_mentioned_user_id(f"@昵称({uid})") == uid
    assert parse_mentioned_user_id(f"@{uid}") == uid
    assert parse_mentioned_user_id(uid) == uid


def test_parse_skips_bot_self_and_reads_message_chain():
    event = SimpleNamespace(
        get_self_id=lambda: "BOT_OPENID",
        get_messages=lambda: [
            SimpleNamespace(qq="BOT_OPENID"),
            SimpleNamespace(qq="USER_OPENID_1"),
        ],
    )
    assert parse_mentioned_user_id("", event=event) == "USER_OPENID_1"
    assert parse_mentioned_user_id("@BOT_OPENID", event=event) == "USER_OPENID_1"


def test_parse_reads_full_message_str_when_arg_empty():
    event = SimpleNamespace(
        get_self_id=lambda: "BOT_OPENID",
        get_message_str=lambda: "/steamwho @USER_OPENID_1",
        get_messages=lambda: [],
    )
    assert parse_mentioned_user_id("", event=event) == "USER_OPENID_1"


def test_qq_avatar_url_only_for_numeric_qq():
    assert qq_avatar_url("123456789") == "https://q1.qlogo.cn/g?b=qq&nk=123456789&s=640"
    assert qq_avatar_url("USER_OPENID_1") is None
    assert qq_avatar_url("") is None
