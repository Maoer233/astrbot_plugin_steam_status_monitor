import asyncio
import time
from datetime import datetime

from ...shared.logging import logger


class PollingTrackingMixin:
    """Global polling scheduling and startup initialization orchestration."""

    async def init_poll_time_once(self):
        '''插件启动后10秒内进行一次全员初始化轮询，设置每个SteamID的next_poll_time，并输出一次初始日志'''
        await asyncio.sleep(10)
        all_logs = []
        # 一次性标记"插件停止期间遗留的陈旧群"：init 过程中 check_status_change 末尾会刷新
        # states.json 的 mtime，若不预先缓存，同一次 init 内后续 sid 会因 mtime 已刷新而漏判。
        # init 完成后（含异常）清空，后续正常轮询不再跳过播报。
        self._startup_stale_groups = {
            gid: self._is_group_state_stale(gid)
            for gid in self.group_steam_ids
        }
        try:
            # Steam 状态查询按 SID 去重，但状态基线必须按群分别建立。
            unique_sids = list(dict.fromkeys(
                sid
                for steam_ids in self.group_steam_ids.values()
                for sid in steam_ids
            ))
            status_map = await self.fetch_player_statuses_batch(unique_sids)
            for group_id in self.group_steam_ids:
                steam_ids = self.group_steam_ids[group_id]
                group_lines = []
                for sid in steam_ids:
                    # 状态基线按群保存；即使同一玩家属于多个群，也必须逐群初始化。
                    msg = await self.check_status_change(
                        group_id,
                        single_sid=sid,
                        status_override=status_map.get(sid),
                        skip_push=True,
                    )
                    if msg:
                        group_lines.append(msg)
                if group_lines:
                    all_logs.append(f"群{group_id}：\n" + "\n".join(group_lines))
            if all_logs:
                logger.info("====== Steam状态监控初始化日志 ======\n" + "\n".join(all_logs) + "\n=====================================================")
        finally:
            self._startup_stale_groups.clear()

    async def global_poll_and_log_loop(self):
        '''全局定时并发查询所有群Steam状态，按动态间隔判断是否需要查询，40秒统一输出日志'''
        while True:
            try:
                # 计算距离下一个整分钟0秒的秒数
                now = time.time()
                next_minute = (int(now) // 60 + 1) * 60
                await asyncio.sleep(max(0, next_minute - now))
                # 0秒：跨群收集所有到点的SteamID，合并为一次批量查询（N群=1次API调用+自动去重）
                group_ids = list(self.group_steam_ids.keys())
                group_sids = {}  # {group_id: [sid, ...]}
                all_sids_set = set()
                now2 = time.time()
                for group_id in group_ids:
                    if not self.group_monitor_enabled.get(group_id, True):
                        continue
                    steam_ids = self.group_steam_ids.get(group_id, [])
                    next_poll = self.next_poll_time.setdefault(group_id, {})
                    sids_to_query = list(dict.fromkeys(
                        sid for sid in steam_ids
                        if now2 >= next_poll.get(sid, 0)
                    ))
                    if not sids_to_query:
                        continue
                    group_sids[group_id] = sids_to_query
                    all_sids_set.update(sids_to_query)
                # 每日排行榜自动推送（以凌晨4:00为一天分界，推送时间可在配置中设定）
                # 注意：此检查必须放在 continue 之前，否则当没有玩家需要轮询时会跳过排行榜推送
                now_dt = datetime.now()
                push_hour = getattr(self, 'rank_push_hour', 8)
                push_minute = getattr(self, 'rank_push_minute', 30)
                if now_dt.hour == push_hour and now_dt.minute == push_minute:
                    push_date_key = self._get_day_key(-1)
                    if self._last_rank_push_date != push_date_key and hasattr(self, 'rank_push_groups') and (self.rank_push_groups or getattr(self, 'rank_push_all', False)):
                        self._last_rank_push_date = push_date_key
                        logger.info(f"[排行榜] 开始每日自动推送，时间={push_hour}:{push_minute:02d}，目标群: {self.rank_push_groups if self.rank_push_groups else '全部群(rank_push_all)'}")
                        asyncio.create_task(self._daily_rank_push())
                # 节流保存：本轮有脏数据且超过间隔则落盘，避免每次 check_status_change 都写盘
                if getattr(self, '_data_dirty', False) and (time.time() - getattr(self, '_last_save_time', 0)) >= getattr(self, '_save_interval', 300):
                    try:
                        self._save_persistent_data(force=True)
                    except Exception as e:
                        logger.error(f"[SteamStatusMonitor] 节流保存失败: {e}")
                # 离线玩家可能数十分钟才再入轮询，deadline 必须每分钟单独检查。
                self.session_service.tick_due(int(now2))
                if not group_sids:
                    await self._flush_pending_end_notifications()
                    await asyncio.sleep(40)  # 本轮无到点，跳过
                    continue
                # 一次批量查询所有到点SteamID（去重），大幅减少API调用
                all_sids = list(all_sids_set)
                global_status_map = await self.fetch_player_statuses_batch(all_sids)
                # 各群并行处理状态变更检测
                async def query_one_group(gid, sids):
                    round_msg_lines = []
                    tasks = []
                    for sid in sids:
                        override = global_status_map.get(sid)
                        tasks.append(self.check_status_change(gid, single_sid=sid, status_override=override))
                    if tasks:
                        results = await asyncio.gather(*tasks, return_exceptions=True)
                        for msg in results:
                            if isinstance(msg, Exception):
                                logger.error(f"[轮询] check_status_change 异常: {msg} (gid={gid})")
                                continue
                            if msg:
                                round_msg_lines.append(msg)
                    if round_msg_lines:
                        self._last_round_logs.append((gid, "\n".join(round_msg_lines)))
                poll_tasks = [query_one_group(gid, sids) for gid, sids in group_sids.items()]
                await asyncio.gather(*poll_tasks, return_exceptions=True)
                # 统一 flush 本轮收集的所有通知（开始游戏 + 延迟退出的结束游戏），合并发送
                await self._flush_pending_end_notifications()
                # 40秒统一输出日志
                await asyncio.sleep(40)
                if self._last_round_logs:
                    if self.detailed_poll_log:
                        all_logs = []
                        for group_id, logstr in self._last_round_logs:
                            all_logs.append(f"群{group_id}：\n" + logstr)
                        logger.info("====== Steam状态监控轮询日志 ======\n" + "\n".join(all_logs) + "\n=====================================================")
                    else:
                        logger.info("周期轮询成功")
                self._last_round_logs.clear()
            except asyncio.CancelledError:
                # terminate 主动取消，正常退出循环，不要吞掉
                logger.info("[SteamStatusMonitor] 主轮询循环已取消")
                raise
            except Exception:
                # 其他异常：保留堆栈后继续循环，防止单次异常导致轮询彻底失效
                logger.exception("[SteamStatusMonitor] 主轮询循环异常，5 秒后继续")
                await asyncio.sleep(5)

