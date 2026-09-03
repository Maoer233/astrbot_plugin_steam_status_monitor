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
