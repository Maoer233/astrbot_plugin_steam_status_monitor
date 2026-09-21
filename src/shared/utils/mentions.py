import re
from typing import Optional

_MENTION_RE = re.compile(
    r"\[CQ:at,qq=([^,\]]+)[^\]]*\]"
    r"|\[At:([^\]]+)\]"
    r"|@[^(\s]+?\(([^)]+)\)"
    r"|@([A-Za-z0-9_-]+)"
)


def is_valid_user_id(value: Optional[str]) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text.isdigit():
        return len(text) >= 5
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{8,64}", text))


def qq_avatar_url(user_id: Optional[str]) -> Optional[str]:
    text = str(user_id or "").strip()
    if not text.isdigit():
        return None
    return f"https://q1.qlogo.cn/g?b=qq&nk={text}&s=640"


def parse_mentioned_user_id(text: str = "", event=None) -> Optional[str]:
    self_ids = _self_ids(event)
    for source in (text, _event_message_str(event)):
        for uid in _ids_from_text(source):
            if uid not in self_ids:
                return uid
    for uid in _ids_from_event(event):
        if uid not in self_ids:
            return uid
    fallback = str(text or "").strip().lstrip("@")
    if is_valid_user_id(fallback) and fallback not in self_ids:
        return fallback
    return None


def _self_ids(event) -> set[str]:
    ids: set[str] = set()
    if event is None:
        return ids
    for attr in ("get_self_id", "get_bot_id"):
        getter = getattr(event, attr, None)
        if not callable(getter):
            continue
        try:
            value = str(getter() or "").strip()
        except Exception:
            continue
        if value:
            ids.add(value)
    return ids


def _ids_from_text(text: str) -> list[str]:
    found: list[str] = []
    for match in _MENTION_RE.finditer(str(text or "")):
        uid = next((group for group in match.groups() if group), "")
        uid = str(uid).strip()
        if is_valid_user_id(uid):
            found.append(uid)
    return found


def _event_message_str(event) -> str:
    if event is None:
        return ""
    getter = getattr(event, "get_message_str", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:
            return ""
    return str(getattr(event, "message_str", "") or "")


def _ids_from_event(event) -> list[str]:
    if event is None:
        return []
    getter = getattr(event, "get_messages", None)
    if not callable(getter):
        return []
    try:
        messages = getter() or []
    except Exception:
        return []
    found: list[str] = []
    for component in messages:
        qq = getattr(component, "qq", None)
        if qq is None:
            continue
        uid = str(qq).strip()
        if is_valid_user_id(uid):
            found.append(uid)
    return found
