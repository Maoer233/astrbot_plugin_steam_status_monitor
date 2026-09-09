import time

from ...domain.monitoring.polling import calculate_poll_schedule
from ...presentation.formatters.status import format_player_status

_STATUS_UNSET = object()


class StatusChangeTrackingMixin:
    """Status polling change detection. Session ownership lives in SessionService."""

    async def check_status_change(self, group_id, single_sid=None, status_override=_STATUS_UNSET, poll_level=None, skip_push=False):
        '''轮询检测玩家状态变更并推送通知（分群，支持单个sid）。
        必须由调用方传入本轮批量结果 status_override；缺失则跳过，不再单查。
        返回精简日志字符串，不直接打印日志'''
        now = int(time.time())
        # 插件重启后首次初始化期间：若该群状态文件是"停止期间遗留的旧数据"，本次检测到的变化
        # 是插件停止期间累积的历史，跳过推送，只更新状态基线，避免重载后刷屏。
        if self._startup_stale_groups.get(group_id, False):
            skip_push = True
        if not self.group_monitor_enabled.get(str(group_id), True):
            skip_push = True
        steam_ids = [single_sid] if single_sid else self.group_steam_ids.get(group_id, [])
        last_states = self.group_last_states.setdefault(group_id, {})
        logs = []
        for sid in steam_ids:
            # 检测循环只消费本轮批量结果；没有覆盖值就跳过，禁止打网单查。
            if not single_sid or status_override is _STATUS_UNSET:
                logs.append(f"{sid}: 获取失败")
                continue
            status = status_override
            if not status:
                logs.append(f"{sid}: 获取失败")
                continue
            current_gameid = status.get("gameid")
            current_game = status.get("gameextrainfo")
            current_game_name = await self.get_chinese_game_name(current_gameid, current_game) if current_gameid else (current_game or "")
            player_name = self._resolve_bind_name(sid, status.get("name") or sid)
            await self.session_service.handle(
                group_id,
                sid,
                current_gameid,
                now,
                player_name=player_name,
                current_game_name=current_game_name or "未知游戏",
                status=status,
                skip_push=skip_push,
            )
            last_states[sid] = status
            next_poll = self.next_poll_time.setdefault(group_id, {})
            poll_interval, poll_level_str = calculate_poll_schedule(
                now=now,
                gameid=current_gameid,
                personastate=status.get("personastate"),
                lastlogoff=status.get("lastlogoff"),
                fixed_interval=self.fixed_poll_interval,
                intervals=self.smart_poll_intervals,
            )
            interval_min = poll_interval // 60
            next_time = ((now // 60) + max(1, (poll_interval + 59) // 60)) * 60
            if interval_min >= 1 and interval_min in list(self.smart_poll_intervals)[1:]:
                next_time = ((now // 60) // interval_min + 1) * interval_min * 60
            next_poll[sid] = next_time
            logs.append(format_player_status(
                name=player_name,
                game_name=current_game_name,
                gameid=current_gameid,
                personastate=status.get("personastate"),
                lastlogoff=status.get("lastlogoff"),
                now=now,
                poll_level=poll_level_str,
            ))
        self._data_dirty = True
        self._save_persistent_data()
        return "\n".join(logs) if logs else None
