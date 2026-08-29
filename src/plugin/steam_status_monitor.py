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
from ..application.services.achievement_tracking import AchievementTrackingMixin
from ..application.services.notification_tracking import NotificationTrackingMixin
from ..application.services.status_change_tracking import StatusChangeTrackingMixin
from ..application.services.polling_tracking import PollingTrackingMixin
from ..presentation.renderers.game_start import render_game_start
from ..presentation.renderers.game_end import render_game_end
from ..presentation.renderers.rank import render_rank_image
from ..presentation.renderers.game_detail import render_game_detail_image
from ..presentation.renderers.game_start import get_font_path
from ..domain.monitoring import MonitorStateStore, StateBackedMonitorMixin
from ..domain.ranking.push_scopes import build_rank_push_scopes
from PIL import Image as PILImage
import io
from datetime import datetime, timedelta, date
import requests  # 新增导入
import tempfile
import traceback
import shutil
from ..presentation.web.admin_api import WebAdminAPI
from ..infrastructure.persistence.plugin_data import PersistenceMixin
from ..infrastructure.clients.steam import SteamClientMixin
from ..infrastructure.clients.itad import ITADClient
from ..application.services.qq_menu_management import QQMenuManagementMixin
from ..shared.paths import ABILITIES_PATH, CONFIG_PATH

# 状态文件最后写入距今超过该秒数（默认 60 分钟），视为插件停止期间遗留的陈旧状态。
# 正常运行时 states.json 约每 5 分钟落盘一次（最慢轮询间隔 30 分钟 + 保存节流 5 分钟），
# 60 分钟阈值足以安全区分"插件停止过"与"正常运行"。
_STALE_STATE_THRESHOLD = 3600


