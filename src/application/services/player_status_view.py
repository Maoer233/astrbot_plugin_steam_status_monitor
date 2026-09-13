import time
from typing import Any, Dict, List, Optional, Tuple

from ...presentation.renderers.steam_list import get_status_text


# 与 alllist 一致的 personastate -> 状态 映射（0离线,1在线,2忙碌,3离开,4打盹）
_PERSONA_STATUS = {0: "offline", 1: "online", 2: "busy", 3: "away", 4: "snooze"}
STATUS_SORT_RANK = {
    "playing": 0,
    "online": 1,
    "busy": 2,
    "away": 3,
    "snooze": 4,
    "offline": 5,
    "error": 6,
}
_STATUS_ICON = {
    "playing": "🎮",
    "online": "🔵",
    "offline": "💤",
    "busy": "🔴",
    "away": "🟣",
    "snooze": "🟣",
    "error": "⚠️",
}


def build_player_row(sid, status, *, name, zh_game_name="", start_time=None, now=None, **extra):
    """把 Steam 摘要转成列表卡行，不改运行时状态。"""
    now = int(now if now is not None else time.time())
    if not status:
        return {
            "sid": sid,
            "name": name or sid,
            "status": "error",
            "avatar_url": "",
            "game": "",
            "gameid": "",
            "play_str": "获取失败",
            "lastlogoff": None,
            **extra,
        }
    gameid = status.get("gameid")
    lastlogoff = status.get("lastlogoff")
    personastate = status.get("personastate", 0)
    avatar_url = status.get("avatarfull") or status.get("avatar") or ""
    if gameid:
        play_seconds = now - start_time if start_time else 0
        play_minutes = play_seconds / 60
        play_str = f"{play_minutes/60:.1f}小时" if play_minutes >= 60 else f"{play_minutes:.1f}分钟"
        return {
            "sid": sid,
            "name": name,
            "status": "playing",
            "avatar_url": avatar_url,
            "game": zh_game_name,
            "gameid": gameid,
            "play_str": play_str,
            "lastlogoff": lastlogoff,
            **extra,
        }
    if personastate and int(personastate) > 0:
        return {
            "sid": sid,
            "name": name,
            "status": _PERSONA_STATUS.get(int(personastate), "online"),
            "avatar_url": avatar_url,
            "game": "",
            "gameid": "",
            "play_str": "",
            "lastlogoff": lastlogoff,
            **extra,
        }
    hours_ago = (now - int(lastlogoff)) / 3600 if lastlogoff else 0
    return {
        "sid": sid,
        "name": name,
        "status": "offline",
        "avatar_url": avatar_url,
        "game": "",
        "gameid": "",
        "play_str": f"上次在线 {hours_ago:.1f} 小时前" if lastlogoff else "",
        "lastlogoff": lastlogoff,
        **extra,
    }


def sort_rows_for_image(rows: List[dict]) -> List[dict]:
    ordered = list(rows)
    ordered.sort(key=lambda u: STATUS_SORT_RANK.get(u.get("status"), 9))
    return ordered


def format_alllist_text(user_list: List[dict]) -> str:
    lines = ["=== Steam 全群玩家状态 ===\n"]
    by_group: Dict[str, List[dict]] = {}
    for user in user_list:
        by_group.setdefault(user.get("group_id", "?"), []).append(user)
    for gid, members in by_group.items():
        lines.append(f"📋 群: {gid}")
        for user in members:
            icon = _STATUS_ICON.get(user["status"], "❓")
            status_text = get_status_text(user["status"])
            detail = f" 正在玩：{user['game']}" if user["status"] == "playing" and user.get("game") else ""
            play = f" | 时长：{user['play_str']}" if user.get("play_str") else ""
            offline_info = f" | {user['play_str']}" if user["status"] == "offline" and user.get("play_str") else ""
            poll = f" | {user.get('poll_str', '')}" if user.get("poll_str") else ""
            lines.append(f"  {icon} {user['name']} {status_text}{detail}{play}{offline_info}")
            lines.append(f"     ID: {user['sid']}{poll}")
        lines.append("")
    online_count = sum(
        1 for user in user_list if user["status"] in ("playing", "online", "away", "snooze", "busy")
    )
    lines.append(f"📊 在线: {online_count} / 总数: {len(user_list)}")
    return "\n".join(lines)


def _poll_str(next_ts, now: int) -> str:
    sl = int(next_ts - now)
    if sl < 60:
        return f"下次轮询{sl}秒后"
    return f"下次轮询{sl // 60}分钟后"


class PlayerStatusViewService:
    """list / alllist / who 共用读模型：名单、主群、行拼装。不出图。"""

    def __init__(self, plugin):
        self._plugin = plugin

    def steam_ids_for_group(self, group_id: str) -> Tuple[List[str], List[str], List[str]]:
        plugin = self._plugin
        direct = [str(sid) for sid in plugin.group_steam_ids.get(group_id, []) or []]
        push = [
            str(sid)
            for sid, push_groups in (getattr(plugin, "push_groups", {}) or {}).items()
            if group_id in {str(target) for target in push_groups}
        ]
        combined = list(dict.fromkeys([*direct, *push]))
        return direct, push, combined

    def primary_group_of(self, sid: str, fallback: str) -> str:
        sid = str(sid)
        for owner_group_id, owner_steam_ids in self._plugin.group_steam_ids.items():
            if sid in {str(owner_sid) for owner_sid in owner_steam_ids}:
                return owner_group_id
        return fallback

    async def build_row(
        self,
        sid: str,
        status: Optional[dict],
        *,
        group_id: str,
        now: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> dict:
        plugin = self._plugin
        now = int(now if now is not None else time.time())
        name = plugin._resolve_bind_name(sid, (status or {}).get("name") or sid)
        gameid = (status or {}).get("gameid")
        game = (status or {}).get("gameextrainfo")
        zh_game_name = await plugin.get_chinese_game_name(gameid, game) if gameid else (game or "未知游戏")
        start_time = plugin.session_service.started_at(group_id, sid, gameid) if gameid else None
        return build_player_row(
            sid,
            status,
            name=name,
            zh_game_name=zh_game_name,
            start_time=start_time,
            now=now,
            **(extra or {}),
        )

    async def build_group_rows(self, group_id: str, now: Optional[int] = None) -> List[dict]:
        plugin = self._plugin
        now = int(now if now is not None else time.time())
        direct, _, steam_ids = self.steam_ids_for_group(group_id)
        status_map = await plugin.fetch_player_statuses_batch(steam_ids) if steam_ids else {}
        rows = []
        for sid in steam_ids:
            primary = group_id if sid in direct else self.primary_group_of(sid, group_id)
            rows.append(await self.build_row(sid, status_map.get(sid), group_id=primary, now=now))
        return rows

    async def build_all_rows(self, now: Optional[int] = None) -> List[dict]:
        plugin = self._plugin
        now = int(now if now is not None else time.time())
        all_sids = []
        for steam_ids in plugin.group_steam_ids.values():
            all_sids.extend(steam_ids)
        status_map = await plugin.fetch_player_statuses_batch(all_sids) if all_sids else {}
        rows = []
        for group_id, steam_ids in plugin.group_steam_ids.items():
            next_poll = getattr(plugin, "next_poll_time", {}).get(group_id, {}) or {}
            for sid in steam_ids:
                rows.append(
                    await self.build_row(
                        sid,
                        status_map.get(sid),
                        group_id=group_id,
                        now=now,
                        extra={
                            "group_id": group_id,
                            "poll_str": _poll_str(next_poll.get(sid, now), now),
                        },
                    )
                )
        return rows
