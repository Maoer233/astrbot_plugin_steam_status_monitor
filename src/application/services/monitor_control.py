from dataclasses import dataclass
from typing import Optional
import time

from ...shared.utils.notify_session import is_valid_group_id


@dataclass(frozen=True)
class MonitorControlResult:
    ok: bool
    code: str
    message: str = ""


class MonitorControlService:
    """群监控启停、水合 skip_push、成就开关。不负责名单增删。"""

    def __init__(self, plugin):
        self._plugin = plugin

    def set_achievement(self, group_id: str, enabled: bool) -> MonitorControlResult:
        plugin = self._plugin
        plugin.group_achievement_enabled[group_id] = enabled
        plugin._save_group_switches()
        if enabled:
            return MonitorControlResult(True, "achievement_on", "已为本群开启Steam成就推送。")
        return MonitorControlResult(True, "achievement_off", "已为本群关闭Steam成就推送。")

    def stop(self, group_id: str) -> MonitorControlResult:
        plugin = self._plugin
        plugin.group_monitor_enabled[group_id] = False
        plugin.running_groups.discard(group_id)
        plugin._save_group_switches()
        plugin.next_poll_time.pop(group_id, None)
        getattr(plugin, "_pending_end_notifications", {}).pop(group_id, None)
        tasks = getattr(plugin, "achievement_poll_tasks", {})
        for key in [k for k in list(tasks.keys()) if k[0] == group_id]:
            task = tasks.pop(key, None)
            if task:
                task.cancel()
        return MonitorControlResult(
            True,
            "stopped",
            "已为本群彻底关闭Steam监控，轮询已停止。使用 /steam on 可重新启动。",
        )

    def ensure_running(self, group_id: str, notify_session: Optional[str] = None) -> bool:
        """addid 自动启动：开开关并加入 running，不水合。已在跑则 False。"""
        plugin = self._plugin
        if group_id in plugin.running_groups:
            return False
        plugin.group_monitor_enabled[group_id] = True
        plugin.running_groups.add(group_id)
        if not hasattr(plugin, "notify_sessions"):
            plugin.notify_sessions = {}
        if notify_session:
            plugin.notify_sessions[group_id] = notify_session
            plugin._save_notify_session()
        if group_id not in plugin.group_last_states:
            plugin.group_last_states[group_id] = {}
        return True

    async def start(self, group_id: str, notify_session: Optional[str] = None) -> MonitorControlResult:
        plugin = self._plugin
        if not is_valid_group_id(group_id):
            return MonitorControlResult(False, "invalid_group", "请在群聊中使用该命令，私聊无法启动群监控。")
        plugin.group_monitor_enabled[group_id] = True
        plugin._save_group_switches()
        if not getattr(plugin, "API_KEY", ""):
            return MonitorControlResult(
                False,
                "missing_api_key",
                "未配置 Steam API Key，请先在插件配置中填写 steam_api_key。",
            )
        steam_ids = plugin.group_steam_ids.get(group_id, [])
        if not steam_ids or not any(isinstance(x, str) and x.strip() for x in steam_ids):
            return MonitorControlResult(
                False,
                "empty_watchlist",
                "未设置监控的 SteamID 列表，请先在插件配置中填写 steam_ids，"
                "或使用 /steam addid [SteamID] 添加要监控的玩家。",
            )
        if group_id in plugin.running_groups:
            return MonitorControlResult(False, "already_running", "本群Steam监控已在运行。")
        plugin.running_groups.add(group_id)
        if not hasattr(plugin, "notify_sessions"):
            plugin.notify_sessions = {}
        if notify_session:
            plugin.notify_sessions[group_id] = notify_session
            plugin._save_notify_session()
        if group_id not in plugin.group_last_states:
            plugin.group_last_states[group_id] = {}
        status_map = await plugin.fetch_player_statuses_batch(steam_ids) if steam_ids else {}
        now = int(time.time())
        for sid in steam_ids:
            status = status_map.get(sid)
            if not status:
                continue
            plugin.group_last_states[group_id][sid] = status
            await plugin.session_service.handle(
                group_id,
                sid,
                status.get("gameid"),
                now,
                player_name=status.get("name") or sid,
                current_game_name=status.get("gameextrainfo") or "未知游戏",
                status=status,
                skip_push=True,
            )
        return MonitorControlResult(True, "started", "本群Steam状态监控启动完成喔！ヾ(≧ω≦)ゞ")
