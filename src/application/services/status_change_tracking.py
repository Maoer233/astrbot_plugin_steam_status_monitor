import time

from ...domain.monitoring.polling import calculate_poll_schedule
from ...domain.monitoring.presence import (
    PRESENCE_OFFLINE_DEBOUNCE_SECONDS,
    PRESENCE_RECHECK_INTERVAL_SECONDS,
    is_online,
    presence_transition,
)
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
            previous = last_states.get(sid)
            _, events = await self.session_service.handle(
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
            # presence 跟踪必须在 next_poll 写入之后：防抖待定要钳制复查轮询，
            # 放在前面会被上面的自然调度值覆盖。
            self._track_presence(group_id, sid, previous, status, events, player_name, now, skip_push, next_poll)
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

    def _track_presence(self, group_id, sid, previous, status, events, player_name, now, skip_push, next_poll):
        """在线状态跟踪与推送入队（personastate 与游戏会话正交，不在 session 状态机内判定）。

        - 上线转换：推「已上线」（本轮开局已隐含上线的除外），并记录在线起始时间。
        - 下线转换：不即推——进 180 秒防抖待定（_presence_pending_offline，持久化），
          由主循环每分钟结算；窗口内回归上线则取消推送并恢复在线起始计时。
        - 同态在线但无起始记录（升级后首次观测/基线被外部改写）：从本轮补记起算。
        - 防抖待定存在时钳制该玩家的复查轮询（离线自然档位 3-30 分钟赶不上窗口）。
        """
        transition = presence_transition(previous, status)
        online_since = self._presence_online_since
        pending = self._presence_pending_offline
        key = str(sid)
        if transition is None:
            if is_online(status):
                if key in pending:
                    # 待定未结算但快照已在线：上游写新基线等场景，直接取消
                    del pending[key]
                if key not in online_since:
                    online_since[key] = now
            self._clamp_recheck_poll(key, now, next_poll)
            return
        if transition.kind == "online":
            was_pending = key in pending
            # 窗口内回归：取消下线推送，在线时长从原始起算时刻连续计算
            restored_since = pending.pop(key, {}).get("online_since")
            online_since[key] = restored_since if restored_since is not None else online_since.get(key, now)
            if was_pending:
                # 群里从没见过「已离线」，回归后单独推「已上线」是噪音，静默取消
                return
            if skip_push:
                return
            if any(event.kind == "started" for event in events):
                return
            if not self.config.get("enable_user_online_notify", True):
                return
            self._enqueue_presence(group_id, sid, player_name, now, "online", None)
            return
        # 下线转换：进入防抖待定，不立即推送
        started_at = online_since.pop(key, None)
        if skip_push or not self.config.get("enable_user_offline_notify", True):
            # 启动基线/群监控关闭/开关已关：不建待定，避免重放停机期间历史
            pending.pop(key, None)
            self._clamp_recheck_poll(key, now, next_poll)
            return
        pending[key] = {
            "detected_at": now,
            "deadline": now + PRESENCE_OFFLINE_DEBOUNCE_SECONDS,
            "online_since": started_at,
            "online_minutes": max(0.0, (now - started_at) / 60) if started_at is not None else None,
            "name": player_name,
        }
        self._clamp_recheck_poll(key, now, next_poll)

    def _clamp_recheck_poll(self, key, now, next_poll):
        """防抖窗口内把该玩家的下次轮询钳到 60 秒，确保窗口结束前有新鲜快照。"""
        pending = self._presence_pending_offline
        if key not in pending:
            return
        recheck_at = now + PRESENCE_RECHECK_INTERVAL_SECONDS
        if next_poll.get(key, 0) > recheck_at:
            next_poll[key] = recheck_at

    def _settle_pending_offline_presence(self, now):
        """结算到期的下线防抖待定：对监控该玩家的每个群入队 offline 通知。

        由主循环每分钟调用；event_time 用检测时刻（去重键），online_minutes 用
        检测时算好的值。结算时二次检查开关（窗口内热关则不推）与群监控开关。
        """
        pending = self._presence_pending_offline
        if not pending:
            return
        for key in [sid for sid, item in pending.items() if now >= item.get("deadline", 0)]:
            item = pending.pop(key)
            if not self.config.get("enable_user_offline_notify", True):
                continue
            for group_id, steam_ids in self.group_steam_ids.items():
                if key not in {str(sid) for sid in steam_ids}:
                    continue
                if not self.group_monitor_enabled.get(str(group_id), True):
                    continue
                self._enqueue_presence(
                    group_id,
                    key,
                    item.get("name") or key,
                    item.get("detected_at", now),
                    "offline",
                    item.get("online_minutes"),
                )
            self._data_dirty = True

    def _enqueue_presence(self, group_id, sid, player_name, event_time, kind, online_minutes):
        """presence 通知入队，复用游戏通知的合并发送、去重与失败重试管线。"""
        self._pending_end_notifications.setdefault(group_id, []).append(
            {
                "type": kind,
                "sid": sid,
                "name": player_name,
                "event_time": event_time,
                "online_minutes": online_minutes,
            }
        )
