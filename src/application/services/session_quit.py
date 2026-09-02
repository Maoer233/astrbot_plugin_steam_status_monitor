import asyncio

from ...presentation.formatters.status import format_play_duration


class SessionQuitMixin:
    """确认退出的唯一入口。不依赖渲染层。"""

    def _end_game_tip(self, duration_min):
        if duration_min < 5:
            return "风扇都没转热，主人就结束了？"
        if duration_min < 10:
            return "杂鱼杂鱼~主人你就这水平？"
        if duration_min < 30:
            return "热身一下就结束了？"
        if duration_min < 60:
            return "歇会儿再来，别太累了喵！"
        if duration_min < 120:
            return "沉浸在游戏世界，时间过得飞快喵！"
        if duration_min < 300:
            return "肝到手软了喵！主人不如陪陪咱~"
        if duration_min < 600:
            return "你吃饭了吗？还是说你已经忘了吃饭这件事？"
        if duration_min < 1200:
            return "家里电费都要被你玩光了喵！"
        if duration_min < 1800:
            return "咱都要给你颁发'不眠猫'勋章了！"
        if duration_min < 2400:
            return "主人你还活着喵？你是不是忘了关电脑呀~"
        return "你已经和椅子合为一体，成为传说中的'椅子精'了喵！"

    def _confirm_quit_immediately(self, group_id, sid, gameid, *, notify=True):
        """唯一确认退出入口：记账、发结束通知、收回开始时间投影。幂等。"""
        pending_sid = self.group_pending_quit.get(group_id, {}).get(sid, {})
        info = pending_sid.get(gameid)
        if not info or info.get("notified"):
            return False

        duration_min = info.get("duration_min") or 0
        if duration_min == 0:
            start_time = info.get("start_time")
            quit_time = info.get("quit_time")
            if start_time and quit_time:
                duration_min = (quit_time - start_time) / 60
                if duration_min > 0:
                    info["duration_min"] = duration_min

        info["notified"] = True
        game_name = info.get("game_name", "未知游戏")
        duration_min = info.get("duration_min", 0)
        self._record_playtime(sid, gameid, game_name, duration_min)
        self._record_session(
            sid=sid,
            gameid=gameid,
            game_name=game_name,
            start_time=info.get("start_time"),
            end_time=info.get("quit_time"),
            duration_min=duration_min,
            group_id=group_id,
        )

        task_key = (group_id, sid, gameid)
        pending_task = getattr(self, "_pending_quit_tasks", {}).pop(task_key, None)
        if pending_task:
            pending_task.cancel()

        key = (group_id, sid, gameid)
        poll_task = getattr(self, "achievement_poll_tasks", {}).pop(key, None)
        if poll_task:
            poll_task.cancel()
        getattr(self, "achievement_snapshots", {}).pop(key, None)
        achievement_monitor = getattr(self, "achievement_monitor", None)
        if achievement_monitor is not None:
            achievement_monitor.clear_game_achievements(group_id, sid, gameid)

        if notify and self.config.get("enable_game_end_notify", True):
            last_state = self.group_last_states.get(group_id, {}).get(sid)
            self._pending_end_notifications.setdefault(group_id, []).append({
                "type": "end",
                "name": info["name"],
                "game": game_name,
                "duration_str": format_play_duration(duration_min),
                "sid": sid,
                "gameid": gameid,
                "quit_time": info["quit_time"],
                "duration_min": duration_min,
                "avatar_url": (last_state or {}).get("avatarfull") or (last_state or {}).get("avatar"),
                "tip_text": self._end_game_tip(duration_min),
            })

        pending_sid.pop(gameid, None)
        start_play_times = self.group_start_play_times.get(group_id, {})
        sid_data = start_play_times.get(sid)
        if isinstance(sid_data, dict):
            sid_data.pop(gameid, None)
        return True

    async def _delayed_quit_check(self, group_id, sid, gameid):
        await asyncio.sleep(180)
        self._confirm_quit_immediately(group_id, sid, gameid)
