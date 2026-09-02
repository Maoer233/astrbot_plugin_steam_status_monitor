"""QQ 群通知会话校验。

AstrBot aiocqhttp 主动推送时会把 GroupMessage 的 session_id 按下划线拆出最后一段，
并要求它是纯数字群号。空群号会被补成 `platform:GroupMessage:0_`，拆完就是空字符串，
触发 `无法发送消息：缺少有效的数字 session_id()`。
"""


def is_valid_group_id(group_id) -> bool:
    return bool(str(group_id or "").strip().isdigit())


def extract_group_session_id(session) -> str:
    text = str(session or "").strip()
    if not text:
        return ""
    parts = text.split(":", 2)
    session_id = parts[2] if len(parts) == 3 else text
    if "_" in session_id:
        session_id = session_id.rsplit("_", 1)[-1]
    return session_id.strip()


def is_sendable_group_session(session) -> bool:
    return extract_group_session_id(session).isdigit()


def build_group_notify_session(platform_id: str, group_id) -> str:
    return f"{platform_id}:GroupMessage:0_{group_id}"
