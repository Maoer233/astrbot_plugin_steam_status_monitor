from src.shared.utils.notify_session import (
    build_group_notify_session,
    extract_group_session_id,
    is_sendable_group_session,
    is_valid_group_id,
)


def test_empty_group_id_is_invalid():
    assert is_valid_group_id("") is False
    assert is_valid_group_id("default") is False
    assert is_valid_group_id("980892742") is True
    assert is_valid_group_id("GROUP_OPENID_1") is True
    assert is_valid_group_id("short") is False


def test_empty_autofill_session_is_not_sendable():
    session = "3640631607:GroupMessage:0_"
    assert extract_group_session_id(session) == ""
    assert is_sendable_group_session(session) is False


def test_real_group_message_session_is_sendable():
    session = "3640631607:GroupMessage:1753538466_980892742"
    assert extract_group_session_id(session) == "980892742"
    assert is_sendable_group_session(session) is True


def test_webui_autofill_session_uses_group_id_after_underscore():
    session = build_group_notify_session("3640631607", "418911866")
    assert session == "3640631607:GroupMessage:0_418911866"
    assert extract_group_session_id(session) == "418911866"
    assert is_sendable_group_session(session) is True


def test_qq_official_openid_session_is_sendable():
    session = "4013550048:GroupMessage:GROUP_OPENID_1"
    assert extract_group_session_id(session) == "GROUP_OPENID_1"
    assert is_sendable_group_session(session) is True
    assert build_group_notify_session("4013550048", "GROUP_OPENID_1") == session


def test_qq_official_openid_with_underscores_is_not_split():
    openid = "abc_def-GHI0123"
    session = f"4013550048:GroupMessage:{openid}"
    assert is_valid_group_id(openid) is True
    assert extract_group_session_id(session) == openid
    assert is_sendable_group_session(session) is True


def test_friend_message_session_is_not_sendable():
    assert is_sendable_group_session("4013550048:FriendMessage:USER_OPENID_1") is False
