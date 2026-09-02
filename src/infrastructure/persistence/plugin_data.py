from datetime import datetime, timedelta
import json
import os
import shutil
import time

from ...shared.logging import logger

from ...shared.paths import FONTS_DIR


class PersistenceMixin:
    def _get_group_data_path(self, group_id, key):
        """获取分群数据文件路径"""
        return os.path.join(self.data_dir, f"group_{group_id}_{key}.json")

    def _load_persistent_data(self):
        # 分群加载各群的状态数据
        for group_id in self.group_steam_ids:
            try:
                path = self._get_group_data_path(group_id, "states")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        self.group_last_states[group_id] = json.load(f)
            except Exception as e:
                logger.warning(f"加载 group_last_states 失败: {e} (group_id={group_id})")
            try:
                path = self._get_group_data_path(group_id, "start_play_times")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        self.group_start_play_times[group_id] = json.load(f)
                        # 数据迁移：旧格式 int → 新格式 {gameid: timestamp}
                        migrated = 0
                        for _sid, _val in list(self.group_start_play_times[group_id].items()):
                            if not isinstance(_val, dict):
                                self.group_start_play_times[group_id][_sid] = {}
                                migrated += 1
                        if migrated:
                            logger.info(f"[数据迁移] group_id={group_id}: {migrated} 个玩家 start_play_times 从 int 迁移为 dict")
            except Exception as e:
                logger.warning(f"加载 group_start_play_times 失败: {e} (group_id={group_id})")
            try:
                path = self._get_group_data_path(group_id, "last_quit_times")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        self.group_last_quit_times[group_id] = json.load(f)
            except Exception as e:
                logger.warning(f"加载 group_last_quit_times 失败: {e} (group_id={group_id})")
            try:
                path = self._get_group_data_path(group_id, "pending_logs")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        self.group_pending_logs[group_id] = json.load(f)
            except Exception as e:
                logger.warning(f"加载 group_pending_logs 失败: {e} (group_id={group_id})")
            try:
                path = self._get_group_data_path(group_id, "pending_quit")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        self.group_pending_quit[group_id] = json.load(f)
            except Exception as e:
                logger.warning(f"加载 group_pending_quit 失败: {e} (group_id={group_id})")
            try:
                path = self._get_group_data_path(group_id, "recent_games")
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        self.group_recent_games[group_id] = json.load(f)
            except Exception as e:
                logger.warning(f"加载 group_recent_games 失败: {e} (group_id={group_id})")
        try:
            path = os.path.join(self.data_dir, "playing_sessions.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    self.session_service.load(json.load(f))
        except Exception as e:
            logger.warning(f"加载 playing_sessions 失败: {e}")
        self.session_service.hydrate_from_legacy()

    def _save_persistent_data(self, force=False):
        '''分群保存各群的状态数据。
        - force=True 或距上次保存超过 _save_interval 才真正落盘
        - 否则只标记脏位，由主循环周期性 flush
        '''
        if not force and (time.time() - self._last_save_time) < getattr(self, '_save_interval', 300):
            self._data_dirty = True
            return
        self._data_dirty = False
        self._last_save_time = time.time()
        for group_id in self.group_steam_ids:
            try:
                path = self._get_group_data_path(group_id, "states")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.group_last_states.get(group_id, {}), f, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"保存 group_last_states 失败: {e} (group_id={group_id})")
            try:
                path = self._get_group_data_path(group_id, "start_play_times")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.group_start_play_times.get(group_id, {}), f, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"保存 group_start_play_times 失败: {e} (group_id={group_id})")
            try:
                path = self._get_group_data_path(group_id, "last_quit_times")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.group_last_quit_times.get(group_id, {}), f, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"保存 group_last_quit_times 失败: {e} (group_id={group_id})")
            try:
                path = self._get_group_data_path(group_id, "pending_logs")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.group_pending_logs.get(group_id, {}), f, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"保存 group_pending_logs 失败: {e} (group_id={group_id})")
            try:
                path = self._get_group_data_path(group_id, "pending_quit")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.group_pending_quit.get(group_id, {}), f, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"保存 group_pending_quit 失败: {e} (group_id={group_id})")
            try:
                path = self._get_group_data_path(group_id, "recent_games")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.group_recent_games.get(group_id, []), f, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"保存 group_recent_games 失败: {e} (group_id={group_id})")
        try:
            path = os.path.join(self.data_dir, "playing_sessions.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.session_service.dump(), f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"保存 playing_sessions 失败: {e}")
        # 保存游玩时长记录（全局，不分群）
        try:
            self._save_play_records()
        except Exception as e:
            logger.warning(f"保存 play_records 失败: {e}")
        # 保存 session 记录（甘特图/热力图数据源）
        try:
            self._save_session_records()
        except Exception as e:
            logger.warning(f"保存 session_records 失败: {e}")

    def _load_notify_session(self):
        path = os.path.join(self.data_dir, "notify_sessions.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.notify_sessions = json.load(f)
                logger.info(f"[SteamStatusMonitor] 已加载 notify_sessions: {self.notify_sessions}")
            except Exception as e:
                logger.warning(f"加载 notify_sessions 失败: {e}")
        else:
            self.notify_sessions = {}

    def _save_notify_session(self):
        if hasattr(self, 'notify_sessions'):
            path = os.path.join(self.data_dir, "notify_sessions.json")
            try:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(self.notify_sessions, f, ensure_ascii=False)
                logger.info(f"[SteamStatusMonitor] 已保存 notify_sessions: {self.notify_sessions}")
            except Exception as e:
                logger.warning(f"保存 notify_sessions 失败: {e}")

    def _record_platform_id(self, event):
        """从消息事件中提取并缓存平台ID，用于WebUI自动构造通知目标"""
        if self._platform_id:
            return
        self._platform_id = event.unified_msg_origin.split(":")[0]
        self._auto_fill_notify_sessions()

    def _auto_fill_notify_sessions(self):
        """为WebUI添加的群（有群有ID但无notify_sessions）自动构造通知目标"""
        if not self._platform_id:
            return
        if not hasattr(self, 'notify_sessions'):
            self.notify_sessions = {}
        filled = 0
        for gid in getattr(self, 'group_steam_ids', {}) or {}:
            if gid not in self.notify_sessions or not self.notify_sessions[gid]:
                self.notify_sessions[gid] = f"{self._platform_id}:GroupMessage:0_{gid}"
                filled += 1
        if filled:
            self._save_notify_session()
            logger.info(f"[WebUI自动投递] 已为 {filled} 个群补全通知目标")

    def _ensure_fonts(self):
        """检测插件fonts目录是否有NotoSansHans系列字体，有则复制到缓存目录并缓存路径"""
        plugin_fonts_dir = str(FONTS_DIR)
        cache_fonts_dir = os.path.join('data', 'steam_status_monitor', 'fonts')
        os.makedirs(plugin_fonts_dir, exist_ok=True)
        os.makedirs(cache_fonts_dir, exist_ok=True)
        font_candidates = [
            'NotoSansHans-Regular.otf',
            'NotoSansHans-Medium.otf'
        ]
        self.font_paths = {}
        for font_name in font_candidates:
            plugin_font_path = os.path.join(plugin_fonts_dir, font_name)
            cache_font_path = os.path.join(cache_fonts_dir, font_name)
            if os.path.exists(plugin_font_path):
                shutil.copy(plugin_font_path, cache_font_path)
                self.font_paths[font_name] = cache_font_path
            elif os.path.exists(cache_font_path):
                self.font_paths[font_name] = cache_font_path
            else:
                self.font_paths[font_name] = None
        # 详细日志
        for font_name in font_candidates:
            logger.info(f"[Font] {font_name} 路径: {self.font_paths.get(font_name)}")
        if not all(self.font_paths.values()):
            logger.warning("[Font] 未检测到全部NotoSansHans字体，渲染可能会出现乱码！")

    def get_font_path(self, font_name=None, bold=False):
        """优先返回缓存fonts目录下NotoSansHans字体路径"""
        if not font_name:
            font_name = 'NotoSansHans-Regular.otf'
        if bold:
            font_name = 'NotoSansHans-Medium.otf'
        return self.font_paths.get(font_name) or font_name

    def _get_groups_file_path(self):
        """获取 steam_groups.json 文件路径"""
        return os.path.join(self.data_dir, "steam_groups.json")

    def _load_group_steam_ids(self):
        """从 steam_groups.json 加载所有群的 SteamID 列表"""
        path = self._get_groups_file_path()
        groups = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    groups = json.load(f)
                logger.info(f"[SteamStatusMonitor] 已加载 steam_groups.json: {groups}")
            except Exception as e:
                logger.warning(f"加载 steam_groups.json 失败: {e}")
        self.monitor_state.group_steam_ids = groups

    def _save_group_steam_ids(self):
        """保存所有群的 SteamID 列表到 steam_groups.json"""
        path = self._get_groups_file_path()
        groups = self.monitor_state.group_steam_ids
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(groups, f, ensure_ascii=False, indent=2)
            logger.info(f"[SteamStatusMonitor] 已保存 steam_groups.json: {groups}")
        except Exception as e:
            logger.warning(f"保存 steam_groups.json 失败: {e}")

    def _get_push_groups_path(self):
        """获取 push_groups.json 文件路径"""
        return os.path.join(self.data_dir, "push_groups.json")

    def _load_push_groups(self):
        """加载 SteamID -> 群号列表 的推送映射"""
        path = self._get_push_groups_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.push_groups = json.load(f)
            except Exception as e:
                logger.warning(f"加载 push_groups.json 失败: {e}")
        else:
            self.push_groups = {}

    def _save_push_groups(self):
        """保存 SteamID -> 群号列表 的推送映射"""
        path = self._get_push_groups_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.push_groups, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存 push_groups.json 失败: {e}")

    # ========== 排行榜功能：游玩时长记录持久化 ==========

    def _load_play_records(self):
        """加载游玩时长记录"""
        path = os.path.join(self.data_dir, "play_records.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.play_records = json.load(f)
            except Exception as e:
                logger.warning(f"加载 play_records.json 失败: {e}")
                self.play_records = {}
        else:
            self.play_records = {}

    def _save_play_records(self):
        """保存游玩时长记录，并自动清理超过30天的旧记录"""
        if not hasattr(self, 'play_records'):
            return
        cutoff_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        cleaned = {}
        for date_str, data in self.play_records.items():
            if date_str >= cutoff_date:
                cleaned[date_str] = data
        self.play_records = cleaned
        path = os.path.join(self.data_dir, "play_records.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.play_records, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"保存 play_records.json 失败: {e}")

    # ========== Session 游玩记录（甘特图/热力图数据源）==========

    def _load_session_records(self):
        """加载 session 级别的游玩记录"""
        path = os.path.join(self.data_dir, "session_records.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.session_records = json.load(f)
            except Exception as e:
                logger.warning(f"加载 session_records.json 失败: {e}")
                self.session_records = {}
        else:
            self.session_records = {}

    def _save_session_records(self):
        """保存 session 记录，自动清理超过90天的旧数据"""
        if not hasattr(self, "session_records"):
            return
        cutoff_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        cleaned = {}
        for sid, sessions in self.session_records.items():
            cleaned[sid] = [s for s in sessions if s.get("date", "") >= cutoff_date]
        self.session_records = cleaned
        path = os.path.join(self.data_dir, "session_records.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.session_records, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"保存 session_records.json 失败: {e}")

    def _record_session(self, sid, gameid, game_name, start_time, end_time, duration_min, group_id):
        """记录单次游玩 session（在游戏退出确认后调用）。"""
        if duration_min <= 0 or not gameid:
            return
        date_str = self._get_day_key(0)
        start_timestamp = int(start_time) if start_time else 0
        end_timestamp = int(end_time) if end_time else 0
        session_id = f"{date_str}_{start_timestamp}_{gameid}"
        sessions = self.session_records.setdefault(str(sid), [])
        if any(item.get("session_id") == session_id for item in sessions):
            return
        sessions.append({
            "session_id": session_id,
            "gameid": str(gameid),
            "game_name": str(game_name),
            "start_time": start_timestamp,
            "end_time": end_timestamp,
            "duration_min": round(float(duration_min), 2),
            "date": date_str,
            "group_id": str(group_id),
        })
        self._session_dirty = True

    # ========== QQ-SteamID 绑定系统 ==========

    def _load_bind_data(self):
        """加载QQ-SteamID绑定数据"""
        path = os.path.join(self.data_dir, "bind_data.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._bind_data = json.load(f)
            except Exception as e:
                logger.warning(f"加载 bind_data.json 失败: {e}")
                self._bind_data = {}
        else:
            self._bind_data = {}

    def _save_bind_data(self):
        """保存QQ-SteamID绑定数据"""
        path = os.path.join(self.data_dir, "bind_data.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._bind_data, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"保存 bind_data.json 失败: {e}")

    def _resolve_bind_name(self, sid, steam_name=None):
        """根据绑定表返回显示名：自定义备注 > QQ昵称 > Steam原始名"""
        bind_data = getattr(self, '_bind_data', {})
        for qq, info in bind_data.items():
            if info.get("sid") == str(sid):
                nick = info.get("nickname", "")
                if nick and nick != "*":
                    return nick
                break
        return steam_name or str(sid)

    def _load_rank_push_groups(self):
        """加载开启了每日排行榜推送的群列表及 rank_push_all 标志"""
        path = os.path.join(self.data_dir, "rank_push_groups.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    self.rank_push_groups = raw.get("groups", [])
                    self.rank_push_all = raw.get("all", False)
                elif isinstance(raw, list):
                    # 兼容旧格式（纯列表）
                    self.rank_push_groups = raw
                    self.rank_push_all = False
                else:
                    self.rank_push_groups = []
                    self.rank_push_all = False
            except Exception as e:
                logger.warning(f"加载 rank_push_groups.json 失败: {e}")
                self.rank_push_groups = []
                self.rank_push_all = False
        else:
            self.rank_push_groups = []
            self.rank_push_all = False

    def _save_rank_push_groups(self):
        """保存开启了每日排行榜推送的群列表及 rank_push_all 标志"""
        path = os.path.join(self.data_dir, "rank_push_groups.json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"groups": self.rank_push_groups, "all": getattr(self, 'rank_push_all', False)}, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"保存 rank_push_groups.json 失败: {e}")
