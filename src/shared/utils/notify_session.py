"""QQ 群通知会话校验。

AstrBot aiocqhttp 主动推送时会把 GroupMessage 的 session_id 按下划线拆出最后一段，
并要求它是纯数字群号。空群号会被补成 `platform:GroupMessage:0_`，拆完就是空字符串，
触发 `无法发送消息：缺少有效的数字 session_id()`。

QQ 官方机器人群标识是 OpenID（字母数字及 `_`/`-`），会话格式为
`platform:GroupMessage:<group_openid>`，不能再按下划线拆，也不能要求末段必须是数字。
"""

from __future__ import annotations

import re

_OPENID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_RESERVED_GROUP_IDS = {"default"}


def is_valid_group_id(group_id) -> bool:
    text = str(group_id or "").strip()
    if not text or text.lower() in _RESERVED_GROUP_IDS:
        return False
    if text.isdigit():
        return True
    return bool(_OPENID_RE.fullmatch(text))


def extract_group_session_id(session) -> str:
    text = str(session or "").strip()
    if not text:
        return ""
    parts = text.split(":", 2)
    session_id = parts[2] if len(parts) == 3 else text
    session_id = session_id.strip()
    if not session_id:
        return ""
    if "_" in session_id:
        prefix, tail = session_id.rsplit("_", 1)
        tail = tail.strip()
        # aiocqhttp: "<msg_id-or-0>_<numeric group id>"；空群号是 `0_`。
        if prefix.replace("-", "").isdigit() and (not tail or tail.isdigit()):
            return tail
    return session_id


def is_sendable_group_session(session) -> bool:
    text = str(session or "").strip()
    parts = text.split(":", 2)
    if len(parts) != 3 or parts[1] != "GroupMessage":
        return False
    return is_valid_group_id(extract_group_session_id(session))


def build_group_notify_session(platform_id: str, group_id) -> str:
    gid = str(group_id).strip()
    if gid.isdigit():
        return f"{platform_id}:GroupMessage:0_{gid}"
    return f"{platform_id}:GroupMessage:{gid}"
