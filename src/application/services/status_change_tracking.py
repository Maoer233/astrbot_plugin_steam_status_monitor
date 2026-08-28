import asyncio
import time

from ...domain.monitoring.polling import calculate_poll_schedule
from ...domain.monitoring.transitions import classify_game_transition
from ...presentation.formatters.status import format_player_status
from ...shared.logging import logger


class StatusChangeTrackingMixin:
    """Status polling change detection and task orchestration.""""

    async def check_status_change(self, group_id, single_sid=None, status_override=None, poll_level=None, skip_push=False):
        '''轮询检测玩家状态变更并推送通知（分群，支持单个sid）
        返回精简日志字符串，不直接打印日志'''
        now = int(time.time())
        # 插件重启后首次初始化期间：若该群状态文件是"停止期间遗留的旧数据"，本次检测到的变化
        # 属于历史变化（如玩家在插件关闭期间切了游戏），跳过播报，避免补播过时信息。
        state_stale = self._startup_stale_groups.get(group_id, False)
        steam_ids = [single_sid] if single_sid else self.group_steam_ids.get(group_id, [])
        last_states = self.group_last_states.setdefault(group_id, {})
        start_play_times = self.group_start_play_times.setdefault(group_id, {})
        last_quit_times = self.group_last_quit_times.setdefault(group_id, {})
        pending_logs = self.group_pending_logs.setdefault(group_id, {})
        pending_quit = self.group_pending_quit.setdefault(group_id, {})
        recent_games = self.group_recent_games.setdefault(group_id, [])
        notify_session = getattr(self, 'notify_sessions', {}).get(group_id, None)
        msg_lines = []
        notifications = []  # 本轮收集的状态变更通知，改为统一合并发送
        for sid in steam_ids:
            status = status_override if status_override and sid == single_sid else await self.fetch_player_status(sid)
            if not status:
                continue
            prev = last_states.get(sid)
            name = self._resolve_bind_name(sid, status.get('name') or sid)
            gameid = status.get('gameid')
            game = status.get('gameextrainfo')
            lastlogoff = status.get('lastlogoff')
            personastate = status.get('personastate', 0)
            zh_game_name = await self.get_chinese_game_name(gameid, game) if gameid else (game or "未知游戏")
            prev_gameid = prev.get('gameid') if prev else None
            current_gameid = gameid
            transition = classify_game_transition(
                prev,
                status,
                pending_quit=pending_quit.get(sid),
                now=now,
            )
            # --- 退出游戏（缓冲3分钟） ---（含游戏切换：直接切到另一款游戏也会结算上一款时长）
            if transition.has_exit:
                logger.info(f"[退出逻辑] {name} prev_gameid={prev_gameid} current_gameid={current_gameid}")
                zh_prev_game_name = await self.get_chinese_game_name(prev_gameid, prev.get('gameextrainfo') if prev else None) if prev_gameid else (prev.get('gameextrainfo') if prev else "未知游戏")
                duration_min = 0
                # 安全获取 sid_data，兼容旧格式 int → dict
                sid_data = start_play_times.get(sid)
                if not isinstance(sid_data, dict):
                    sid_data = {}
                    start_play_times[sid] = sid_data
                start_time = sid_data.get(prev_gameid, now)
                if prev_gameid in sid_data:
                    duration_min = (now - sid_data[prev_gameid]) / 60
                    if duration_min == 0:
                        for _ in range(2):
                            start_time = sid_data.get(prev_gameid, now)
                            duration_min = (now - start_time) / 60
                            if duration_min > 0:
                                break
                            await asyncio.sleep(1)
                self.achievement_monitor.clear_game_achievements(group_id, sid, prev_gameid)
                if not self._should_skip_game(prev_gameid) and not state_stale:
                    pending_quit.setdefault(sid, {})[prev_gameid] = {
                        "quit_time": now,
                        "name": name,
                        "game_name": zh_prev_game_name,
                        "duration_min": duration_min,
                        "start_time": start_time,
                        "notified": False
                    }
                    # 成就结算：游戏结束时，延迟15分钟再做一次对比
                    try:
                        player_name = name
                        game_name = zh_prev_game_name
                        key = (group_id, sid, prev_gameid)
                        poll_task = self.achievement_poll_tasks.pop(key, None)
                        if poll_task:
                            poll_task.cancel()
                        if not skip_push:
                            asyncio.create_task(self.achievement_delayed_final_check(group_id, sid, prev_gameid, player_name, game_name))
                    except Exception as e:
                        logger.error(f"结算成就时异常: {e}")
                    # 延迟退出任务必须包含群维度，避免同一玩家在多个群的任务互相取消。
                    if not hasattr(self, '_pending_quit_tasks'):
                        self._pending_quit_tasks = {}
                    task_key = (group_id, sid, prev_gameid)
                    old_task = self._pending_quit_tasks.get(task_key)
                    if old_task:
                        old_task.cancel()
                    if not skip_push:
                        task = asyncio.create_task(self._delayed_quit_check(group_id, sid, prev_gameid))
                        self._pending_quit_tasks[task_key] = task
                else:
                    reason = "黑白名单过滤" if self._should_skip_game(prev_gameid) else "插件停止期间的遗留变化"
                    logger.info(f"[退出跳过] {name} 退出游戏 {zh_prev_game_name}({prev_gameid}) 被跳过（{reason}）")
                last_quit_times.setdefault(sid, {})[prev_gameid] = now
                last_states[sid] = status
                if current_gameid in [None, "", "0"]:
                    continue  # 纯退出：防止重复推送
                # 游戏切换：不continue，继续执行下方开始游戏逻辑

            # --- 开始游戏/继续游戏（仅当 gameid 变更时推送） ---
            if current_gameid not in [None, "", "0"] and current_gameid != prev_gameid:
                quit_info = pending_quit.setdefault(sid, {}).get(current_gameid)
                # 网络波动已由领域层统一判定（3分钟内重启同一游戏）
                if transition.is_network_fluctuation and quit_info:
                    # 只取消当前群对应的延迟任务。
                    task_key = (group_id, sid, current_gameid)
                    pending_task = getattr(self, '_pending_quit_tasks', {}).pop(task_key, None)
                    if pending_task:
                        pending_task.cancel()
                    quit_info["notified"] = True
                    pending_quit[sid].pop(current_gameid, None)
                    msg = f"⚠️ {name} 游玩 {zh_game_name} 时网络波动了"
                    # 网络波动通知开关检查
                    if not self.config.get('enable_network_fluctuation_notify', True):
                        last_states[sid] = status
                        continue
                    if skip_push:
                        last_states[sid] = status
                        continue
                    # 推送到主群和所有联动群
                    notify_sessions = []
                    notify_session = getattr(self, 'notify_sessions', {}).get(group_id, None)
                    if notify_session:
                        notify_sessions.append(notify_session)
                    for push_gid in self.push_groups.get(sid, []):
                        push_session = getattr(self, 'notify_sessions', {}).get(push_gid, None)
                        if push_session and push_session not in notify_sessions:
                            notify_sessions.append(push_session)
                    for session in notify_sessions:
                        await self.context.send_message(session, MessageChain([Plain(msg)]))
                    last_states[sid] = status
                    continue  # 只推送网络波动提醒，跳过后续逻辑
                # 修复：补充开始游戏推送逻辑
                if self._should_skip_game(current_gameid):
                    logger.info(f"[游戏过滤] {name} 开始游戏 {zh_game_name}({current_gameid}) 被跳过（黑白名单过滤）")
                    start_play_times.setdefault(sid, {})[current_gameid] = now
                    last_states[sid] = status
                    continue
                start_play_times.setdefault(sid, {})[current_gameid] = now
                # 收集通知，由末尾统一合并发送（不在循环内逐条推送）
                if not skip_push and not state_stale and self.config.get('enable_game_start_notify', True):
                    notifications.append({
                        "type": "start",
                        "name": name,
                        "game": zh_game_name,
                        "sid": sid,
                        "gameid": current_gameid,
                        "status": status,
                    })
                # 成就监控任务启动（受 enable_achievement_poll 配置控制）
                if skip_push or not self.config.get('enable_achievement_poll', True):
                    last_states[sid] = status
                    continue
                try:
                    player_name = name
                    game_name = zh_game_name
                    key = (group_id, sid, current_gameid)
                    achievements = await self.achievement_monitor.get_player_achievements(self.API_KEY, group_id, sid, current_gameid)
                    self.achievement_snapshots[key] = list(achievements) if achievements else []
                    # 新增日志：已成功获取成就列表
                    unlocked_count = len(achievements) if achievements else 0
                    # 获取总成就数量
                    details = await self.achievement_monitor.get_achievement_details(group_id, current_gameid, lang="schinese", api_key=self.API_KEY, steamid=sid)
                    total_count = len(details) if details else 0
                    logger.info(f"[成就初始化] {name} 已成功获取成就列表 {unlocked_count}/{total_count} 游戏名：{zh_game_name}")
                    poll_task = asyncio.create_task(self.achievement_periodic_check(group_id, sid, current_gameid, player_name, game_name))
                    self.achievement_poll_tasks[key] = poll_task
                except Exception as e:
                    logger.error(f"启动成就监控任务异常: {e}")
                last_states[sid] = status
                continue

            # 智能轮询间隔由领域策略计算；这里仅负责调度适配和消息组装。
            next_poll = self.next_poll_time.setdefault(group_id, {})
            poll_interval, poll_level_str = calculate_poll_schedule(
                now=now,
                gameid=gameid,
                personastate=personastate,
                lastlogoff=lastlogoff,
                fixed_interval=self.fixed_poll_interval,
                intervals=self.smart_poll_intervals,
            )
            interval_min = poll_interval // 60
            next_time = ((now // 60) + max(1, (poll_interval + 59) // 60)) * 60
            if interval_min >= 1 and interval_min in list(self.smart_poll_intervals)[1:]:
                next_time = ((now // 60) // interval_min + 1) * interval_min * 60
            next_poll[sid] = next_time
            # 轮询间隔描述
            if gameid:
                msg_lines.append(f"🟢【{name}】正在玩 {zh_game_name}（{poll_level_str}）")
            else:
                msg_lines.append(format_player_status(
                    name=name,
                    game_name=zh_game_name,
                    gameid=gameid,
                    personastate=personastate,
                    lastlogoff=lastlogoff,
                    now=now,
                    poll_level=poll_level_str,
                ))
            last_states[sid] = status

        for sid in pending_quit:
            for gameid in list(pending_quit[sid].keys()):
                info = pending_quit[sid][gameid]
                if now - info["quit_time"] >= 180 and not info.get("notified"):
                    info["notified"] = True
                    # 游戏结束通知开关：关闭则跳过推送，但仍清理 pending_quit
                    if not self.config.get('enable_game_end_notify', True):
                        if gameid in pending_quit[sid]:
                            del pending_quit[sid][gameid]
                        continue
                    duration_min = info.get("duration_min", 0)
                    # 优化时间显示
                    time_str = format_play_duration(duration_min)
                    # 收集到通知缓冲，由主轮询统一合并发送（兜底逻辑，正常由 _delayed_quit_check 处理）
                    avatar_url = None
                    ls = last_states.get(sid)
                    if ls:
                        avatar_url = ls.get("avatarfull") or ls.get("avatar")
                    notifications.append({
                        "type": "end",
                        "name": info["name"],
                        "game": info["game_name"],
                        "duration_str": time_str,
                        "sid": sid,
                        "gameid": gameid,
                        "quit_time": info["quit_time"],
                        "duration_min": duration_min,
                        "avatar_url": avatar_url,
                        "tip_text": "你已经和椅子合为一体，成为传说中的'椅子精'了喵！",
                    })
                    if gameid in pending_quit[sid]:
                        del pending_quit[sid][gameid]

        self._save_persistent_data()
        # 将本轮收集的开始/结束游戏通知提交到缓冲区，由主轮询统一 flush 合并发送
        if notifications and not skip_push:
            self._pending_end_notifications.setdefault(group_id, []).extend(notifications)
        # 只返回日志字符串
        return "\n".join(msg_lines) if msg_lines else None

