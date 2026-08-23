from astrbot.api.star import Star, Context
from ..shared.logging import logger, register_sensitive_values
from ..shared.network import configure_tls, httpx_client_kwargs, requests_verify
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image  # 确保已导入 Image
import json
import time
import httpx
import asyncio
import os
import random
from ..application.services.openbox import handle_openbox
from ..application.services.steam_list import handle_steam_list
import re
from ..application.services.achievement_monitor import AchievementMonitor
from ..presentation.renderers.game_start import render_game_start
from ..presentation.renderers.game_end import render_game_end
from ..presentation.renderers.rank import render_rank_image
from ..domain.ranking.push_scopes import build_rank_push_scopes
from PIL import Image as PILImage
import io
from datetime import datetime, timedelta
import requests  # 新增导入
import tempfile
import traceback
import shutil
from typing import Any
from ..shared.utils.superpower import load_abilities, get_daily_superpower
from ..presentation.web.admin_api import WebAdminAPI
from ..infrastructure.persistence.plugin_data import PersistenceMixin
from ..infrastructure.clients.qqofficial_panel import QQOfficialPanelClient, QQOfficialPanelError
from ..infrastructure.clients.steam import SteamClientMixin
from ..shared.paths import ABILITIES_PATH, CONFIG_PATH

class SteamStatusMonitorV3(PersistenceMixin, SteamClientMixin, Star):

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        # 插件运行状态标志，重启后自动丢失
        if hasattr(self, '_ssm_running') and self._ssm_running:
            logger.error("当前插件已在运行中。请重启astrbot而非重载插件")
            return
        self._ssm_running = True
        self._plugin_version = "3.3.3"
        self._ensure_fonts()  # 插件启动时自动检测/下载字体
        self.context = context
        # 分群管理：所有状态数据均以 group_id 为 key
        self.group_steam_ids = {}         # {group_id: [steamid, ...]}
        self.group_last_states = {}       # {group_id: {steamid: status}}
        self.group_start_play_times = {}  # {group_id: {steamid: {gameid: start_time}}}
        self.group_last_quit_times = {}   # {group_id: {steamid: {gameid: quit_time}}}
        self.group_pending_logs = {}      # {group_id: {steamid: {gameid: log_dict}}}
        self.group_recent_games = {}      # {group_id: [gameid, ...]}
        self.group_pending_quit = {}      # {group_id: {steamid: {gameid: {quit_time, name, game_name, duration_min, start_time, notified}}}}
        # 超能力缓存和能力列表
        self._superpower_cache = {}  # {(steamid, date): superpower}
        self._abilities = None
        self._abilities_path = str(ABILITIES_PATH)
        self._game_name_cache = {}  # 修复: 游戏名缓存，防止 AttributeError
        # 统一使用 AstrBot 配置系统
        self.config = config or {}
        # 兼容旧逻辑，若 config 为空则尝试读取 config.json（可选，建议后续移除）
        if not self.config:
            try:
                config_path = str(CONFIG_PATH)
                with open(config_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
            except Exception as e:
                logger.error(f"steam_status_monitor 配置读取失败: {e}")
                self.config = {}
        # 旧配置迁移：如存在 steam_ids（未分群），迁移到 group_steam_ids['default']
        if 'steam_ids' in self.config and 'group_steam_ids' not in self.config:
            steam_ids = self.config.get('steam_ids', [])
            if isinstance(steam_ids, str):
                steam_ids = [x.strip() for x in steam_ids.split(',') if x.strip()]
            self.config['group_steam_ids'] = {'default': steam_ids}
            self.config.pop('steam_ids', None)
            logger.info(f"已自动迁移旧 steam_ids 配置到 group_steam_ids['default']")
        # 读取配置项，提供默认值
        self.API_KEY = self.config.get('steam_api_key', '')
        register_sensitive_values(self.API_KEY, self.config.get('sgdb_api_key', ''))
        self.SSL_CA_FILE = self.config.get('ssl_ca_file', '')
        try:
            configure_tls(self.SSL_CA_FILE)
        except ValueError as exc:
            logger.error(f"TLS 配置无效，将使用系统默认信任链: {exc}")
            self.SSL_CA_FILE = ''
            configure_tls()
        # API Base URL（支持自定义，默认官方地址）
        self.STEAM_API_BASE = (self.config.get('steam_api_base', '') or 'https://api.steampowered.com').rstrip('/')
        self.STEAM_STORE_BASE = (self.config.get('steam_store_base', '') or 'https://store.steampowered.com').rstrip('/')
        self.SGDB_API_BASE = (self.config.get('sgdb_api_base', '') or 'https://www.steamgriddb.com').rstrip('/')
        self.group_steam_ids = self.config.get('group_steam_ids', {})
        self.RETRY_TIMES = self.config.get('retry_times', 3)
        # 代理支持（来自 PR #16 by Sodiumsss）
        self.ENABLE_PROXY = self.config.get('enable_proxy', False)
        self.PROXY_URL = self.config.get('proxy_url', '')
        self.proxy = self.PROXY_URL if self.ENABLE_PROXY and self.PROXY_URL else None
        # 代理前置校验：若启用 SOCKS 代理但未安装 socksio，尝试自动安装
        if self.proxy and self.proxy.startswith('socks'):
            try:
                import socksio
            except ImportError:
                logger.info(f'[SteamStatusMonitor] 检测到 SOCKS 代理 ({self.proxy})，socksio 未安装，尝试自动安装...')
                import subprocess, sys
                try:
                    subprocess.check_call(
                        [sys.executable, '-m', 'pip', 'install', 'httpx[socks]', '-q'],
                        timeout=60
                    )
                    import socksio
                    logger.info('[SteamStatusMonitor] socksio 自动安装成功')
                except Exception as ie:
                    logger.error(
                        f'[SteamStatusMonitor] socksio 自动安装失败: {ie}。'
                        f'请手动执行: pip install httpx[socks]'
                    )
        self.max_group_size = self.config.get('max_group_size', 20)
        self.GROUP_ID = None  # 当前操作群号，指令时动态赋值
        self.fixed_poll_interval = self.config.get('fixed_poll_interval', 0)  # 新增：固定轮询间隔，0为智能轮询
        self.poll_interval_mid_sec = self.config.get('poll_interval_mid_sec', 600)  # 10分钟
        self.poll_interval_long_sec = self.config.get('poll_interval_long_sec', 1800)  # 30分钟
        self.next_poll_time = {}  # {group_id: {steamid: next_time}}
        self.detailed_poll_log = self.config.get('detailed_poll_log', True)
        # 新增：智能轮询间隔配置 [游戏中, 12分钟内, 12分钟~3小时, 3小时~24小时, 24~48小时, 超过48小时]
        raw_intervals = self.config.get('smart_poll_intervals', "1,3,5,10,20,30")
        if isinstance(raw_intervals, str):
            self.smart_poll_intervals = [int(x.strip()) for x in raw_intervals.split(",") if x.strip()]
        else:
            self.smart_poll_intervals = list(raw_intervals)
        # 归一化回字符串写入 config，防止 WebUI schema 校验类型错误
        self.config['smart_poll_intervals'] = ",".join(str(x) for x in self.smart_poll_intervals)
        # 数据持久化目录
        self.data_dir = os.path.join("data", "steam_status_monitor")
        os.makedirs(self.data_dir, exist_ok=True)
        self._load_group_steam_ids()  # 新增：优先从 steam_groups.json 加载
        self._load_persistent_data()
        self._load_notify_session()
        # 成就监控
        self.achievement_monitor = AchievementMonitor(self.data_dir, steam_api_base=self.STEAM_API_BASE, proxy=self.proxy)
        self.max_achievement_notifications = self.config.get('max_achievement_notifications', 5)
        self.achievement_poll_tasks = {}  # {(group_id, sid, gameid): asyncio.Task}
        self.achievement_snapshots = {}   # {(group_id, sid, gameid): [成就列表]}
        self.achievement_blacklist = set()  # 新增：成就查询黑名单
        self.achievement_fail_count = {}    # 新增：成就查询失败计数
        # --- 新增：重启后自动推送 ---
        self.running_groups = set()  # 正在运行的群号集合
        self.group_monitor_enabled = {}      # {group_id: bool} 监控开关
        self.group_achievement_enabled = {}  # {group_id: bool} 成就推送开关
        self._qq_menu_lock = asyncio.Lock()
        self._platform_id = None  # 记录消息平台ID，用于WebUI自动补全通知目标
        # --- WebUI 群自动补全 notify_sessions ---
        self._auto_fill_notify_sessions()
        # --- 新增：重启后自动恢复所有群的轮询 ---
        if hasattr(self, 'notify_sessions') and self.notify_sessions and self.API_KEY and self.group_steam_ids:
            logger.info(f"[SteamStatusMonitor] 检测到 notify_sessions={self.notify_sessions}，自动启动监控轮询")
            for group_id in self.notify_sessions:
                if group_id in self.group_steam_ids:
                    self.running_groups.add(group_id)
        # --- 新增：全局日志收集与统一输出 ---
        self._last_round_logs = []  # [(group_id, logstr)]
        # --- 新增：持久化数据脏标志 + 节流保存，避免高频写盘拖慢主循环 ---
        self._data_dirty = False          # 有变更待保存
        self._last_save_time = time.time() # 上次保存时间戳
        self._save_interval = 300          # 节流间隔（秒），300秒=5分钟
        # 保存任务引用，便于 terminate 时取消，防止重载/禁用后残留多实例并发
        self._poll_loop_task = asyncio.create_task(self.global_poll_and_log_loop())
        self._init_poll_task = asyncio.create_task(self.init_poll_time_once())
        # SGDB API Key 可在 https://www.steamgriddb.com/profile/preferences/api 获取
        self.SGDB_API_KEY = self.config.get('sgdb_api_key', '')
        self._load_push_groups()  # <--- 修复：确保push_groups属性初始化
        # --- 排行榜功能：游玩时长记录 + 去重缓存 + 每日推送开关 ---
        self.play_records = {}              # {date_str: {steamid: {gameid: {name, minutes}}}}
        self.session_records = {}           # {steamid: [session_dict]} 甘特图/热力图数据
        self._session_dirty = False         # session 数据脏标志
        self._recorded_quit_cache = {}      # {(steamid, gameid): timestamp} 去重用
        self.rank_push_groups = []          # 开启了每日排行榜推送的群列表
        self.rank_push_all = False           # True=全群统一推送全局排行（只渲染一次）
        self.rank_push_hour = self.config.get('rank_push_hour', 8)
        self.rank_push_minute = self.config.get('rank_push_minute', 30)
        self._last_rank_push_date = None    # 记录上次推送日期，防止同一天重复推送
        self._load_play_records()
        self._load_session_records()
        self._load_rank_push_groups()
        # QQ-SteamID 绑定数据
        self._bind_data = {}  # {qq: {sid, nickname}}
        self._load_bind_data()
        # --- 通知合并缓冲区：_delayed_quit_check 到期后将通知写入此队列，由主轮询统一 flush ---
        self._pending_end_notifications = {}  # {group_id: [notification_dict, ...]}
        # --- AstrBot Plugin Pages 管理后台 ---
        self.web_api = WebAdminAPI(self)
        self.web_api.register_routes(context)
        logger.info("[WebAdmin] 管理页面已注册到 AstrBot 内置 WebUI")

    async def init_poll_time_once(self):
        '''插件启动后10秒内进行一次全员初始化轮询，设置每个SteamID的next_poll_time，并输出一次初始日志'''
        await asyncio.sleep(10)
        all_logs = []
        for group_id in self.group_steam_ids:
            steam_ids = self.group_steam_ids[group_id]
            group_lines = []
            for sid in steam_ids:
                # 状态基线按群保存；即使同一玩家属于多个群，也必须逐群初始化。
                msg = await self.check_status_change(group_id, single_sid=sid, skip_push=True)
                if msg:
                    group_lines.append(msg)
            if group_lines:
                all_logs.append(f"群{group_id}：\n" + "\n".join(group_lines))
        if all_logs:
            logger.info("====== Steam状态监控初始化日志 ======\n" + "\n".join(all_logs) + "\n=====================================================")

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
                    sids_to_query = [sid for sid in steam_ids if now2 >= next_poll.get(sid, 0)]
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
                if not group_sids:
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

    async def terminate(self):
        '''插件被卸载/停用时取消所有后台任务并保存持久化数据'''
        # 取消主轮询循环和初始化任务，防止重载/禁用后残留多实例并发
        for t in (getattr(self, '_poll_loop_task', None), getattr(self, '_init_poll_task', None)):
            if t and not t.done():
                t.cancel()
        # 取消所有延迟退出检查任务
        if hasattr(self, '_pending_quit_tasks'):
            for task in self._pending_quit_tasks.values():
                task.cancel()
            self._pending_quit_tasks.clear()
        # 停止所有成就定时任务
        for task in self.achievement_poll_tasks.values():
            task.cancel()
        self.achievement_poll_tasks.clear()
        self.achievement_snapshots.clear()
        # 保存持久化数据（强制落盘，不节流）
        self._save_persistent_data(force=True)
        # 重置运行标志，允许下次重载正常初始化
        self._ssm_running = False

    def crop_image_auto(self, img_path_or_bytes, bg_color=(20,26,33), threshold=25):
        """
        自动裁剪图片内容区域，去除边缘与 bg_color 相近的空白。
        支持本地路径、bytes、URL、PIL.Image。
        """
        import numpy as np
        # 新增：如果已经是PIL.Image对象，直接用
        if isinstance(img_path_or_bytes, PILImage.Image):
            img = img_path_or_bytes.convert("RGB")
        elif isinstance(img_path_or_bytes, str) and (img_path_or_bytes.startswith("http://") or img_path_or_bytes.startswith("https://")):
            resp = requests.get(img_path_or_bytes, timeout=15, verify=requests_verify())
            resp.raise_for_status()
            img = PILImage.open(io.BytesIO(resp.content)).convert("RGB")
        elif isinstance(img_path_or_bytes, bytes):
            img = PILImage.open(io.BytesIO(img_path_or_bytes)).convert("RGB")
        else:
            img = PILImage.open(img_path_or_bytes).convert("RGB")
        arr = np.array(img)
        # 自动检测背景色（取四角平均色）
        h, w, _ = arr.shape
        corners = [arr[0,0], arr[0,-1], arr[-1,0], arr[-1,-1]]
        avg_bg = np.mean(corners, axis=0)
        # 计算每个像素与背景色的距离
        diff = np.abs(arr - avg_bg).sum(axis=2)
        mask = diff > threshold
        coords = np.argwhere(mask)
        if coords.size == 0:
            return img
        y0, x0 = coords.min(axis=0)
        y1, x1 = coords.max(axis=0) + 1
        # 防止裁剪过度，留出2px边距
        y0 = max(y0 - 0, 0)
        x0 = max(x0 - 0, 0)
        y1 = min(y1 - 0, arr.shape[0])
        x1 = min(x1 - 0, arr.shape[1])
        cropped = img.crop((x0, y0, x1, y1))
        return cropped


    async def achievement_periodic_check(self, group_id, sid, gameid, player_name, game_name):
        '''每20分钟对比一次成就列表，直到游戏结束，失败多次自动加入黑名单'''
        key = (group_id, sid, gameid)
        try:
            while True:
                await asyncio.sleep(1200)  # 20分钟
                # 黑名单跳过
                if gameid in self.achievement_blacklist:
                    logger.info(f"[成就定时对比] 游戏 {gameid} 已在黑名单，跳过轮询")
                    break
                achievements_a = self.achievement_snapshots.get(key)
                achievements_b = await self.achievement_monitor.get_player_achievements(
                    self.API_KEY, group_id, sid, gameid
                )
                # 新增：当天失败次数统计
                today = time.strftime('%Y-%m-%d')
                fail_key = (gameid, today)
                if achievements_b is None:
                    cnt = self.achievement_fail_count.get(fail_key, 0) + 1
                    self.achievement_fail_count[fail_key] = cnt
                    if cnt >= 10:
                        self.achievement_blacklist.add(gameid)
                        logger.info(f"[成就黑名单] 游戏 {gameid} 当天累计获取失败10次，已加入黑名单")
                        break
                    continue
                # 修正：补充新成就检测逻辑
                if achievements_a is not None and achievements_b is not None:
                    new_achievements = set(achievements_b) - set(achievements_a)
                    if new_achievements:
                        logger.info(f"[成就定时对比] {player_name} 在 {game_name} 解锁新成就：{', '.join(new_achievements)}")
                        await self.notify_new_achievements(group_id, sid, player_name, gameid, game_name, new_achievements)
                        self.achievement_snapshots[key] = list(achievements_b)
                    else:
                        logger.info(f"[成就定时对比] {player_name} 在 {game_name} 未发现新成就")
        except asyncio.CancelledError:
            logger.info(f"[成就定时对比] 任务已取消 group_id={group_id} sid={sid} gameid={gameid}")
        except Exception as e:
            logger.error(f"[成就定时对比] group_id={group_id} sid={sid} gameid={gameid} 异常: {e}")

    async def achievement_delayed_final_check(self, group_id, sid, gameid, player_name, game_name):
        '''游戏结束后延迟5分钟再做一次成就对比，失败多次自动加入黑名单'''
        key = (group_id, sid, gameid)
        await asyncio.sleep(300)  # 5分钟
        # 黑名单跳过
        if gameid in self.achievement_blacklist:
            logger.info(f"[成就结束冗余对比] 游戏 {gameid} 已在黑名单，跳过轮询")
            return
        achievements_a = self.achievement_snapshots.get(key)
        achievements_b = await self.achievement_monitor.get_player_achievements(
            self.API_KEY, group_id, sid, gameid
        )
        today = time.strftime('%Y-%m-%d')
        fail_key = (gameid, today)
        if achievements_b is None:
            cnt = self.achievement_fail_count.get(fail_key, 0) + 1
            self.achievement_fail_count[fail_key] = cnt
            if cnt >= 10:
                self.achievement_blacklist.add(gameid)
                logger.info(f"[成就黑名单] 游戏 {gameid} 当天累计获取失败10次，已加入黑名单")
                return
        if achievements_a is not None and achievements_b is not None:
            new_achievements = set(achievements_b) - set(achievements_a)
            if new_achievements:
                logger.info(f"[成就结束冗余对比] {player_name} 在 {game_name} 解锁新成就：{', '.join(new_achievements)}")
                await self.notify_new_achievements(group_id, sid, player_name, gameid, game_name, new_achievements)
            else:
                logger.info(f"[成就结束冗余对比] {player_name} 在 {game_name} 未发现新成就")
        # 清理快照和定时任务
        self.achievement_snapshots.pop(key, None)
        self.achievement_poll_tasks.pop(key, None)
        self.achievement_monitor.clear_game_achievements(group_id, sid, gameid)

    async def notify_new_achievements(self, group_id, steamid, player_name, gameid, game_name, new_achievements):
        if not self.group_achievement_enabled.get(group_id, True):
            return
        if not new_achievements or not self.notify_sessions:
            return
        achievements_to_notify = list(new_achievements)[:self.max_achievement_notifications]
        extra_count = len(new_achievements) - len(achievements_to_notify)
        # 优先用缓存
        details = self.achievement_monitor.details_cache.get((group_id, gameid))
        if not details:
            try:
                details = await self.achievement_monitor.get_achievement_details(group_id, gameid, lang="schinese", api_key=self.API_KEY, steamid=steamid)
            except Exception as e:
                details = None
                logger.warning(f"获取成就详情失败: {e}")
        # 在渲染前补充 game_name 字段，确保图片顶部能显示游戏名
        if details and game_name:
            for d in details.values():
                d["game_name"] = game_name
        font_path = self.get_font_path('NotoSansHans-Regular.otf')
        # 推送到主群和所有push_group
        notify_sessions = []
        notify_session = getattr(self, 'notify_sessions', {}).get(group_id, None)
        if notify_session:
            notify_sessions.append(notify_session)
        for push_gid in self.push_groups.get(steamid, []):
            push_session = getattr(self, 'notify_sessions', {}).get(push_gid, None)
            if push_session and push_session not in notify_sessions:
                notify_sessions.append(push_session)
        # 图片推送（受 notify_send_image 开关控制）
        send_image = self.config.get('notify_send_image', True)
        tmp_path = None
        if send_image and details:
            unlocked_set = await self.achievement_monitor.get_player_achievements(self.API_KEY, group_id, steamid, gameid)
            if not unlocked_set:
                key = (group_id, steamid, gameid)
                unlocked_set = set(self.achievement_snapshots.get(key, []))
            if unlocked_set is None:
                unlocked_set = set()
            try:
                img_bytes = await self.achievement_monitor.render_achievement_image(details, set(achievements_to_notify), player_name=player_name, steamid=steamid, appid=gameid, unlocked_set=unlocked_set, font_path=font_path)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    tmp.write(img_bytes)
                    tmp_path = tmp.name
            except Exception as e:
                import traceback
                logger.error(f"成就图片渲染失败: {e}\n{traceback.format_exc()}")
        # 成就通知只发送图片，不发送文字
        if not tmp_path:
            return  # 图片渲染失败则不发送
        for session in notify_sessions:
            try:
                msg_chain = [Image.fromFileSystem(tmp_path)]
                await self.context.send_message(session, MessageChain(msg_chain))
            except Exception as e:
                logger.error(f"发送成就通知失败: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam on")
    async def steam_on(self, event: AstrMessageEvent):
        '''手动启动Steam状态监控轮询（分群）'''
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        self.group_monitor_enabled[group_id] = True
        if not self.API_KEY:
            yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
            return
        steam_ids = self.group_steam_ids.get(group_id, [])
        if not steam_ids or not any(isinstance(x, str) and x.strip() for x in steam_ids):
            yield event.plain_result(
                "未设置监控的 SteamID 列表，请先在插件配置中填写 steam_ids，"
                "或使用 /steam addid [SteamID] 添加要监控的玩家。"
            )
            return
        if group_id in self.running_groups:
            yield event.plain_result("本群Steam监控已在运行。")
            return
        self.running_groups.add(group_id)
        if not hasattr(self, 'notify_sessions'):
            self.notify_sessions = {}
        self.notify_sessions[group_id] = event.unified_msg_origin
        self._record_platform_id(event)
        self._save_notify_session()
        # 初始化状态
        now = int(time.time())
        if group_id not in self.group_last_states:
            self.group_last_states[group_id] = {}
        if group_id not in self.group_start_play_times:
            self.group_start_play_times[group_id] = {}
        # 批量查询所有玩家状态，减少API调用
        status_map = await self.fetch_player_statuses_batch(steam_ids) if steam_ids else {}
        for sid in steam_ids:
            status = status_map.get(sid)
            if status:
                self.group_last_states[group_id][sid] = status
                if status.get('gameid'):
                    prev = self.group_last_states[group_id].get(sid)
                    prev_gameid = prev.get('gameid') if prev else None
                    if prev_gameid and prev_gameid == status.get('gameid') and sid in self.group_start_play_times[group_id]:
                        pass
                    else:
                        gid = status.get('gameid')
                        if gid:
                            self.group_start_play_times[group_id].setdefault(sid, {})[gid] = int(time.time())
        yield event.plain_result("本群Steam状态监控启动完成喔！ヾ(≧ω≦)ゞ")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam addid")
    async def steam_addid(self, event: AstrMessageEvent, steamid: str, at_user: str = "", nickname: str = ""):
        '''添加SteamID到本群监控列表（分群），支持逗号分隔多个ID，支持SteamID/个人资料链接/自定义ID/好友码
        末尾可加 @用户 [备注名] 绑定QQ与SteamID'''
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        # 解析 @用户 [备注名] 后缀（多参数接收，兼容 AstrBot 参数分割）
        bind_qq = None
        bind_nickname = None
        if at_user:
                        m = re.search(r'\[CQ:at,qq=(\d+)\]|\[At:(\d+)\]|@.+?\((\d+)\)|@(\d+)', at_user.strip()); bind_qq = m.group(1) or m.group(2) or m.group(3) or m.group(4) if m else None
        if nickname:
            bind_nickname = nickname.strip()
        # 仅以中英文逗号分隔多个 ID
        import re as _re
        raw_list = [x.strip() for x in _re.split(r'[,，]+', steamid) if x.strip()]
        # 逐个解析为 SteamID64（支持 URL / 自定义 ID / 好友码 / 纯数字）
        resolved_list = []
        invalid_list = []
        for raw in raw_list:
            sid = await self.resolve_steam_input(raw)
            if sid and sid.isdigit() and len(sid) == 17:
                resolved_list.append(sid)
            else:
                invalid_list.append(raw)
        if invalid_list:
            yield event.plain_result(
                f"以下输入无法解析为有效SteamID：{', '.join(invalid_list)}\n"
                f"支持格式：17位SteamID64 / 个人资料链接 / 自定义ID链接 / 8位好友码"
            )
            return
        # 去重
        seen = set()
        steamid_list = []
        for sid in resolved_list:
            if sid not in seen:
                seen.add(sid)
                steamid_list.append(sid)
        steam_ids = self.group_steam_ids.setdefault(group_id, [])
        added = []
        already = []
        limit = self.max_group_size
        for sid in steamid_list:
            if sid in steam_ids:
                already.append(sid)
            elif len(steam_ids) < limit:
                steam_ids.append(sid)
                added.append(sid)
            else:
                break
        self.group_steam_ids[group_id] = steam_ids
        self._save_group_steam_ids()  # 保存到 steam_groups.json
        # 绑定数据：写入并保存
        if bind_qq and steamid_list:
            if not hasattr(self, '_bind_data'):
                self._bind_data = {}
            for sid in steamid_list:
                self._bind_data[bind_qq] = {"sid": sid, "nickname": bind_nickname or "*"}
            self._save_bind_data()
            logger.info(f"[绑定] QQ{ bind_qq} -> SteamID {added[0]}，备注={bind_nickname or '无'}")
        msg = ""
        if added:
            msg += f"已为本群添加SteamID: {', '.join(added)}\n"
        if already:
            msg += f"以下SteamID已存在于本群监控组: {', '.join(already)}\n"
        if len(steam_ids) >= limit and len(added) < len(steamid_list):
            msg += f"本群监控组人数已达上限（{limit}人），部分ID未添加。\n"
        # 自动启用本群监控（幂等）
        if added and group_id not in self.running_groups:
            self.group_monitor_enabled[group_id] = True
            self.running_groups.add(group_id)
            if not hasattr(self, 'notify_sessions'):
                self.notify_sessions = {}
            self.notify_sessions[group_id] = event.unified_msg_origin
            self._record_platform_id(event)
            self._save_notify_session()
            if group_id not in self.group_last_states:
                self.group_last_states[group_id] = {}
            if group_id not in self.group_start_play_times:
                self.group_start_play_times[group_id] = {}
            msg += "监控已自动启动。\n"
        yield event.plain_result(msg.strip() if msg else "未添加任何SteamID。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam delid")
    async def steam_delid(self, event: AstrMessageEvent, steamid: str, group_id_param: str = ""):
        '''从监控组删除SteamID；支持好友码/链接；可选传群号跨群删除：/steam delid [SteamID/好友码/链接] [群号]'''
        group_id = group_id_param.strip() if group_id_param.strip() else (str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default')
        # 支持好友码/链接解析为64位ID
        sid = await self.resolve_steam_input(steamid)
        if not sid or not sid.isdigit() or len(sid) != 17:
            yield event.plain_result("无法解析为有效SteamID，支持格式：17位SteamID64 / 个人资料链接 / 8位好友码")
            return
        steam_ids = self.group_steam_ids.get(group_id, [])
        if not steam_ids:
            yield event.plain_result(f"群 {group_id} 没有监控任何SteamID")
            return
        if sid not in steam_ids:
            yield event.plain_result(f"该SteamID不存在于群 {group_id} 的监控组")
            return
        steam_ids.remove(sid)
        self.group_steam_ids[group_id] = steam_ids
        self._save_group_steam_ids()
        # 同步清理绑定数据
        removed_bind = []
        bind_data = getattr(self, '_bind_data', None)
        if bind_data:
            for qq, info in list(bind_data.items()):
                if info.get("sid") == sid:
                    removed_bind.append(qq)
                    del bind_data[qq]
            if removed_bind:
                self._bind_data = bind_data
                self._save_bind_data()
                logger.info(f"[绑定] 删除SteamID {sid} 时同步清理绑定: QQ {', '.join(removed_bind)}")
        yield event.plain_result(f"已为群 {group_id} 删除SteamID: {sid}")

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("steam list")
    async def steam_list(self, event: AstrMessageEvent):
        '''列出本群所有玩家当前状态（分群）'''
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        steam_ids = self.group_steam_ids.get(group_id, [])
        if not self.API_KEY:
            yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
            return
        if not steam_ids:
            yield event.plain_result("本群未设置监控的 SteamID 列表，请先添加。"); return
        event.group_steam_ids = steam_ids
        font_path = self.get_font_path('NotoSansHans-Regular.otf')
        logger.info(f"[Font] steam_list 渲染传入字体路径: {font_path}")
        # 修改：显式传递 group_id
        async for result in handle_steam_list(self, event, group_id=group_id, font_path=font_path, proxy=self.proxy):
            yield result

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("steam config")
    async def steam_config(self, event: AstrMessageEvent):
        '''显示当前插件配置（敏感信息已隐藏）'''
        lines = []
        hidden_keys = {"steam_api_key", "sgdb_api_key"}
        for k, v in self.config.items():
            if k in hidden_keys:
                lines.append(f"{k}: ****** (已隐藏)")
            else:
                lines.append(f"{k}: {v}")
        # 新增：显示智能轮询间隔说明
        if hasattr(self, "smart_poll_intervals"):
            intervals = self.smart_poll_intervals
            lines.append(f"智能轮询间隔（分钟）: {intervals}（依次为[游戏中, 12分钟内, 12分钟~3小时, 3小时~24小时, 24~48小时, 超过48小时]）")
        yield event.plain_result("当前配置：\n" + "\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam set")
    async def steam_set(self, event: AstrMessageEvent, key: str, value: str):
        '''设置配置参数，立即生效（如 steam set fixed_poll_interval 600）'''
        if key not in self.config:
            yield event.plain_result(f"无效参数: {key}")
            return
        old = self.config[key]
        if key == "smart_poll_intervals":
            # 支持字符串输入
            value_list = [int(x.strip()) for x in value.split(",") if x.strip()]
            value = ",".join(str(x) for x in value_list)
            self.smart_poll_intervals = value_list
        elif isinstance(old, int):
            try:
                value = int(value)
            except Exception:
                yield event.plain_result("类型错误，应为整数")
                return
        elif isinstance(old, float):
            try:
                value = float(value)
            except Exception:
                yield event.plain_result("类型错误，应为浮点数")
                return
        elif isinstance(old, list):
            # 兼容旧格式
            value = [int(x.strip()) for x in value.split(",") if x.strip()]
        self.config[key] = value
        # 同步到属性
        self.API_KEY = self.config.get('steam_api_key', '')
        self.STEAM_IDS = self.config.get('steam_ids', [])
        self.RETRY_TIMES = self.config.get('retry_times', 3)
        self.GROUP_ID = self.config.get('notify_group_id', None)
        self.fixed_poll_interval = self.config.get('fixed_poll_interval', 0)
        # 重新解析智能轮询间隔
        raw_intervals = self.config.get('smart_poll_intervals', "1,3,5,10,20,30")
        if isinstance(raw_intervals, str):
            self.smart_poll_intervals = [int(x.strip()) for x in raw_intervals.split(",") if x.strip()]
        else:
            self.smart_poll_intervals = list(raw_intervals)
        if hasattr(self.config, "save_config"):
            self.config.save_config()
        yield event.plain_result(f"已设置 {key} = {value}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam rs")
    async def steam_rs(self, event: AstrMessageEvent):
        '''清除所有状态并初始化（重启插件用）'''
        self.group_last_states.clear()
        self.group_start_play_times.clear()
        self.group_last_quit_times.clear()
        self.group_pending_logs.clear()
        self.group_pending_quit.clear()
        self.group_recent_games.clear()
        self._superpower_cache.clear()
        self._game_name_cache.clear()
        self.achievement_poll_tasks.clear()
        self.achievement_snapshots.clear()
        self.running_groups.clear()
        self.group_monitor_enabled.clear()
        self.group_achievement_enabled.clear()
        self.notify_sessions = {}
        self._save_persistent_data(force=True)  # 清空后保存
        yield event.plain_result("Steam状态监控插件已重置，所有状态已清空。")

    async def _render_daily_rank_file(self, rank_data):
        """补齐排行榜展示信息并渲染为临时图片。"""
        sid_set = {player["sid"] for player in rank_data}
        sid_info = {}
        if sid_set:
            status_map = await self.fetch_player_statuses_batch(list(sid_set))
            for sid, info in status_map.items():
                sid_info[sid] = {
                    "name": info.get("name") or sid,
                    "avatar_url": info.get("avatarfull") or info.get("avatar"),
                }

        yesterday = self._get_day_key(-1)
        day_data = self.play_records.get(yesterday, {})
        for player in rank_data:
            sid = player["sid"]
            info = sid_info.get(sid, {})
            player["name"] = self._resolve_bind_name(
                sid,
                info.get("name", sid[-8:]),
            )
            player["avatar_url"] = info.get("avatar_url")
            player["top_game_id"] = None
            if not player["games"]:
                continue
            top_name = player["games"][0]["name"]
            for game_id, game_info in day_data.get(sid, {}).items():
                if game_info.get("name") == top_name:
                    player["top_game_id"] = game_id
                    break

        async def cover_fetcher(gameid):
            return await self.get_game_cover_url(gameid)

        avatar_frame_paths = {}
        from ..presentation.renderers.game_start import get_avatar_frame_path, get_avatar_frame_url

        for player in rank_data:
            sid = player.get("sid", "")
            if not sid:
                continue
            frame_path = await get_avatar_frame_path(
                self.data_dir,
                sid,
                proxy=self.proxy,
            )
            if not frame_path:
                frame_url = await get_avatar_frame_url(sid, proxy=self.proxy)
                if frame_url:
                    frame_path = await get_avatar_frame_path(
                        self.data_dir,
                        sid,
                        frame_url,
                        proxy=self.proxy,
                    )
            if frame_path:
                avatar_frame_paths[sid] = frame_path

        font_path = self.get_font_path("NotoSansHans-Regular.otf")
        img_bytes = await render_rank_image(
            self.data_dir,
            rank_data,
            "昨日",
            font_path=font_path,
            proxy=self.proxy,
            cover_fetcher=cover_fetcher,
            avatar_frame_paths=avatar_frame_paths,
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(img_bytes)
            return tmp.name

    async def _daily_rank_push(self, test_mode=False):
        """推送昨日榜单；默认按目标群独立聚合，显式全局模式才共享总榜。"""
        use_global_rank = getattr(self, "rank_push_all", False)
        scopes = build_rank_push_scopes(
            getattr(self, "rank_push_groups", []),
            use_global_rank=use_global_rank,
        )
        if not scopes:
            logger.warning(
                "[排行榜] 没有目标群可推送"
                "（请先使用 /steam rank_on 或 /steam rank_on all 开启推送）"
            )
            return

        rendered_files = {}
        try:
            for target_group_id, data_group_id in scopes:
                render_key = (
                    ("global", None)
                    if data_group_id is None
                    else ("group", data_group_id)
                )
                if render_key not in rendered_files:
                    rank_data = self._get_rank_data(
                        days=1,
                        group_id=data_group_id,
                        base_day_offset=-1,
                    )
                    if not rank_data:
                        scope_label = (
                            "全部群"
                            if data_group_id is None
                            else f"群 {data_group_id}"
                        )
                        logger.info(
                            f"[排行榜] {scope_label}昨日无游玩记录，跳过推送"
                        )
                        rendered_files[render_key] = None
                    else:
                        try:
                            rendered_files[render_key] = (
                                await self._render_daily_rank_file(rank_data)
                            )
                        except Exception as e:
                            logger.error(
                                f"[排行榜] 渲染群 {data_group_id or '全局'} "
                                f"昨日榜单失败: {e}"
                            )
                            rendered_files[render_key] = None

                tmp_path = rendered_files[render_key]
                if not tmp_path:
                    continue
                try:
                    session = getattr(self, "notify_sessions", {}).get(
                        target_group_id
                    )
                    if not session:
                        logger.warning(
                            f"[排行榜] 群 {target_group_id} 未找到推送会话，跳过"
                        )
                        continue
                    await self.context.send_message(
                        session,
                        MessageChain([
                            Plain("📊 昨日游戏时长排行榜来啦！\n"),
                            Image.fromFileSystem(tmp_path),
                        ]),
                    )
                    logger.info(
                        f"[排行榜] 已推送昨日排行榜到群 {target_group_id}"
                    )
                except Exception as e:
                    logger.error(
                        f"[排行榜] 推送群 {target_group_id} 失败: {e}"
                    )
        except Exception as e:
            logger.error(f"[排行榜] 每日推送异常: {e}")
        finally:
            for tmp_path in {
                path for path in rendered_files.values() if path
            }:
                try:
                    os.unlink(tmp_path)
                except OSError as e:
                    logger.warning(
                        f"[排行榜] 清理临时图片失败 {tmp_path}: {e}"
                    )
    async def _render_and_send_rank(self, event, group_id, days, period_label, is_all=False):
        """生成排行榜图片并发送"""
        try:
            rank_data = self._get_rank_data(days=days, group_id=None if is_all else group_id)
            if not rank_data:
                yield event.plain_result(f"暂无{period_label}游玩记录，玩家游戏结束后才会有数据。")
                return
            # 补充玩家昵称和头像URL
            sid_set = {p["sid"] for p in rank_data}
            sid_info = {}
            if sid_set:
                status_map = await self.fetch_player_statuses_batch(list(sid_set))
                for sid, info in status_map.items():
                    sid_info[sid] = {
                        "name": info.get("name") or sid,
                        "avatar_url": info.get("avatarfull") or info.get("avatar")
                    }
            for p in rank_data:
                info = sid_info.get(p["sid"], {})
                p["name"] = self._resolve_bind_name(p["sid"], info.get("name", p["sid"][-8:]))
                p["avatar_url"] = info.get("avatar_url")
                # 标记主玩游戏ID用于封面获取
                if p["games"]:
                    # 需要gameid来获取封面，从play_records中反查
                    p["top_game_id"] = None
            # 从play_records中反查每个玩家top游戏的gameid
            for p in rank_data:
                if not p["games"]:
                    continue
                top_name = p["games"][0]["name"]
                # 在最近数据中找匹配的gameid
                for di in range(days):
                    dk = self._get_day_key(-di)
                    day_data = self.play_records.get(dk, {})
                    sid_games = day_data.get(p["sid"], {})
                    for gid, ginfo in sid_games.items():
                        if ginfo.get("name") == top_name:
                            p["top_game_id"] = gid
                            break
                    if p.get("top_game_id"):
                        break

            # 封面获取回调
            async def cover_fetcher(gameid):
                return await self.get_game_cover_url(gameid)

            # 获取头像框路径
            avatar_frame_paths = {}
            from ..presentation.renderers.game_start import get_avatar_frame_url, get_avatar_frame_path
            for p in rank_data:
                sid = p.get("sid", "")
                if sid:
                    fp = await get_avatar_frame_path(self.data_dir, sid, proxy=self.proxy)
                    if not fp:
                        frame_url = await get_avatar_frame_url(sid, proxy=self.proxy)
                        if frame_url:
                            fp = await get_avatar_frame_path(self.data_dir, sid, frame_url, proxy=self.proxy)
                    if fp:
                        avatar_frame_paths[sid] = fp

            font_path = self.get_font_path('NotoSansHans-Regular.otf')
            img_bytes = await render_rank_image(
                self.data_dir, rank_data, period_label,
                font_path=font_path, proxy=self.proxy,
                cover_fetcher=cover_fetcher,
                avatar_frame_paths=avatar_frame_paths
            )
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            yield event.image_result(tmp_path)
        except Exception as e:
            logger.error(f"[排行榜] 渲染失败: {e}\n{traceback.format_exc()}")
            yield event.plain_result(f"排行榜生成失败: {e}")

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("steam rank")
    async def steam_rank(self, event: AstrMessageEvent, period: str = ""):
        '''查看本群玩家游戏时长排行榜（默认今日，可选 week/month）'''
        group_id = event.get_group_id() or "default"
        period = period.strip().lower()
        if period == "week":
            days, label = 7, "最近7天"
        elif period == "month":
            days, label = 30, "最近30天"
        elif period.isdigit():
            days = int(period)
            if days <= 0:
                days = 1
            label = f"最近{days}天"
        else:
            days, label = 1, "今日"
        async for result in self._render_and_send_rank(event, group_id, days, label, is_all=False):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam allrank")
    async def steam_allrank(self, event: AstrMessageEvent, period: str = ""):
        '''查看所有群玩家游戏时长排行榜（默认今日，可选 week/month）'''
        period = period.strip().lower()
        if period == "week":
            days, label = 7, "最近7天"
        elif period == "month":
            days, label = 30, "最近30天"
        elif period.isdigit():
            days = int(period)
            if days <= 0:
                days = 1
            label = f"最近{days}天"
        else:
            days, label = 1, "今日"
        async for result in self._render_and_send_rank(event, None, days, label, is_all=True):
            yield result

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam rank_on")
    async def steam_rank_on(self, event: AstrMessageEvent, param: str = ""):
        '''每日排行榜推送管理；参数: all=全局排行, list=查看状态, test=即刻推送, del [群号]=删除推送'''
        param = param.strip().lower()
        if param == "list":
            is_all = getattr(self, 'rank_push_all', False)
            groups = list(self.rank_push_groups)
            if groups:
                mode = '全局' if is_all else '分群'
                yield event.plain_result(f"当前排行榜推送模式：{mode}排行，推送群：{', '.join(groups)}")
            else:
                yield event.plain_result("当前未开启任何排行榜推送。使用 /steam rank_on 或 /steam rank_on all 开启。")
            return
        if param == "test":
            yield event.plain_result("正在生成昨日排行榜，稍等...")
            await self._daily_rank_push(test_mode=True)
            return
        if param.startswith("del"):
            parts = param.split()
            if len(parts) >= 2:
                target = parts[1]
            else:
                target = event.get_group_id() or "default"
            if target in self.rank_push_groups:
                self.rank_push_groups.remove(target)
                self._save_rank_push_groups()
                yield event.plain_result(f"已关闭群 {target} 的每日排行榜推送。")
            else:
                yield event.plain_result(f"群 {target} 未在推送列表中。")
            return
        if param == "all":
            self.rank_push_all = True
            group_id = event.get_group_id() or "default"
            if group_id not in self.rank_push_groups:
                self.rank_push_groups.append(group_id)
            self._save_rank_push_groups()
            yield event.plain_result("已开启每日排行榜自动推送（全局排行）")
        else:
            self.rank_push_all = False
            group_id = event.get_group_id() or "default"
            if group_id not in self.rank_push_groups:
                self.rank_push_groups.append(group_id)
                self._save_rank_push_groups()
            yield event.plain_result(f"已开启本群每日排行榜自动推送。")

    def _qq_menu_panel(self) -> dict[str, Any]:
        default_commands = [
            {"command": "/steam help", "description": "查看 Steam 插件指令帮助"},
            {"command": "/steam list", "description": "查看本群玩家当前状态"},
            {"command": "/steam rank", "description": "查看本群今日时长排行"},
            {"command": "/steam rank 7", "description": "查看本群最近七天排行"},
            {"command": "/steam rank 30", "description": "查看本群最近三十天排行"},
        ]
        commands = self.config.get("qq_menu_commands", default_commands)
        items = []
        for command_item in commands:
            command = str(command_item.get("command", "")).strip()
            description = str(command_item.get("description", "")).strip()
            if not command or not description:
                continue
            items.append(
                {
                    "name": command,
                    "desc": description,
                    "type": "command",
                    "only_admin": False,
                }
            )
        return {"remark": "Steam 状态监控", "items": items}

    def _qq_menu_client(self, event: AstrMessageEvent) -> tuple[QQOfficialPanelClient, str]:
        platform_id = event.get_platform_id()
        platform = self.context.get_platform_inst(platform_id)
        platform_type = str(platform.meta().name)
        use_web_config = bool(self.config.get("qq_official_enabled", False))
        if not use_web_config and platform_type not in {"qq_official", "qq_official_webhook"}:
            raise QQOfficialPanelError(
                "请从 QQ 官方机器人平台会话执行该指令，或先启用后台 QQ 官方机器人配置"
            )
        appid = str(
            self.config.get("qq_official_appid", "")
            if use_web_config
            else getattr(platform, "appid", "")
        ).strip()
        secret = str(
            self.config.get("qq_official_secret", "")
            if use_web_config
            else getattr(platform, "secret", "")
        ).strip()
        if not appid or not secret:
            source = "后台 QQ 官方机器人配置" if use_web_config else "QQ 官方机器人平台"
            raise QQOfficialPanelError(f"{source}缺少 appid 或 secret")
        return QQOfficialPanelClient(appid, secret, proxy=self.proxy), platform_type

    async def _save_qq_menu_panel_id(self, panel_id: str) -> None:
        self.config["qq_menu_panel_id"] = panel_id
        save_async = getattr(self.config, "save_config_async", None)
        if callable(save_async):
            await save_async()
            return
        save = getattr(self.config, "save_config", None)
        if callable(save):
            await asyncio.to_thread(save)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam qq菜单同步")
    async def steam_qq_menu_sync(self, event: AstrMessageEvent):
        """创建或更新 QQ 官方机器人指令面板。"""
        if not self.config.get("qq_menu_enabled", False):
            yield event.plain_result("QQ 指令面板未启用，请先在插件配置中开启 qq_menu_enabled。")
            return
        scope = str(self.config.get("qq_menu_scope", "group")).strip().lower()
        if scope not in {"group", "c2c"}:
            yield event.plain_result("qq_menu_scope 仅支持 group 或 c2c。")
            return
        raw_openids = self.config.get("qq_menu_group_openids", [])
        if isinstance(raw_openids, str):
            group_openids = [item.strip() for item in raw_openids.split(",") if item.strip()]
        else:
            group_openids = [str(item).strip() for item in raw_openids if str(item).strip()]
        if scope == "group" and not group_openids:
            platform = self.context.get_platform_inst(event.get_platform_id())
            platform_type = str(platform.meta().name)
            current_group = str(event.get_group_id() or "").strip()
            if platform_type in {"qq_official", "qq_official_webhook"} and current_group:
                group_openids = [current_group]
            else:
                yield event.plain_result(
                    "群聊面板需要目标群 OpenID；当前会话不是 QQ 官方机器人会话，"
                    "请在后台填写 qq_menu_group_openids。"
                )
                return
        try:
            client, _ = self._qq_menu_client(event)
            async with self._qq_menu_lock:
                panel_id = str(self.config.get("qq_menu_panel_id", "")).strip()
                action = "更新"
                if panel_id:
                    try:
                        await client.update_panel(panel_id, self._qq_menu_panel())
                        if scope == "group":
                            await client.update_targets(panel_id, group_openids)
                    except QQOfficialPanelError as exc:
                        if not exc.panel_not_found:
                            raise
                        panel_id = ""
                if not panel_id:
                    panel_id = await client.create_panel(
                        scope=scope,
                        panel=self._qq_menu_panel(),
                        group_openids=group_openids,
                    )
                    await self._save_qq_menu_panel_id(panel_id)
                    action = "创建"
            target = f"{len(group_openids)} 个群" if scope == "group" else "单聊"
            yield event.plain_result(f"QQ 指令面板已{action}：{target}，panel_id={panel_id}")
        except QQOfficialPanelError as exc:
            logger.warning("[SteamStatusMonitor] QQ 指令面板同步失败: %s", exc)
            yield event.plain_result(f"QQ 指令面板同步失败：{exc}")
        except Exception as exc:
            logger.exception("[SteamStatusMonitor] QQ 指令面板同步异常")
            yield event.plain_result(f"QQ 指令面板同步异常：{type(exc).__name__}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam qq菜单状态")
    async def steam_qq_menu_status(self, event: AstrMessageEvent):
        """查询 QQ 官方机器人指令面板状态。"""
        panel_id = str(self.config.get("qq_menu_panel_id", "")).strip()
        if not panel_id:
            yield event.plain_result("尚未记录 QQ 指令面板，请先执行 /steam qq菜单同步。")
            return
        try:
            client, platform_type = self._qq_menu_client(event)
            data = await client.get_panel(panel_id)
            panel = data.get("panel") if isinstance(data.get("panel"), dict) else {}
            items = panel.get("items") if isinstance(panel.get("items"), list) else []
            yield event.plain_result(
                "QQ 指令面板状态：\n"
                f"平台：{platform_type}\n"
                f"场景：{data.get('scope') or self.config.get('qq_menu_scope', 'group')}\n"
                f"面板 ID：{panel_id}\n"
                f"版本：{panel.get('version', '-')}\n"
                f"指令数：{len(items)}"
            )
        except QQOfficialPanelError as exc:
            if exc.panel_not_found:
                await self._save_qq_menu_panel_id("")
                yield event.plain_result("QQ 指令面板已不存在，本地记录已清除，可重新同步。")
                return
            yield event.plain_result(f"查询 QQ 指令面板失败：{exc}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam qq菜单删除")
    async def steam_qq_menu_delete(self, event: AstrMessageEvent):
        """删除本插件记录的 QQ 官方机器人指令面板。"""
        panel_id = str(self.config.get("qq_menu_panel_id", "")).strip()
        if not panel_id:
            yield event.plain_result("尚未记录 QQ 指令面板，无需删除。")
            return
        try:
            client, _ = self._qq_menu_client(event)
            await client.delete_panel(panel_id)
        except QQOfficialPanelError as exc:
            if not exc.panel_not_found:
                yield event.plain_result(f"删除 QQ 指令面板失败：{exc}")
                return
        await self._save_qq_menu_panel_id("")
        yield event.plain_result("QQ 指令面板已删除，本地记录已清除。")

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("steam help")
    async def steam_help(self, event: AstrMessageEvent):
        '''显示所有指令帮助'''
        help_text = (
            "Steam状态监控插件指令：\n"
            "/steam on - 启动监控\n"
            "/steam off - 停止监控\n"
            "/steam list - 列出所有玩家状态\n"
            "/steam config - 查看当前配置\n"
            "/steam set [参数] [值] - 设置配置参数\n"
            "/steam addid [SteamID] - 添加SteamID\n"
            "/steam delid [SteamID] - 删除SteamID\n"
            "/steam push_group [SteamID] - 添加id到联动推送的副群\n"
            "/steam delpush_group [SteamID] [群号可选] - 删除id联动推送的副群，可指定群号\n"
            "/steam openbox [SteamID] - 查看指定SteamID的全部信息\n"
            "/steam rank - 查看本群今日游戏时长排行榜\n"
            "/steam rank 天数 - 查看本群指定天数排行榜（如 7, 30）\n"
            "/steam allrank - 查看所有群今日排行榜\n"
            "/steam allrank 天数 - 查看所有群指定天数排行榜\n"
            "/steam alllist [img|text] - 查看所有群聊玩家状态（默认图片，text 纯文本）\n"
            "/steam rank_on [all|list|test|del] - 管理每日排行榜推送（可配置时间）\n"
            "/steam rank_on list - 查看推送状态\n"
            "/steam rank_on del [群号] - 删除指定群推送（默认本群）\n"
            "/steam rs - 清除状态并初始化\n"
            "/steam help - 显示本帮助\n"
        )
        yield event.plain_result(help_text)

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("steam openbox")
    async def steam_openbox(self, event: AstrMessageEvent, steamid: str):
        '''查询指定SteamID的全部API返回信息'''
        if not self.API_KEY:
            yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
            return
        sid = await self.resolve_steam_input(steamid)
        if not sid or not sid.isdigit() or len(sid) != 17:
            yield event.plain_result("无法解析为有效SteamID，支持格式：17位SteamID64 / 个人资料链接 / 自定义ID链接 / 8位好友码")
            return
        async for result in handle_openbox(self, event, sid):
            yield result

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("steamwho")
    async def steam_who(self, event: AstrMessageEvent, qq: str):
        '''查询指定QQ绑定的Steam玩家状态（ /steamwho @用户 或 /在干嘛 @用户 ）'''

        m = re.search(r'\[CQ:at,qq=(\d+)\]|\[At:(\d+)\]|@.+?\((\d+)\)|@(\d+)', qq.strip()); qq_clean = m.group(1) or m.group(2) or m.group(3) or m.group(4) if m else qq.strip().lstrip('@')
        info = getattr(self, "_bind_data", {}).get(qq_clean)
        if not info:
            yield event.plain_result(f"QQ {qq_clean} 未绑定任何SteamID，请先使用 /steam addid SteamID @{qq_clean}")
            return
        sid = info.get("sid", "")
        if not sid:
            yield event.plain_result(f"QQ {qq_clean} 的绑定数据异常")
            return
        status = await self.fetch_player_status(sid)
        if not status:
            yield event.plain_result(f"无法获取 {sid} 的Steam状态")
            return
        name = self._resolve_bind_name(sid, status.get('name') or sid)
        gameid = status.get('gameid')
        game = status.get('gameextrainfo')
        personastate = status.get('personastate', 0)
        avatar_url = status.get('avatarfull') or status.get('avatar') or ''
        lastlogoff = status.get('lastlogoff')
        zh_game_name = await self.get_chinese_game_name(gameid, game) if gameid else (game or '')
        # 构建单人 user_list
        now = int(time.time())
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        start_play_times = self.group_start_play_times.get(group_id, {}).get(sid, {})
        if gameid:
            start_time = start_play_times.get(gameid) if isinstance(start_play_times, dict) else None
            if not start_time and isinstance(start_play_times, dict) and start_play_times:
                start_time = max(start_play_times.values())
            if not start_time and not isinstance(start_play_times, dict):
                start_time = start_play_times
            play_seconds = now - start_time if start_time else 0
            play_minutes = play_seconds / 60
            play_str = f"{play_minutes/60:.1f}小时" if play_minutes >= 60 else f"{play_minutes:.1f}分钟"
            user_list = [{'sid': sid, 'name': name, 'status': 'playing', 'avatar_url': avatar_url, 'game': zh_game_name, 'gameid': gameid, 'play_str': play_str, 'lastlogoff': lastlogoff}]
        elif personastate and int(personastate) > 0:
            _persona_status = {0: 'offline', 1: 'online', 2: 'busy', 3: 'away', 4: 'snooze'}
            p_status = _persona_status.get(int(personastate), 'online')
            user_list = [{'sid': sid, 'name': name, 'status': p_status, 'avatar_url': avatar_url, 'game': '', 'gameid': '', 'play_str': '', 'lastlogoff': lastlogoff}]
        else:
            hours_ago = (now - int(lastlogoff)) / 3600 if lastlogoff else 0
            play_str = f"上次在线 {hours_ago:.1f}小时前" if lastlogoff else ''
            user_list = [{'sid': sid, 'name': name, 'status': 'offline', 'avatar_url': avatar_url, 'game': '', 'gameid': '', 'play_str': play_str, 'lastlogoff': lastlogoff}]
        # 获取头像框
        from ..presentation.renderers.game_start import get_avatar_frame_url, get_avatar_frame_path
        avatar_frame_paths = {}
        fp = await get_avatar_frame_path(self.data_dir, sid, proxy=self.proxy)
        if not fp:
            frame_url = await get_avatar_frame_url(sid, proxy=self.proxy)
            if frame_url: fp = await get_avatar_frame_path(self.data_dir, sid, frame_url, proxy=self.proxy)
        if fp: avatar_frame_paths[sid] = fp
        # 获取封面
        covers = {}
        if gameid:
            from ..presentation.renderers.game_start import get_cover_path
            cp = await get_cover_path(self.data_dir, gameid, game or zh_game_name, proxy=self.proxy)
            if cp: covers[sid] = cp
        # 渲染列表卡片
        from ..presentation.renderers.steam_list import render_steam_list_image
        font_path = self.get_font_path('NotoSansHans-Regular.otf')
        img_bytes = await render_steam_list_image(self.data_dir, user_list, font_path=font_path, proxy=self.proxy, avatar_frame_paths=avatar_frame_paths, covers=covers)
        if img_bytes:
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            yield event.image_result(tmp_path)
        else:
            yield event.plain_result("渲染图片失败")

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("在干嘛")
    async def steam_zai_gan_ma(self, event: AstrMessageEvent, qq: str):
        '''/在干嘛 @用户 —— steamwho 的别名'''
        async for r in self.steam_who(event, qq):
            yield r

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam off")
    async def steam_off(self, event: AstrMessageEvent):
        '''彻底停止本群Steam状态监控轮询，释放轮询资源'''
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        self.group_monitor_enabled[group_id] = False
        if group_id in self.running_groups:
            self.running_groups.remove(group_id)
        # 清除该群的轮询时间表，停止轮询（/steam on 后会重新初始化）
        self.next_poll_time.pop(group_id, None)
        # 清除待推送的退出缓冲，避免残留延迟任务在停用后推送
        self.group_pending_quit.pop(group_id, None)
        # 取消该群所有成就轮询任务，释放资源
        keys_to_cancel = [k for k in list(self.achievement_poll_tasks.keys()) if k[0] == group_id]
        for key in keys_to_cancel:
            task = self.achievement_poll_tasks.pop(key, None)
            if task:
                task.cancel()
        yield event.plain_result(f"已为本群彻底关闭Steam监控，轮询已停止。使用 /steam on 可重新启动。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam achievement_on")
    async def steam_achievement_on(self, event: AstrMessageEvent):
        """开启本群Steam成就推送"""
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        self.group_achievement_enabled[group_id] = True
        yield event.plain_result(f"已为本群开启Steam成就推送。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam achievement_off")
    async def steam_achievement_off(self, event: AstrMessageEvent):
        """关闭本群Steam成就推送"""
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        self.group_achievement_enabled[group_id] = False
        yield event.plain_result(f"已为本群关闭Steam成就推送。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam test_achievement_render")
    async def steam_test_achievement_render(self, event: AstrMessageEvent, steamid: str, gameid: int, count: int = 3):
        '''测试成就消息渲染效果（steam test_achievement_render [steamid] [gameid] [数量]）'''
        player_name = steamid
        game_name = await self.get_chinese_game_name(gameid)
        group_id = self.GROUP_ID or 'default'
        achievements = await self.achievement_monitor.get_player_achievements(self.API_KEY, group_id, steamid, gameid)
        if not achievements:
            yield event.plain_result("未获取到任何成就，可能为隐私或无成就。")
            return
        details = await self.achievement_monitor.get_achievement_details(group_id, gameid, lang="schinese", api_key=self.API_KEY, steamid=steamid)
        import random
        count = max(1, min(count, len(achievements)))
        unlocked = set(random.sample(list(achievements), count))
        font_path = self.get_font_path('NotoSansHans-Regular.otf')
        # 直接测试 Pillow 渲染
        try:
            img_bytes = await self.achievement_monitor.render_achievement_image(details, unlocked, player_name=player_name, font_path=font_path)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            yield event.image_result(tmp_path)
        except Exception as e:
            import traceback
            logger.error(f"成就图片渲染失败: {e}\n{traceback.format_exc()}")
            # 回退文本
            msg = self.achievement_monitor.render_achievement_message(details, unlocked, player_name=player_name)
            yield event.plain_result(msg)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam test_game_start_render")
    async def test_game_start_render(self, event: AstrMessageEvent, steamid: str, gameid: int):
        '''测试开始游戏图片渲染效果（steam test_game_start_render [steamid] [gameid]）'''
        try:
            status = await self.fetch_player_status(steamid)
            player_name = self._resolve_bind_name(steamid, status.get("name") if status else steamid)
            avatar_url = status.get("avatarfull") or status.get("avatar") or "" if status else ""
            zh_game_name, en_game_name = await self.get_game_names(gameid)
            logger.info(f"[测试开始游戏渲染] steamid={steamid} gameid={gameid} player_name={player_name} avatar_url={avatar_url} zh_game_name={zh_game_name} en_game_name={en_game_name}")
            superpower = self.get_today_superpower(steamid)
            print(f"[superpower] test_game_start_render superpower={superpower}")
            font_path = self.get_font_path('NotoSansHans-Regular.otf')
            online_count = await self.get_game_online_count(gameid)
            img_bytes = await render_game_start(
                self.data_dir, steamid, player_name, avatar_url, gameid, zh_game_name, api_key=self.API_KEY, superpower=superpower, sgdb_api_key=self.SGDB_API_KEY, font_path=font_path, sgdb_game_name=en_game_name, online_count=online_count, appid=gameid
                , proxy=self.proxy, version=self._plugin_version)
            logger.info(f"[测试开始游戏渲染] render_game_start 返回类型: {type(img_bytes)} 长度: {len(img_bytes) if img_bytes else 'None'}")
            if img_bytes:
                import tempfile
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    tmp.write(img_bytes)
                    tmp_path = tmp.name
                img = PILImage.open(tmp_path).convert("RGB")
                cropped_img = self.crop_image_auto(img, bg_color=(51,81,66), threshold=15)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp2:
                    cropped_img.save(tmp2, format="PNG")
                    tmp_path = tmp2.name
                logger.info(f"[测试开始游戏渲染] 已保存裁剪图到 {tmp_path}")
                yield event.image_result(tmp_path)
            else:
                yield event.plain_result("渲染失败，未获取到图片数据。")
        except Exception as e:
            logger.error(f"测试开始游戏图片渲染失败: {e}\n{traceback.format_exc()}")
            yield event.plain_result(f"渲染异常: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam test_game_end_render")
    async def steam_test_game_end_render(self, event: AstrMessageEvent, steamid: str, gameid: int, duration_min: float = 120, end_time: str = None, tip_text: str = None):
        '''测试游戏结束图片渲染（steam test_game_end_render [steamid] [gameid] [时长分钟] [结束时间 可选] [提示 可选]）'''
        try:
            status = await self.fetch_player_status(steamid)
            player_name = self._resolve_bind_name(steamid, status.get("name") if status else steamid)
            avatar_url = status.get("avatarfull") or status.get("avatar") or "" if status else ""
            zh_game_name, en_game_name = await self.get_game_names(gameid)
            logger.info(f"[get_game_names] zh_game_name={zh_game_name}, en_game_name={en_game_name}")  # 新增英文名输出
            from datetime import datetime
            if end_time:
                end_time_str = end_time
            else:
                end_time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
            duration_h = float(duration_min) / 60 if duration_min else 0
            if not tip_text:
                if duration_min < 5:
                    tip_text = "风扇都没转热，主人就结束了？"
                elif duration_min < 10:
                    tip_text = "杂鱼杂鱼~主人你就这水平？"
                elif duration_min < 30:
                    tip_text = "热身一下就结束了？"
                elif duration_min < 60:
                    tip_text = "歇会儿再来，别太累了喵！"
                elif duration_min < 120:
                    tip_text = "沉浸在游戏世界，时间过得飞快喵！"
                elif duration_min < 300:
                    tip_text = "肝到手软了喵！主人不如陪陪咱~"
                elif duration_min < 600:
                    tip_text = "你吃饭了吗？还是说你已经忘了吃饭这件事？"
                elif duration_min < 1200:
                    tip_text = "家里电费都要被你玩光了喵！"
                elif duration_min < 1800:
                    tip_text = "咱都要给你颁发‘不眠猫’勋章了！"
                elif duration_min < 2400:
                    tip_text = "主人你还活着喵？你是不是忘了关电脑呀~"
                else:
                    tip_text = "你已经和椅子合为一体，成为传说中的‘椅子精’了喵！"
            font_path = self.get_font_path('NotoSansHans-Regular.otf')
            img_bytes = await render_game_end(
                self.data_dir, steamid, player_name, avatar_url, gameid, zh_game_name,
                end_time_str, tip_text, duration_h, sgdb_api_key=self.SGDB_API_KEY, font_path=font_path, sgdb_game_name=en_game_name, appid=gameid
            , proxy=self.proxy)
            msg = f"👋 {player_name} 不玩 {zh_game_name} 了\n游玩时间 {duration_h:.1f}小时"
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            yield event.plain_result(msg)
            yield event.image_result(tmp_path)
        except Exception as e:
            import traceback
            logger.error(f"测试游戏结束图片渲染失败: {e}\n{traceback.format_exc()}")
            yield event.plain_result(f"渲染异常: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam清除缓存")
    async def steam_clear_cache(self, event: AstrMessageEvent):
        '''清除所有头像、封面图等图片缓存（慎用）'''
        try:
            cache_dirs = [
                os.path.join(self.data_dir, "avatars"),
                os.path.join(self.data_dir, "covers"),
                os.path.join(self.data_dir, "covers_v"),
            ]
            cleared = []
            for d in cache_dirs:
                if os.path.exists(d):
                    shutil.rmtree(d)
                    cleared.append(d)
            msg = "已清除以下缓存目录：\n" + "\n".join(cleared) if cleared else "未找到任何缓存目录，无需清理。"
            yield event.plain_result(msg)
        except Exception as e:
            yield event.plain_result(f"清除缓存失败: {e}")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam clear_allids")
    async def steam_clear_allids(self, event: AstrMessageEvent):
        '''删除所有群聊的所有已监控SteamID，并清空相关状态数据'''
        self.group_steam_ids.clear()
        self._save_group_steam_ids()  # 新增：保存到 steam_groups.json
        self.group_last_states.clear()
        self.group_start_play_times.clear()
        self.group_last_quit_times.clear()
        self.group_pending_logs.clear()
        self.group_pending_quit.clear()
        self.group_recent_games.clear()
        self._save_persistent_data(force=True)
        self.config['group_steam_ids'] = self.group_steam_ids
        if hasattr(self.config, "save_config"):
            self.config.save_config()
        yield event.plain_result("已删除所有群聊的所有SteamID，相关状态数据已清空。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam clear_groupids")
    async def steam_clear_groupids(self, event: AstrMessageEvent, group_id: str):
        '''删除指定群聊的所有已监控SteamID，并清空相关状态数据'''
        if group_id not in self.group_steam_ids:
            yield event.plain_result(f"群聊 {group_id} 未绑定任何SteamID，无需清理。")
            return
        self.group_steam_ids.pop(group_id, None)
        self._save_group_steam_ids()  # 保存到 steam_groups.json
        self.group_last_states.pop(group_id, None)
        self.group_start_play_times.pop(group_id, None)
        self.group_last_quit_times.pop(group_id, None)
        self.group_pending_logs.pop(group_id, None)
        self.group_pending_quit.pop(group_id, None)
        self.group_recent_games.pop(group_id, None)
        self._save_persistent_data(force=True)
        self.notify_sessions.pop(group_id, None)
        if hasattr(self.config, "save_config"):
            self.config.save_config()
        yield event.plain_result(f"已删除群聊 {group_id} 的所有SteamID，相关状态数据已清空。")

    def _should_skip_game(self, gameid):
        """根据黑白名单配置判断是否应跳过该游戏的监控/播报"""
        if not gameid:
            return False
        mode = self.config.get('game_filter_mode', '全部游戏')
        if mode == '全部游戏':
            return False
        ids_str = self.config.get('game_filter_ids', '')
        if not ids_str or not ids_str.strip():
            return False
        try:
            filter_ids = [x.strip() for x in ids_str.split(',') if x.strip()]
        except Exception:
            return False
        if mode == '白名单':
            return str(gameid) not in filter_ids
        elif mode == '黑名单':
            return str(gameid) in filter_ids
        return False

    def _get_day_key(self, offset_days=0):
        """基于凌晨4:00边界的日期键
        offset_days=0: 当前所处"天"的日期键
        offset_days=-1: 前一天的日期键
        """
        now = datetime.now()
        if now.hour < 4:
            now = now - timedelta(days=1)
        now = now + timedelta(days=offset_days)
        return now.strftime("%Y-%m-%d")

    def _get_rank_data(self, days=1, group_id=None, base_day_offset=0):
        """聚合游玩时长数据，返回已排序的排行榜列表
        Args:
            days: 1=今日, 7=最近7天, 30=最近30天
            group_id: 指定群则只统计该群的SteamID，None则统计全部
        Returns:
            [{sid, name, total_minutes, games: [{name, minutes}]}] 按总时长降序
        """
        try:
            today_str = self._get_day_key(base_day_offset)
            base_date = datetime.strptime(today_str, "%Y-%m-%d")
            date_keys = []
            for i in range(days):
                d = base_date - timedelta(days=i)
                date_keys.append(d.strftime("%Y-%m-%d"))
            # 确定要统计的 SteamID 集合
            if group_id:
                target_sids = set(self.group_steam_ids.get(group_id, []))
            else:
                target_sids = set()
                for gids in self.group_steam_ids.values():
                    target_sids.update(gids)
            if not target_sids:
                return []
            # 聚合
            merged = {}  # {sid: {gameid: {name, minutes}}}
            for date_key in date_keys:
                day_data = self.play_records.get(date_key, {})
                for sid, games in day_data.items():
                    if sid not in target_sids:
                        continue
                    if sid not in merged:
                        merged[sid] = {}
                    for gid, info in games.items():
                        # 防御性清洗：name 可能被缓存污染为 tuple/list
                        raw_name = info.get("name", "未知游戏")
                        if isinstance(raw_name, (tuple, list)):
                            raw_name = raw_name[0] if raw_name else "未知游戏"
                        raw_name = str(raw_name) if raw_name else "未知游戏"
                        if gid not in merged[sid]:
                            merged[sid][gid] = {"name": raw_name, "minutes": 0}
                        merged[sid][gid]["minutes"] += info.get("minutes", 0)
                        merged[sid][gid]["name"] = info.get("name", merged[sid][gid]["name"])
            # 构建排行榜列表
            rank_list = []
            for sid, games in merged.items():
                total = sum(g["minutes"] for g in games.values())
                if total <= 0:
                    continue
                game_list = sorted(
                    [{"name": g["name"], "minutes": g["minutes"]} for g in games.values()],
                    key=lambda x: x["minutes"],
                    reverse=True
                )
                rank_list.append({
                    "sid": sid,
                    "name": game_list[0]["name"] if game_list else sid,  # 临时用游戏名占位，后续替换为玩家名
                    "total_minutes": total,
                    "games": game_list
                })
            rank_list.sort(key=lambda x: x["total_minutes"], reverse=True)
            return rank_list
        except Exception as e:
            logger.error(f"[排行榜] 聚合数据异常: {e}")
            return []

    def _record_playtime(self, sid, gameid, game_name, duration_min):
        """记录游玩时长到 play_records，带5分钟去重（防止多群重复记录）"""
        try:
            if duration_min <= 0 or not gameid:
                return
            # 防御性清洗：确保 game_name 是字符串（可能被缓存污染为 tuple/list）
            if isinstance(game_name, (tuple, list)):
                game_name = game_name[0] if game_name else "未知游戏"
            game_name = str(game_name) if game_name else "未知游戏"
            cache_key = (str(sid), str(gameid))
            now = time.time()
            last_ts = self._recorded_quit_cache.get(cache_key, 0)
            if now - last_ts < 300:
                logger.debug(f"[排行榜] 去重跳过: {sid} {game_name} (上次记录{int(now-last_ts)}秒前)")
                return
            self._recorded_quit_cache[cache_key] = now
            today_key = self._get_day_key(0)
            if today_key not in self.play_records:
                self.play_records[today_key] = {}
            if str(sid) not in self.play_records[today_key]:
                self.play_records[today_key][str(sid)] = {}
            gid = str(gameid)
            if gid not in self.play_records[today_key][str(sid)]:
                self.play_records[today_key][str(sid)][gid] = {"name": game_name, "minutes": 0}
            self.play_records[today_key][str(sid)][gid]["minutes"] += int(duration_min)
            self.play_records[today_key][str(sid)][gid]["name"] = game_name
            self._data_dirty = True
            logger.info(f"[排行榜] 记录游玩时长: {sid} {game_name} +{int(duration_min)}分钟")
            # 清理过期的去重缓存（超过10分钟）
            expired = [k for k, v in self._recorded_quit_cache.items() if now - v > 600]
            for k in expired:
                self._recorded_quit_cache.pop(k, None)
        except Exception as e:
            logger.error(f"[排行榜] 记录游玩时长异常: {e}")

    def _get_notify_sessions(self, group_id, sid):
        """获取需要通知的所有 session（主群 + 联动群），去重返回"""
        sessions = []
        ns = getattr(self, 'notify_sessions', {}).get(group_id, None)
        if ns and ns not in sessions:
            sessions.append(ns)
        for push_gid in self.push_groups.get(sid, []):
            push_session = getattr(self, 'notify_sessions', {}).get(push_gid, None)
            if push_session and push_session not in sessions:
                sessions.append(push_session)
        return sessions

    async def _render_notification_image(self, noti):
        """为单条通知渲染图片（开始游戏 / 结束游戏），返回临时文件路径或 None"""
        try:
            if noti["type"] == "start":
                status = noti.get("status", {})
                avatar_url = status.get("avatarfull") or status.get("avatar")
                superpower = self.get_today_superpower(noti["sid"])
                font_path = self.get_font_path('NotoSansHans-Regular.otf')
                zh_game_name, en_game_name = await self.get_game_names(noti["gameid"], noti["game"])
                online_count = await self.get_game_online_count(noti["gameid"])
                img_bytes = await render_game_start(
                    self.data_dir, noti["sid"], noti["name"], avatar_url,
                    noti["gameid"], zh_game_name,
                    api_key=self.API_KEY, superpower=superpower,
                    sgdb_api_key=self.SGDB_API_KEY, font_path=font_path,
                    sgdb_game_name=en_game_name, online_count=online_count,
                    appid=noti.get("gameid"), proxy=self.proxy,
                    version=self._plugin_version)
            else:
                from datetime import datetime
                end_time_str = datetime.fromtimestamp(noti["quit_time"]).strftime("%Y-%m-%d %H:%M")
                duration_h = noti["duration_min"] / 60 if noti["duration_min"] > 0 else 0
                avatar_url = noti.get("avatar_url")
                tip_text = noti.get("tip_text") or "你已经和椅子合为一体，成为传说中的'椅子精'了喵！"
                zh_game_name, en_game_name = await self.get_game_names(noti["gameid"], noti["game"])
                font_path = self.get_font_path('NotoSansHans-Regular.otf')
                img_bytes = await render_game_end(
                    self.data_dir, noti["sid"], noti["name"], avatar_url,
                    noti["gameid"], zh_game_name,
                    end_time_str, tip_text, duration_h,
                    sgdb_api_key=self.SGDB_API_KEY, font_path=font_path,
                    sgdb_game_name=en_game_name, appid=noti.get("gameid"),
                    proxy=self.proxy)
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                return tmp.name
        except Exception as e:
            logger.error(f"渲染通知图片失败 ({noti.get('type')}, {noti.get('name')}): {e}")
            return None

    async def _send_merged_notification(self, group_id, notifications):
        """按接收会话过滤通知后合并发送，避免联动群收到无关玩家消息。"""
        if not notifications:
            return
        send_text = self.config.get('notify_send_text', True)
        send_image = self.config.get('notify_send_image', True)
        session_notifications = {}
        for notification in notifications:
            for session in self._get_notify_sessions(group_id, notification["sid"]):
                session_notifications.setdefault(session, []).append(notification)

        for session, matched_notifications in session_notifications.items():
            msg_chain = []
            for notification in matched_notifications:
                if notification["type"] == "start":
                    line = f"🟢【{notification['name']}】开始游玩 {notification['game']}\n"
                else:
                    line = f"👋 {notification['name']} 不玩 {notification['game']}，游玩时间 {notification['duration_str']}\n"
                if send_text:
                    msg_chain.append(Plain(line))
                if send_image:
                    img_path = await self._render_notification_image(notification)
                    if img_path:
                        msg_chain.append(Image.fromFileSystem(img_path))
            if not msg_chain:
                continue
            try:
                await self.context.send_message(session, MessageChain(msg_chain))
            except Exception:
                logger.exception(f"推送合并通知失败 (group_id={group_id}, session={session})")

    async def _flush_pending_end_notifications(self):
        """将 _pending_end_notifications 缓冲区中的延迟退出通知按群合并后发送，发送完毕后清空"""
        if not self._pending_end_notifications:
            return
        for group_id, notifications in list(self._pending_end_notifications.items()):
            await self._send_merged_notification(group_id, notifications)
        self._pending_end_notifications.clear()

    async def _delayed_quit_check(self, group_id, sid, gameid):
        await asyncio.sleep(180)
        info = self.group_pending_quit.get(group_id, {}).get(sid, {}).get(gameid)
        if info and not info.get("notified"):
            duration_min = info["duration_min"]
            if duration_min == 0:
                for _ in range(2):
                    last_quit_time = info["quit_time"]
                    start_time = info["start_time"]
                    if start_time and last_quit_time:
                        duration_min = (last_quit_time - start_time) / 60
                        if duration_min > 0:
                            info["duration_min"] = duration_min
                            break
                    await asyncio.sleep(1)
            info["notified"] = True
            # >>> 排行榜数据采集：记录本次游玩时长（在推送/return之前执行，确保即使关闭通知也能记录）<<<
            self._record_playtime(sid, gameid, info.get("game_name", "未知游戏"), info.get("duration_min", 0))
            # >>> Session 级别游玩记录采集（甘特图/热力图数据源）<<<
            self._record_session(
                sid=sid, gameid=gameid, game_name=info.get("game_name", "未知游戏"),
                start_time=info.get("start_time"), end_time=info.get("quit_time"),
                duration_min=info.get("duration_min", 0), group_id=group_id
            )
            # 游戏结束通知开关：关闭则跳过推送，但仍清理成就任务和 pending_quit
            if not self.config.get('enable_game_end_notify', True):
                key = (group_id, sid, gameid)
                poll_task = self.achievement_poll_tasks.pop(key, None)
                if poll_task:
                    poll_task.cancel()
                self.achievement_snapshots.pop(key, None)
                self.achievement_monitor.clear_game_achievements(group_id, sid, gameid)
                self.group_pending_quit.get(group_id, {}).get(sid, {}).pop(gameid, None)
                return
            duration_min = info["duration_min"]
            if duration_min < 60:
                time_str = f"{duration_min:.1f}分钟"
            else:
                time_str = f"{duration_min/60:.1f}小时"
            # 获取头像 URL 和提示词（供后续图片渲染时使用）
            from datetime import datetime
            if duration_min < 5:
                tip_text = "风扇都没转热，主人就结束了？"
            elif duration_min < 10:
                tip_text = "杂鱼杂鱼~主人你就这水平？"
            elif duration_min < 30:
                tip_text = "热身一下就结束了？"
            elif duration_min < 60:
                tip_text = "歇会儿再来，别太累了喵！"
            elif duration_min < 120:
                tip_text = "沉浸在游戏世界，时间过得飞快喵！"
            elif duration_min < 300:
                tip_text = "肝到手软了喵！主人不如陪陪咱~"
            elif duration_min < 600:
                tip_text = "你吃饭了吗？还是说你已经忘了吃饭这件事？"
            elif duration_min < 1200:
                tip_text = "家里电费都要被你玩光了喵！"
            elif duration_min < 1800:
                tip_text = "咱都要给你颁发'不眠猫'勋章了！"
            elif duration_min < 2400:
                tip_text = "主人你还活着喵？你是不是忘了关电脑呀~"
            else:
                tip_text = "你已经和椅子合为一体，成为传说中的'椅子精'了喵！"
            avatar_url = None
            last_state = self.group_last_states.get(group_id, {}).get(sid)
            if last_state:
                avatar_url = last_state.get("avatarfull") or last_state.get("avatar")
            # 写入通知缓冲区，由主轮询统一 flush 合并发送
            self._pending_end_notifications.setdefault(group_id, []).append({
                "type": "end",
                "name": info["name"],
                "game": info["game_name"],
                "duration_str": time_str,
                "sid": sid,
                "gameid": gameid,
                "quit_time": info["quit_time"],
                "duration_min": duration_min,
                "avatar_url": avatar_url,
                "tip_text": tip_text,
            })
            # 三分钟后再关闭成就轮询和清理快照
            key = (group_id, sid, gameid)
            poll_task = self.achievement_poll_tasks.pop(key, None)
            if poll_task:
                poll_task.cancel()
            self.achievement_snapshots.pop(key, None)
            self.achievement_monitor.clear_game_achievements(group_id, sid, gameid)
            self.group_pending_quit.get(group_id, {}).get(sid, {}).pop(gameid, None)

    async def check_status_change(self, group_id, single_sid=None, status_override=None, poll_level=None, skip_push=False):
        '''轮询检测玩家状态变更并推送通知（分群，支持单个sid）
        返回精简日志字符串，不直接打印日志'''
        now = int(time.time())
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
            # --- 退出游戏（缓冲3分钟） ---（含游戏切换：直接切到另一款游戏也会结算上一款时长）
            if prev_gameid and (current_gameid in [None, "", "0"] or current_gameid != prev_gameid):
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
                if not self._should_skip_game(prev_gameid):
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
                    logger.info(f"[游戏过滤] {name} 退出游戏 {zh_prev_game_name}({prev_gameid}) 被跳过（黑白名单过滤）")
                last_quit_times.setdefault(sid, {})[prev_gameid] = now
                last_states[sid] = status
                if current_gameid in [None, "", "0"]:
                    continue  # 纯退出：防止重复推送
                # 游戏切换：不continue，继续执行下方开始游戏逻辑

            # --- 开始游戏/继续游戏（仅当 gameid 变更时推送） ---
            if current_gameid not in [None, "", "0"] and current_gameid != prev_gameid:
                quit_info = pending_quit.setdefault(sid, {}).get(current_gameid)
                # 检查是否为网络波动（3分钟内重启同一游戏）
                if quit_info and now - quit_info["quit_time"] <= 180 and not quit_info.get("notified"):
                    # 只取消当前群对应的延迟任务。
                    task_key = (group_id, sid, current_gameid)
                    pending_task = getattr(self, '_pending_quit_tasks', {}).pop(task_key, None)
                    if pending_task:
                        pending_task.cancel()
                    quit_info["notified"] = True
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
                if not skip_push and self.config.get('enable_game_start_notify', True):
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

            # 智能轮询间隔设置（支持固定间隔）
            next_poll = self.next_poll_time.setdefault(group_id, {})
            import math
            # intervals 提前定义，固定间隔模式下对齐逻辑也需要使用（修复原版 NameError）
            intervals = self.smart_poll_intervals if isinstance(self.smart_poll_intervals, list) and len(self.smart_poll_intervals) == 6 else [1, 3, 5, 10, 20, 30]
            if self.fixed_poll_interval and self.fixed_poll_interval > 0:
                poll_interval = self.fixed_poll_interval
                poll_level_str = f"固定{self.fixed_poll_interval//60 if self.fixed_poll_interval>=60 else self.fixed_poll_interval}{'分钟' if self.fixed_poll_interval>=60 else '秒'}轮询"
            else:
                # 优先级：游戏中 > 在线 > 离线 > 默认
                if gameid:
                    poll_interval = intervals[0] * 60
                    poll_level_str = f"{intervals[0]}分钟轮询"
                elif personastate and int(personastate) > 0:
                    poll_interval = intervals[1] * 60
                    poll_level_str = f"{intervals[1]}分钟轮询"
                elif lastlogoff:
                    minutes_ago = (now - int(lastlogoff)) / 60
                    if minutes_ago <= 12:
                        poll_interval = intervals[1] * 60
                        poll_level_str = f"{intervals[1]}分钟轮询"
                    elif minutes_ago <= 180:
                        poll_interval = intervals[2] * 60
                        poll_level_str = f"{intervals[2]}分钟轮询"
                    elif minutes_ago <= 1440:
                        poll_interval = intervals[3] * 60
                        poll_level_str = f"{intervals[3]}分钟轮询"
                    elif minutes_ago <= 2880:
                        poll_interval = intervals[4] * 60
                        poll_level_str = f"{intervals[4]}分钟轮询"
                    else:
                        poll_interval = intervals[5] * 60
                        poll_level_str = f"{intervals[5]}分钟轮询"
                else:
                    poll_interval = intervals[5] * 60
                    poll_level_str = f"{intervals[5]}分钟轮询"
            interval_min = poll_interval // 60
            next_time = ((now // 60) + math.ceil(interval_min)) * 60
            if interval_min in [intervals[1], intervals[2], intervals[3], intervals[4], intervals[5]]:
                next_time = ((now // 60) // interval_min + 1) * interval_min * 60
            next_poll[sid] = next_time
            # 轮询间隔描述
            if gameid:
                msg_lines.append(f"🟢【{name}】正在玩 {zh_game_name}（{poll_level_str}）")
            elif personastate and int(personastate) > 0:
                _persona_text = {1: '在线', 2: '忙碌', 3: '离开', 4: '打盹'}
                ptext = _persona_text.get(int(personastate), '在线')
                picon = {1: '🟡', 2: '🔴', 3: '🟣', 4: '🟣'}.get(int(personastate), '🟡')
                msg_lines.append(f"{picon}【{name}】{ptext}（{poll_level_str}）")
            elif lastlogoff:
                hours_ago = (now - int(lastlogoff)) / 3600
                msg_lines.append(f"⚪️【{name}】离线 上次在线 {hours_ago:.1f} 小时前（{poll_level_str}）")
            else:
                msg_lines.append(f"⚪️【{name}】离线（{poll_level_str}）")
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
                    if duration_min < 60:
                        time_str = f"{duration_min:.1f}分钟"
                    else:
                        time_str = f"{duration_min/60:.1f}小时"
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

    async def get_game_online_count(self, gameid):
        '''通过 Steam Web API 获取当前游戏在线人数'''
        if not gameid:
            return None
        url = f"{self.STEAM_API_BASE}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={gameid}"
        try:
            async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(self.proxy)) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get('response', {}).get('player_count')
        except Exception as e:
            logger.warning(f"获取在线人数失败: {e} (gameid={gameid})")
        return None

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam alllist")
    async def steam_alllist(self, event: AstrMessageEvent, mode: str = "img"):
        '''所有群聊玩家状态（默认图片，steam alllist text 输出文本）'''
        _persona_status = {0: 'offline', 1: 'online', 2: 'busy', 3: 'away', 4: 'snooze'}
        from ..presentation.renderers.steam_list import render_steam_list_image
        from ..presentation.renderers.game_start import get_avatar_frame_url, get_avatar_frame_path
        user_list = []
        now = int(time.time())
        # 收集所有SteamID并批量查询，减少API调用
        all_sids = []
        for gid_ in self.group_steam_ids:
            all_sids.extend(self.group_steam_ids[gid_])
        status_map = await self.fetch_player_statuses_batch(all_sids) if all_sids else {}
        for group_id, steam_ids in self.group_steam_ids.items():
            start_play_times = self.group_start_play_times.get(group_id, {})
            next_poll = self.next_poll_time.get(group_id, {})
            for sid in steam_ids:
                nt = next_poll.get(sid, now)
                sl = int(nt - now)
                p_str = f"下次轮询{sl}秒后" if sl < 60 else f"下次轮询{sl//60}分钟后"
                status = status_map.get(sid)
                if not status:
                    user_list.append({'sid': sid, 'name': self._resolve_bind_name(sid, sid), 'status': 'error', 'avatar_url': '', 'game': '', 'gameid': '', 'play_str': '获取失败', 'group_id': group_id, 'poll_str': p_str})
                    continue
                name = status.get('name') or sid
                gameid = status.get('gameid')
                game = status.get('gameextrainfo')
                avatar_url = status.get('avatarfull') or status.get('avatar') or ''
                zh_game_name = await self.get_chinese_game_name(gameid, game) if gameid else (game or "未知游戏")
                if gameid:
                    st = start_play_times.get(sid, {}).get(gameid) if isinstance(start_play_times.get(sid), dict) else start_play_times.get(sid)
                    ps = now - st if st else 0
                    pm = ps / 60
                    ps_str = f"{pm:.1f}分钟" if pm < 60 else f"{pm/60:.1f}小时"
                    user_list.append({'sid': sid, 'name': name, 'status': 'playing', 'avatar_url': avatar_url, 'game': zh_game_name, 'gameid': gameid, 'play_str': ps_str, 'group_id': group_id, 'poll_str': p_str})
                elif status.get('personastate', 0) > 0:
                    p_status = _persona_status.get(status.get('personastate', 0), 'online')
                    user_list.append({'sid': sid, 'name': name, 'status': p_status, 'avatar_url': avatar_url, 'game': '', 'gameid': '', 'play_str': '', 'group_id': group_id, 'poll_str': p_str})
                elif status.get('lastlogoff'):
                    ha = (now - int(status['lastlogoff'])) / 3600
                    user_list.append({'sid': sid, 'name': name, 'status': 'offline', 'avatar_url': avatar_url, 'game': '', 'gameid': '', 'play_str': f"上次在线 {ha:.1f} 小时前", 'group_id': group_id, 'poll_str': p_str})
                else:
                    user_list.append({'sid': sid, 'name': name, 'status': 'offline', 'avatar_url': avatar_url, 'game': '', 'gameid': '', 'play_str': '', 'group_id': group_id, 'poll_str': p_str})
        # 纯文本输出模式
        if mode.lower() == 'text':
            from ..presentation.renderers.steam_list import get_status_text
            lines = ["=== Steam 全群玩家状态 ===\n"]
            by_group = {}
            for u in user_list:
                by_group.setdefault(u.get('group_id', '?'), []).append(u)
            for gid, members in by_group.items():
                lines.append(f"📋 群: {gid}")
                for u in members:
                    sid_shown = u['sid']
                    sicon = {'playing': '🎮', 'online': '🔵', 'offline': '💤',
                             'busy': '🔴', 'away': '🟣', 'snooze': '🟣', 'error': '⚠️'}.get(u['status'], '❓')
                    name = u['name']
                    stext = get_status_text(u['status'])
                    detail = f" 正在玩：{u['game']}" if u['status'] == 'playing' and u.get('game') else ""
                    play = f" | 时长：{u['play_str']}" if u.get('play_str') else ""
                    offline_info = f" | {u['play_str']}" if u['status'] == 'offline' and u.get('play_str') else ""
                    poll = f" | {u.get('poll_str','')}" if u.get('poll_str') else ""
                    lines.append(f"  {sicon} {name} {stext}{detail}{play}{offline_info}")
                    lines.append(f"     ID: {sid_shown}{poll}")
                lines.append("")
            online_count = sum(1 for u in user_list if u['status'] in ('playing','online','away','snooze','busy'))
            lines.append(f"📊 在线: {online_count} / 总数: {len(user_list)}")
            yield event.plain_result("\n".join(lines))
            return
        # 图片输出模式（默认）
        # 按状态排序：游戏中 > 在线 > 忙碌 > 离开/打盹 > 离线 > 异常
        _status_rank = {'playing': 0, 'online': 1, 'busy': 2, 'away': 3, 'snooze': 4, 'offline': 5, 'error': 6}
        user_list.sort(key=lambda u: _status_rank.get(u['status'], 9))
        avatar_frame_paths = {}
        for u in user_list:
            sid = u.get('sid', '')
            if sid:
                fp = await get_avatar_frame_path(self.data_dir, sid, proxy=self.proxy)
                if not fp:
                    frame_url = await get_avatar_frame_url(sid, proxy=self.proxy)
                    if frame_url:
                        fp = await get_avatar_frame_path(self.data_dir, sid, frame_url, proxy=self.proxy)
                if fp:
                    avatar_frame_paths[sid] = fp
        font_path = self.get_font_path('NotoSansHans-Regular.otf')
        # 获取封面
        covers = {}
        for u in user_list:
            gid = u.get('gameid', '')
            if gid:
                from ..presentation.renderers.game_start import get_cover_path
                cp = await get_cover_path(self.data_dir, gid, u.get('game', ''), proxy=self.proxy)
                if cp:
                    covers[u['sid']] = cp
        img_bytes = await render_steam_list_image(self.data_dir, user_list, font_path=font_path, proxy=self.proxy, avatar_frame_paths=avatar_frame_paths, covers=covers)
        if img_bytes:
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            yield event.image_result(tmp_path)
        else:
            yield event.plain_result("渲染图片失败")

    def get_today_superpower(self, steamid):
        from datetime import date
        today = date.today().isoformat()
        cache_key = (steamid, today)
        if cache_key in self._superpower_cache:
            return self._superpower_cache[cache_key]
        if self._abilities is None:
            self._abilities = load_abilities(self._abilities_path)
        superpower = get_daily_superpower(steamid, self._abilities)
        self._superpower_cache[cache_key] = superpower
        return superpower

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam push_group")
    async def steam_push_group(self, event: AstrMessageEvent, steamid: str):
        '''将本群加入指定SteamID的联动推送组（不重复轮询，仅同步推送）'''
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        if not steamid.isdigit() or len(steamid) != 17:
            yield event.plain_result("SteamID无效（需为64位数字串，17位）")
            return
        # 检查主群是否已轮询该SteamID
        found = False
        for gid, ids in self.group_steam_ids.items():
            if steamid in ids:
                found = True
                break
        if not found:
            yield event.plain_result("未找到已轮询该SteamID的主群，请先在任一群添加并开启监控。")
            return
        # 记录推送群
        self.push_groups.setdefault(steamid, [])
        if group_id not in self.push_groups[steamid]:
            self.push_groups[steamid].append(group_id)
            self._save_push_groups()
            yield event.plain_result(f"本群已加入SteamID {steamid} 的联动推送组。")
        else:
            yield event.plain_result("本群已在该SteamID的推送组中。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam delpush_group")
    async def steam_delpush_group(self, event: AstrMessageEvent, steamid: str, target_group: str = ''):
        '''将当前群/指定群从SteamID的联动推送组移除；可传 target_group 指定群号'''
        if target_group:
            group_id = target_group.strip()
        else:
            group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        if not steamid.isdigit() or len(steamid) != 17:
            yield event.plain_result("SteamID无效（需为64位数字串，17位）")
            return
        if steamid not in self.push_groups or group_id not in self.push_groups[steamid]:
            yield event.plain_result(f"群 {group_id} 未在 SteamID {steamid} 的推送组中。")
            return
        self.push_groups[steamid].remove(group_id)
        if not self.push_groups[steamid]:
            self.push_groups.pop(steamid)
        self._save_push_groups()
        if target_group:
            yield event.plain_result(f"已从 SteamID {steamid} 的联动推送组中移除群 {group_id}。")
        else:
            yield event.plain_result(f"本群已从 SteamID {steamid} 的联动推送组移除。")
