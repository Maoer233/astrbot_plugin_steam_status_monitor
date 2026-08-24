"""QQ 官方机器人后台配置的默认值、清洗与校验。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

DEFAULT_QQ_MENU_COMMANDS = [
    {"command": "/steam help", "description": "查看 Steam 插件指令帮助"},
    {"command": "/steam list", "description": "查看本群玩家当前状态"},
    {"command": "/steam rank", "description": "查看本群今日时长排行"},
    {"command": "/steam rank 7", "description": "查看本群最近七天排行"},
    {"command": "/steam rank 30", "description": "查看本群最近三十天排行"},
]

QQ_OFFICIAL_DEFAULTS: dict[str, Any] = {
    "qq_official_enabled": False,
    "qq_official_appid": "",
    "qq_official_secret": "",
    "qq_official_callback_url": "",
    "qq_official_message_format": "plain",
    "qq_menu_enabled": False,
    "qq_menu_scope": "group",
    "qq_menu_group_openids": [],
    "qq_menu_commands": DEFAULT_QQ_MENU_COMMANDS,
}

_SECRET_MASK = "******"
_APPID_RE = re.compile(r"^[0-9]{5,20}$")
_OPENID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def mask_secret(secret: Any) -> str:
    value = str(secret or "")
    if not value:
        return ""
    return f"{_SECRET_MASK}{value[-4:]}" if len(value) > 4 else _SECRET_MASK


def normalise_qq_official_settings(
    data: dict[str, Any],
    *,
    current_secret: str = "",
) -> dict[str, Any]:
    """只接收面板允许修改的字段，并返回校验后的配置。"""
    result = dict(QQ_OFFICIAL_DEFAULTS)
    result["qq_official_enabled"] = bool(data.get("qq_official_enabled", False))
    result["qq_official_appid"] = str(data.get("qq_official_appid", "") or "").strip()

    raw_secret = str(data.get("qq_official_secret", "") or "").strip()
    result["qq_official_secret"] = (
        current_secret if raw_secret.startswith(_SECRET_MASK) else raw_secret
    )
    result["qq_official_callback_url"] = str(
        data.get("qq_official_callback_url", "") or ""
    ).strip()
    result["qq_official_message_format"] = str(
        data.get("qq_official_message_format", "plain") or "plain"
    ).strip().lower()
    result["qq_menu_enabled"] = bool(data.get("qq_menu_enabled", False))
    result["qq_menu_scope"] = str(
        data.get("qq_menu_scope", "group") or "group"
    ).strip().lower()

    raw_openids = data.get("qq_menu_group_openids", [])
    if isinstance(raw_openids, str):
        values = re.split(r"[,\n]", raw_openids)
    elif isinstance(raw_openids, list):
        values = raw_openids
    else:
        raise ValueError("目标群 OpenID 必须是列表或逗号/换行分隔的文本")
    result["qq_menu_group_openids"] = list(
        dict.fromkeys(str(value).strip() for value in values if str(value).strip())
    )

    raw_commands = data.get("qq_menu_commands", DEFAULT_QQ_MENU_COMMANDS)
    if not isinstance(raw_commands, list):
        raise ValueError("菜单指令必须是列表")
    commands: list[dict[str, str]] = []
    seen_commands: set[str] = set()
    for item in raw_commands:
        if not isinstance(item, dict):
            raise ValueError("每个菜单项必须包含指令和说明")
        command = str(item.get("command", "") or "").strip()
        description = str(item.get("description", "") or "").strip()
        if not command and not description:
            continue
        if not command.startswith("/") or len(command) > 100:
            raise ValueError(f"菜单指令格式无效：{command or '空指令'}")
        if not description or len(description) > 100:
            raise ValueError(f"菜单指令说明格式无效：{command}")
        if command in seen_commands:
            raise ValueError(f"菜单指令不能重复：{command}")
        seen_commands.add(command)
        commands.append({"command": command, "description": description})
    if len(commands) > 20:
        raise ValueError("菜单指令最多配置 20 项")
    result["qq_menu_commands"] = commands

    appid = result["qq_official_appid"]
    secret = result["qq_official_secret"]
    if appid and not _APPID_RE.fullmatch(appid):
        raise ValueError("机器人 AppID 必须为 5 至 20 位数字")
    if secret and (len(secret) < 8 or len(secret) > 256 or any(ch.isspace() for ch in secret)):
        raise ValueError("机器人密钥必须为 8 至 256 个不含空白的字符")
    if result["qq_official_enabled"] and (not appid or not secret):
        raise ValueError("启用 QQ 官方机器人配置时必须填写 AppID 和密钥")

    callback_url = result["qq_official_callback_url"]
    if callback_url:
        parsed = urlparse(callback_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("回调地址必须是完整的 HTTP 或 HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("回调地址不能包含用户名或密码")

    if result["qq_official_message_format"] not in {"plain", "markdown"}:
        raise ValueError("消息格式只支持 plain 或 markdown")
    if result["qq_menu_scope"] not in {"group", "c2c"}:
        raise ValueError("指令面板场景只支持 group 或 c2c")
    for openid in result["qq_menu_group_openids"]:
        if not _OPENID_RE.fullmatch(openid):
            raise ValueError(f"群 OpenID 格式无效：{openid}")

    return result