class SteamStatusMonitorV3(
    QQMenuManagementMixin,
    PollingTrackingMixin,
    StatusChangeTrackingMixin,
    NotificationTrackingMixin,
    AchievementTrackingMixin,
    StateBackedMonitorMixin,
    PersistenceMixin,
    SteamClientMixin,
    Star,
):

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.monitor_state = MonitorStateStore()
        # 插件运行状态标志，重启后自动丢失
        if hasattr(self, '_ssm_running') and self._ssm_running:
            logger.error("当前插件已在运行中。请重启astrbot而非重载插件")
            return
        self._ssm_running = True
        self._plugin_version = "4.2.0"
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
        self.ITAD_CLIENT = ITADClient(
            self.config.get('itad_api_key', ''),
            proxy=self.proxy,
            base_url=self.config.get('itad_api_base', ''),
        )
        self._steam_search_cache = {}
        self._steam_search_pending = {}
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
        # --- 插件启动时间戳 + 启动初始化期间的"陈旧群"标记（init 完成后清空） ---
        self._startup_time = time.time()
        self._startup_stale_groups = {}
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

    def _is_group_state_stale(self, group_id, threshold=_STALE_STATE_THRESHOLD):
        """判断该群状态缓存是否为插件停止期间遗留的旧数据。

        依据：states.json 最后写入时间早于本次插件启动，且距今超过阈值（默认 60 分钟）。
        正常运行时 states.json 约每 5 分钟落盘一次（mtime 持续刷新）；插件停止后 mtime
        停在停止时刻。重启后首次初始化期间用它识别"停止期间累积的历史变化"，跳过播报。
        """
        try:
            path = self._get_group_data_path(group_id, "states")
            if not os.path.exists(path):
                return False  # 无缓存文件，无从判断，视为正常
            mtime = os.path.getmtime(path)
            return mtime < self._startup_time and (time.time() - mtime) > threshold
        except Exception as e:
            logger.warning(f"[陈旧状态] 判断 states 新鲜度失败: {e} (group_id={group_id})")
            return False

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
        pushed = []
        already = []
        already_pushed = []
        binding_updated = []
        pushed_primary_groups = {}
        limit = self.max_group_size
        for sid in steamid_list:
            if sid in steam_ids:
                already.append(sid)
                # 已在本群监控：若本次携带绑定/备注，仍允许更新（不直接跳过）
                if bind_qq or bind_nickname:
                    binding_updated.append(sid)
                continue
            primary_group = next(
                (
                    candidate
                    for candidate, candidate_ids in self.group_steam_ids.items()
                    if candidate != group_id and sid in candidate_ids
                ),
                None,
            )
            if primary_group is not None:
                targets = self.push_groups.setdefault(sid, [])
                pushed_primary_groups[sid] = primary_group
                if group_id in targets:
                    already_pushed.append(sid)
                else:
                    targets.append(group_id)
                    pushed.append(sid)
                continue
            if len(steam_ids) < limit:
                steam_ids.append(sid)
                added.append(sid)
            else:
                break
        self.group_steam_ids[group_id] = steam_ids
        if added:
            self._save_group_steam_ids()
        if pushed:
            self._save_push_groups()
        # 绑定数据：写入并保存（已在本群监控的ID同样允许更新备注/绑定）
        if steamid_list and (bind_qq or bind_nickname):
            if not hasattr(self, '_bind_data'):
                self._bind_data = {}
            for sid in steamid_list:
                if bind_qq:
                    self._bind_data[bind_qq] = {"sid": sid, "nickname": bind_nickname or "*"}
                elif bind_nickname:
                    # 未解析到QQ：更新该SteamID已有绑定记录的备注，无记录则以 sid 为键新增
                    matched = False
                    for _key, _info in list(self._bind_data.items()):
                        if _info.get("sid") == str(sid):
                            self._bind_data[_key]["nickname"] = bind_nickname
                            matched = True
                    if not matched:
                        self._bind_data[f"__remark:{sid}"] = {"sid": sid, "nickname": bind_nickname}
            self._save_bind_data()
            logger.info(f"[绑定] {'QQ'+str(bind_qq) if bind_qq else '备注'} -> SteamID {steamid_list[-1]}，备注={bind_nickname or '无'}")
        msg = ""
        if added:
            msg += f"已为本群添加SteamID: {', '.join(added)}\n"
        if pushed:
            push_details = []
            for sid in pushed:
                primary_group = pushed_primary_groups.get(sid)
                suffix = f"（主监控群：{primary_group}）" if primary_group else ""
                push_details.append(f"{sid}{suffix}")
            msg += (
                "以下SteamID已被其他群监控，当前群不会重复监控，已自动设置为分发路由（push_group）："
                f"{', '.join(push_details)}\n"
            )
        if binding_updated:
            msg += f"以下SteamID已在本群监控，备注/绑定已更新：{', '.join(binding_updated)}\n"
        already_plain = [sid for sid in already if sid not in binding_updated]
        if already_plain:
            msg += f"以下SteamID已经在本群监控，无需重复添加：{', '.join(already_plain)}\n"
        if already_pushed:
            push_details = []
            for sid in already_pushed:
                primary_group = pushed_primary_groups.get(sid)
                suffix = f"（主监控群：{primary_group}）" if primary_group else ""
                push_details.append(f"{sid}{suffix}")
            msg += f"以下SteamID已经是本群的分发路由（push_group），无需重复添加：{', '.join(push_details)}\n"
        unhandled = len(steamid_list) - len(added) - len(pushed) - len(already) - len(already_pushed)
        if unhandled:
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
    @filter.command("steam push_group")
    async def steam_push_group(self, event: AstrMessageEvent, steamid: str):
        '''将本群加入指定SteamID的联动推送组（不重复轮询，仅同步推送）'''
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        if not steamid.isdigit() or len(steamid) != 17:
            yield event.plain_result("SteamID无效（需为64位数字串，17位）")
            return
        if not any(steamid in steam_ids for steam_ids in self.group_steam_ids.values()):
            yield event.plain_result("未找到已轮询该SteamID的主群，请先在任一群添加并开启监控。")
            return
        targets = self.push_groups.setdefault(steamid, [])
        if group_id in targets:
            yield event.plain_result("本群已在该SteamID的推送组中。")
            return
        targets.append(group_id)
        self._save_push_groups()
        yield event.plain_result(f"本群已加入SteamID {steamid} 的联动推送组。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam delpush_group")
    async def steam_delpush_group(self, event: AstrMessageEvent, steamid: str, target_group: str = ''):
        '''将当前群或指定群从SteamID的联动推送组移除'''
        group_id = target_group.strip() or (
            str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        )
        targets = self.push_groups.get(steamid, [])
        if group_id not in targets:
            yield event.plain_result(f"群 {group_id} 未在 SteamID {steamid} 的推送组中。")
            return
        targets.remove(group_id)
        if not targets:
            self.push_groups.pop(steamid, None)
        self._save_push_groups()
        yield event.plain_result(f"已从 SteamID {steamid} 的联动推送组中移除群 {group_id}。")

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
        from ..application.services.monitor_admin import MonitorAdminService

        result = MonitorAdminService(self).remove_player(group_id, sid)
        if not result.changed:
            yield event.plain_result(
                f"该SteamID不存在于群 {group_id} 的监控组或分发路由: {sid}"
            )
            return

        if result.message == "removed push route":
            yield event.plain_result(f"已关闭群 {group_id} 对 SteamID {sid} 的分发路由")
        else:
            yield event.plain_result(
                f"已删除 SteamID {sid} 的主监控及全部路由分发"
            )

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("steam game")
    async def steam_game(self, event: AstrMessageEvent, appid: str):
        """查询 Steam 游戏详情并生成详情卡片。"""
        appid = str(appid).strip()
        if not appid.isdigit():
            yield event.plain_result("用法：/steam game <Steam AppID>")
            return
        game = await self.fetch_game_details(appid)
        if not game:
            yield event.plain_result(f"未找到 Steam 游戏 AppID：{appid}，或 Steam 商店暂时无法访问。")
            return
        try:
            img_bytes = await render_game_detail_image(
                game,
                font_path=get_font_path("NotoSansHans-Regular.otf"),
                proxy=self.proxy,
            )
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                image_path = tmp.name
            yield event.image_result(image_path)
        except Exception as exc:
            logger.exception("渲染 Steam 游戏详情卡片失败: %s", exc)
            yield event.plain_result(f"游戏详情获取成功，但卡片生成失败：{exc}")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def steam_price_selection(self, event: AstrMessageEvent):
        """接收候选游戏确认消息，支持纯序号或 @机器人后跟序号。"""
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        if not self._steam_search_pending.get(group_id):
            return
        message = str(event.get_message_str() or '').strip()
        if message.startswith('/'):
            return
        match = re.search(r"(?:^|\s)([1-9]\d?)\s*$", message)
        if not match:
            return
        async for result in self.steam_price(event, match.group(1)):
            yield result

    @staticmethod
    def _contains_chinese(text: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in text)

    async def _translate_game_query(self, query: str) -> str:
        """将中文游戏名转换为 Steam/ITAD 更容易命中的英文官方名。"""
        if not self._contains_chinese(query):
            return query
        try:
            provider = self.context.get_using_provider()
            if not provider:
                return query
            response = await provider.text_chat(
                prompt=(
                    "请将以下游戏名翻译为 Steam 商店使用的英文官方名称，"
                    f"仅输出英文名，不要输出其他内容：{query}"
                ),
                contexts=[],
                image_urls=[],
                func_tool=None,
                system_prompt="",
            )
            translated = re.sub(
                r"^(?:英文名|翻译结果|Translation)\s*[:：]?\s*",
                "",
                str(response.completion_text or "").strip(),
                flags=re.IGNORECASE,
            ).strip().strip('`\"“”')
            if translated:
                logger.info("[LLM][翻译游戏名] %s -> %s", query, translated)
                return translated
        except Exception as exc:
            logger.warning("LLM 翻译游戏名失败，将使用原始查询: %s", exc)
        return query

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("steam price")
    async def steam_price(self, event: AstrMessageEvent, query: str):
        """按中文名、英文名或 Steam 链接查询当前价格与历史最低价。"""
        query = str(query).strip()
        if not query:
            yield event.plain_result("用法：/steam price <游戏名或 Steam 链接>")
            return
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        pending = self._steam_search_pending.get(group_id)
        selected_from_cache = False
        if query.isdigit() and pending:
            index = int(query) - 1
            games = self._steam_search_cache.get(group_id, [])
            if 0 <= index < len(games):
                game = games[index]
                selected_from_cache = True
            else:
                yield event.plain_result("候选序号无效，请重新回复序号。")
                return
        else:
            search_query = await self._translate_game_query(query)
            games = await self.ITAD_CLIENT.search_games(search_query)
            if not games:
                yield event.plain_result("未找到匹配游戏，或 ITAD 暂时无法访问。")
                return
            game = games[0]
        if not selected_from_cache and len(games) > 1:
            self._steam_search_cache[group_id] = games
            self._steam_search_pending[group_id] = True
            lines = ["找到多个匹配游戏，请回复序号："]
            for index, game in enumerate(games, 1):
                lines.append(f"{index}. {game.title}")
            yield event.plain_result("\n".join(lines))
            return
        price_region = self.config.get("price_region", "CN")
        summary, cn_summary, ru_summary = await asyncio.gather(
            self.ITAD_CLIENT.get_price_summary(game.id, price_region),
            self.ITAD_CLIENT.get_price_summary(game.id, "CN"),
            self.ITAD_CLIENT.get_price_summary(game.id, "RU"),
        )
        region_prices = {"CN": cn_summary, "RU": ru_summary}
        detail = await self.fetch_game_details(game.appid) if game.appid else None
        if detail and game.appid:
            review = await self.fetch_game_reviews(game.appid)
            if review:
                detail['review'] = review
        self._steam_search_pending.pop(group_id, None)
        self._steam_search_cache.pop(group_id, None)
        currency = summary.get('currency') or ''
        current_price = summary.get('current_price')
        regular_price = summary.get('current_regular')
        cut = summary.get('cut')
        lowest = summary.get('history_low')
        if lowest is None:
            lowest = summary.get('lowest')
        lines = [game.title]
        if current_price is not None:
            price_text = f"{current_price:g} {currency}".strip()
            if regular_price is not None and regular_price != current_price:
                price_text += f"（原价 {regular_price:g} {currency}）".strip()
            if cut:
                price_text += f"，折扣 {int(cut)}%"
            lines.append(f"当前价格：{price_text}")
        else:
            lines.append("当前价格：暂无")
        lines.append(f"历史最低价：{f'{lowest:g} {currency}'.strip() if lowest is not None else '暂无'}")
        if detail and detail.get('store_appid'):
            lines.append(f"Steam AppID：{detail['store_appid']}")
        lines.append("来源：ITAD")

        card_data = detail or {
            'name': game.title,
            'header_image': game.image,
            'short_description': '由 ITAD 提供当前价格与历史最低价信息。',
            'genres': [],
            'developers': [],
            'release_date': {'date': '未知'},
            'price_overview': {},
            'review': {},
        }
        try:
            img_bytes = await render_game_detail_image(
                card_data,
                font_path=get_font_path("NotoSansHans-Regular.otf"),
                proxy=self.proxy,
                itad_summary=summary,
                region_prices=region_prices,
            )
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                image_path = tmp.name
            yield event.image_result(image_path)
            return
        except Exception as exc:
            logger.exception("渲染 Steam 价格详情卡片失败: %s", exc)

        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.MEMBER)
    @filter.command("steam list")
    async def steam_list(self, event: AstrMessageEvent):
        '''列出本群所有玩家当前状态（分群）'''
        group_id = str(event.get_group_id()) if hasattr(event, 'get_group_id') else 'default'
        direct_steam_ids = self.group_steam_ids.get(group_id, [])
        push_steam_ids = [
            sid
            for sid, push_groups in (getattr(self, 'push_groups', {}) or {}).items()
            if group_id in {str(target) for target in push_groups}
        ]
        steam_ids = list(dict.fromkeys([*direct_steam_ids, *push_steam_ids]))
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

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam qq菜单同步")
    async def steam_qq_menu_sync(self, event: AstrMessageEvent):
        """创建或更新 QQ 官方机器人指令面板。"""
        yield event.plain_result(await self.qq_menu_sync(event))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam qq菜单状态")
    async def steam_qq_menu_status(self, event: AstrMessageEvent):
        """查询 QQ 官方机器人指令面板状态。"""
        yield event.plain_result(await self.qq_menu_status(event))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam qq菜单删除")
    async def steam_qq_menu_delete(self, event: AstrMessageEvent):
        """删除本插件记录的 QQ 官方机器人指令面板。"""
        yield event.plain_result(await self.qq_menu_delete(event))

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
        # 渲染列表卡片（新版steam风格不展示封面；旧版卡片风格需要封面，仅在关闭新风格时预取）
        from ..presentation.renderers.steam_list import render_steam_list_image
        font_path = self.get_font_path('NotoSansHans-Regular.otf')
        steam_style = self.config.get('enable_steam_style', False)
        covers = {}
        if not steam_style and gameid:
            from ..presentation.renderers.game_start import get_cover_path
            cp = await get_cover_path(self.data_dir, gameid, game or zh_game_name, proxy=self.proxy)
            if cp: covers[sid] = cp
        img_bytes = await render_steam_list_image(self.data_dir, user_list, font_path=font_path, proxy=self.proxy, avatar_frame_paths=avatar_frame_paths, covers=covers, steam_style=steam_style)
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
                , proxy=self.proxy, version=self._plugin_version, sgdb_api_base=self.SGDB_API_BASE, steam_store_base=self.STEAM_STORE_BASE)
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
            , proxy=self.proxy, api_key=self.API_KEY)
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
        for task in self._pending_quit_tasks.values():
            task.cancel()
        self._pending_quit_tasks.clear()
        for task in self.achievement_poll_tasks.values():
            task.cancel()
        self.achievement_poll_tasks.clear()
        self.achievement_snapshots.clear()
        self.achievement_fail_count.clear()
        self.group_steam_ids.clear()
        self.push_groups.clear()
        self.running_groups.clear()
        self.group_monitor_enabled.clear()
        self.group_achievement_enabled.clear()
        self.next_poll_time.clear()
        self.group_last_states.clear()
        self.group_start_play_times.clear()
        self.group_last_quit_times.clear()
        self.group_pending_logs.clear()
        self.group_pending_quit.clear()
        self.group_recent_games.clear()
        self._pending_end_notifications.clear()
        self.notify_sessions.clear()
        self._save_group_steam_ids()
        self._save_push_groups()
        self._save_notify_session()
        self._save_persistent_data(force=True)
        self.config['group_steam_ids'] = self.group_steam_ids
        if hasattr(self.config, "save_config"):
            self.config.save_config()
        yield event.plain_result("已删除所有群聊的所有SteamID，相关状态数据已清空。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("steam clear_groupids")
    async def steam_clear_groupids(self, event: AstrMessageEvent, group_id: str):
        '''删除指定群聊的所有已监控SteamID，并清空相关状态数据'''
        has_primary = group_id in self.group_steam_ids
        routed_sids = [
            sid for sid, targets in self.push_groups.items()
            if group_id in {str(target) for target in targets}
        ]
        if not has_primary and not routed_sids:
            yield event.plain_result(f"群聊 {group_id} 未绑定任何SteamID，无需清理。")
            return

        for task_key in list(self._pending_quit_tasks):
            if task_key[0] == group_id:
                task = self._pending_quit_tasks.pop(task_key)
                task.cancel()
        for sid in list(self.group_steam_ids.get(group_id, [])):
            self.push_groups.pop(sid, None)
        for sid in routed_sids:
            targets = [target for target in self.push_groups.get(sid, []) if str(target) != group_id]
            if targets:
                self.push_groups[sid] = targets
            else:
                self.push_groups.pop(sid, None)
        self.group_steam_ids.pop(group_id, None)
        self.group_last_states.pop(group_id, None)
        self.group_start_play_times.pop(group_id, None)
        self.group_last_quit_times.pop(group_id, None)
        self.group_pending_logs.pop(group_id, None)
        self.group_pending_quit.pop(group_id, None)
        self.group_recent_games.pop(group_id, None)
        self.next_poll_time.pop(group_id, None)
        self.running_groups.discard(group_id)
        self.group_monitor_enabled.pop(group_id, None)
        self.group_achievement_enabled.pop(group_id, None)
        self.notify_sessions.pop(group_id, None)
        self._save_group_steam_ids()
        self._save_push_groups()
        self._save_notify_session()
        self._save_persistent_data(force=True)
        if hasattr(self.config, "save_config"):
            self.config.save_config()
        yield event.plain_result(f"已删除群聊 {group_id} 的所有SteamID和分发路由，相关状态数据已清空。")

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
        # 新版steam风格不展示封面；旧版卡片风格需要封面，仅在关闭新风格时预取
        steam_style = self.config.get('enable_steam_style', False)
        covers = {}
        if not steam_style:
            for u in user_list:
                gid = u.get('gameid', '')
                if gid:
                    from ..presentation.renderers.game_start import get_cover_path
                    cp = await get_cover_path(self.data_dir, gid, u.get('game', ''), proxy=self.proxy)
                    if cp:
                        covers[u['sid']] = cp
        img_bytes = await render_steam_list_image(self.data_dir, user_list, font_path=font_path, proxy=self.proxy, avatar_frame_paths=avatar_frame_paths, covers=covers, steam_style=steam_style)
        if img_bytes:
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            yield event.image_result(tmp_path)
        else:
            yield event.plain_result("渲染图片失败")

    def get_today_superpower(self, steamid):
        today = date.today().isoformat()
        cache_key = (steamid, today)
        if cache_key in self._superpower_cache:
            return self._superpower_cache[cache_key]
        if self._abilities is None:
            with open(self._abilities_path, encoding="utf-8") as abilities_file:
                self._abilities = [line.strip() for line in abilities_file if line.strip()]
        superpower = random.Random(f"{steamid}-{today}").choice(self._abilities)
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
