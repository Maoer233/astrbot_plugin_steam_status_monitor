# -*- coding: utf-8 -*-
"""AstrBot Plugin Pages API for the Steam Monitor dashboard."""
import asyncio
import base64
import logging
import mimetypes
import os
from datetime import datetime
from functools import wraps

from astrbot.api.web import error_response, json_response, request

from .qqofficial_settings import (
    QQ_OFFICIAL_DEFAULTS,
    mask_secret,
    normalise_qq_official_settings,
)
from .response_cache import AsyncTTLCache
from ...application.services.monitor_admin import MonitorAdminService
from ...shared.network import configure_tls, httpx_client_kwargs
from .statistics import (
    build_dashboard_stats,
    build_groups,
    build_heatmap_data,
    build_player_search_index,
)
from astrbot.core.star.command_management import (
    list_commands,
    update_command_permission,
)

# 尝试导入父包的工具函数（运行时可能因 AstrBot 加载机制失败，优雅降级）
try:
    from ..renderers.rank import render_rank_image
except ImportError:
    render_rank_image = None

logger = logging.getLogger(__name__)

PLUGIN_NAME = "steam_status_monitor_V3"


def _get_player_display_name(p, sid):
    """获取玩家显示名：绑定备注 > Steam昵称 > SteamID"""
    # 1) 从绑定表查
    bind_data = getattr(p, "_bind_data", {}) or {}
    for qq, info in bind_data.items():
        if info.get("sid") == str(sid):
            nick = info.get("nickname", "")
            if nick and nick != "*":
                return nick
            break
    # 2) 从 last_states 查 Steam 用户名
    for gid_states in (getattr(p, "group_last_states", {}) or {}).values():
        state = gid_states.get(str(sid), {})
        if state.get("name"):
            return state["name"]
    # 3) 递归查（部分数据 group_id 可能不同）
    for gid_states in (getattr(p, "group_last_states", {}) or {}).values():
        for _sid, state in gid_states.items():
            if str(_sid) == str(sid) and state.get("name"):
                return state["name"]
    return str(sid)


def _flatten_commands(commands):
    """递归展开框架返回的命令树。"""
    flattened = []
    for command in commands or []:
        flattened.append(command)
        flattened.extend(_flatten_commands(command.get("sub_commands") or []))
    return flattened


def _command_descriptor_to_dict(descriptor):
    """将框架更新函数返回的 CommandDescriptor 转为可 JSON 序列化字典。"""
    return {
        "handler_full_name": descriptor.handler_full_name,
        "handler_name": descriptor.handler_name,
        "plugin": descriptor.plugin_name,
        "plugin_display_name": descriptor.plugin_display_name,
        "module_path": descriptor.module_path,
        "description": descriptor.description,
        "type": descriptor.command_type,
        "parent_signature": descriptor.parent_signature,
        "parent_group_handler": descriptor.parent_group_handler,
        "original_command": descriptor.original_command,
        "current_fragment": descriptor.current_fragment,
        "effective_command": descriptor.effective_command,
        "aliases": descriptor.aliases,
        "permission": descriptor.permission,
        "enabled": descriptor.enabled,
        "is_group": descriptor.is_group,
        "has_conflict": descriptor.has_conflict,
        "reserved": descriptor.reserved,
        "sub_commands": [
            _command_descriptor_to_dict(child)
            for child in descriptor.sub_commands
        ],
    }


class _PluginPageRequest:
    """Expose the subset of aiohttp's request API used by the legacy handlers."""

    @property
    def query(self):
        return request.query

    @property
    def match_info(self):
        return request.path_params or {}

    async def json(self):
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload


def safe_api(handler):
    """Catch handler failures and return a consistent Plugin Pages error."""

    @wraps(handler)
    async def wrapper(*_args, **_kwargs):
        try:
            return await handler(_PluginPageRequest())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(
                f"[WebAdmin] API 异常 {handler.__name__}: {e}",
                exc_info=True,
            )
            return error_response(str(e), status_code=500)

    return wrapper


class WebAdminAPI:
    """Backend API registered into AstrBot's authenticated Dashboard."""

    def __init__(self, plugin_instance, admin_service=None):
        self.plugin = plugin_instance
        self.admin = admin_service or MonitorAdminService(plugin_instance)
        self._response_cache = AsyncTTLCache()

    async def _cached_response(self, key, ttl, builder):
        payload = await self._response_cache.get_or_create(
            key, ttl, lambda: asyncio.to_thread(builder)
        )
        return json_response(payload)

    def invalidate_cache(self, *names):
        self._response_cache.invalidate(*names)

    def register_routes(self, context):
        """Register all routes used by ``pages/steam-monitor``."""

        def r(method, path, handler):
            context.register_web_api(
                f"/{PLUGIN_NAME}{path}",
                safe_api(handler),
                [method],
                f"Steam Monitor Page: {handler.__name__}",
            )

        # Plugin-local endpoints. The Page bridge adds the plugin prefix.
        r("GET", "/dashboard/stats", self._api_dashboard_stats)
        r("GET", "/dashboard/rank-image", self._api_dashboard_rank_image)
        r("GET", "/gantt/data", self._api_gantt_data)
        r("GET", "/heatmap/data", self._api_heatmap_data)
        r("GET", "/heatmap/player/<steamid>", self._api_heatmap_player)
        r("GET", "/groups", self._api_groups_list)
        r("POST", "/groups/add", self._api_groups_add)
        r("POST", "/groups/delete", self._api_groups_delete)
        r("POST", "/groups/add-group", self._api_groups_add_group)
        r("POST", "/groups/delete-group", self._api_groups_delete_group)
        r("POST", "/groups/import-batch", self._api_groups_import_batch)
        r("GET", "/groups/<group_id>/players", self._api_group_players)
        r("GET", "/bindings", self._api_bindings_list)
        r("POST", "/bindings/add", self._api_bindings_add)
        r("POST", "/bindings/delete", self._api_bindings_delete)
        r("POST", "/bindings/update", self._api_bindings_update)
        r("GET", "/push/settings", self._api_push_settings)
        r("POST", "/push/update", self._api_push_update)
        r("POST", "/push/groups/add", self._api_push_group_add)
        r("POST", "/push/groups/remove", self._api_push_group_remove)
        r("POST", "/push/rank-scope", self._api_push_rank_scope)
        r("GET", "/settings", self._api_settings_get)
        r("POST", "/settings/update", self._api_settings_update)
        r("GET", "/qq-official/settings", self._api_qq_official_settings_get)
        r("POST", "/qq-official/settings", self._api_qq_official_settings_update)
        r("POST", "/qq-official/settings/reset", self._api_qq_official_settings_reset)
        r("GET", "/permissions", self._api_permissions_list)
        r("POST", "/permissions/update", self._api_permissions_update)
        r("GET", "/players/search", self._api_players_search)
        r("GET", "/players/avatar/<steamid>", self._api_player_avatar)
        r("GET", "/players/info/<steamid>", self._api_player_info)
        r("GET", "/games/cover/<gameid>", self._api_game_cover)
        r("GET", "/test/steam", self._api_test_steam)
        r("GET", "/test/cover", self._api_test_cover)
        r("GET", "/test/steamid/<steamid>", self._api_test_steamid)

    def _is_plugin_command(self, command):
        if command.get("plugin") == "steam_status_monitor_V3":
            return True
        module_path = command.get("module_path") or ""
        plugin_module = self.plugin.__class__.__module__
        return module_path == plugin_module or module_path.startswith(f"{plugin_module}.")

    async def _plugin_commands(self):
        commands = _flatten_commands(await list_commands())
        return [command for command in commands if self._is_plugin_command(command)]

    # ────── Command Permissions ──────

    async def _api_permissions_list(self, request):
        commands = await self._plugin_commands()
        return json_response({"commands": commands})

    async def _api_permissions_update(self, request):
        data = await request.json()
        handler_full_name = str(data.get("handler_full_name") or "").strip()
        permission = str(data.get("permission") or "").strip().lower()
        if permission not in {"admin", "member"}:
            return json_response(
                {"error": "permission 只允许 admin 或 member"},
                status_code=400,
            )

        commands = await self._plugin_commands()
        if not any(
            command.get("handler_full_name") == handler_full_name
            for command in commands
        ):
            return json_response(
                {"error": "指定指令不属于本插件"},
                status_code=404,
            )

        descriptor = await update_command_permission(handler_full_name, permission)
        return json_response({
            "ok": True,
            "command": _command_descriptor_to_dict(descriptor),
        })

    # ────── Dashboard Stats ──────

    async def _api_dashboard_stats(self, request):
        p = self.plugin
        today = (
            p._get_day_key(0)
            if hasattr(p, "_get_day_key")
            else datetime.now().strftime("%Y-%m-%d")
        )
        last_update = datetime.now().strftime("%Y-%m-%d %H:%M")
        return await self._cached_response(
            ("dashboard", today),
            10,
            lambda: build_dashboard_stats(p, today, last_update),
        )

    # ────── Dashboard Rank Image ──────

    async def _api_dashboard_rank_image(self, request):
        """生成排行榜图片（参考 rank_render 渲染方式，使用本地缓存的封面/头像）"""
        p = self.plugin
        if not render_rank_image:
            return json_response(
                {"error": "rank_render not available"},
                status_code=500,
            )

        from datetime import datetime, timedelta
        try:
            days = int(request.query.get("days", "7"))
        except (ValueError, TypeError):
            days = 7
        try:
            offset = int(request.query.get("offset", "0"))
        except (ValueError, TypeError):
            offset = 0

        now = datetime.now()
        boundary_today = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if now < boundary_today:
            boundary_today -= timedelta(days=1)
        range_end = boundary_today + timedelta(days=offset + 1)
        range_start = range_end - timedelta(days=days)

        start_key = range_start.strftime("%Y-%m-%d")
        end_key = range_end.strftime("%Y-%m-%d")
        period_label = f"最近{days}天" if days > 1 else ("今天" if offset == 0 else "昨天")

        play_records = getattr(p, "play_records", {}) or {}
        player_agg = {}

        for date_key, date_data in play_records.items():
            if date_key < start_key or date_key >= end_key:
                continue
            for sid, games in date_data.items():
                if sid not in player_agg:
                    player_agg[sid] = {"sid": sid, "name": _get_player_display_name(p, sid), "total_minutes": 0, "games": []}
                game_map = {}
                for gid, ginfo in games.items():
                    mins = ginfo.get("minutes", 0) if isinstance(ginfo, dict) else 0
                    if mins <= 0:
                        continue
                    gname = ginfo.get("name", str(gid)) if isinstance(ginfo, dict) else str(gid)
                    if gid in game_map:
                        game_map[gid]["mins"] += mins
                    else:
                        game_map[gid] = {"gid": gid, "name": gname, "mins": mins}
                    player_agg[sid]["total_minutes"] += mins
                for gid_data in game_map.values():
                    existing = next((g for g in player_agg[sid]["games"] if g.get("gameid") == gid_data["gid"]), None)
                    if existing:
                        existing["minutes"] += gid_data["mins"]
                    else:
                        player_agg[sid]["games"].append({"gameid": gid_data["gid"], "name": gid_data["name"], "minutes": gid_data["mins"]})

        rank_data = sorted(player_agg.values(), key=lambda x: -x["total_minutes"])[:25]
        if not rank_data:
            return json_response({"error": "no data"}, status_code=404)

        # 补充头像 URL 和 top_game_id（与 rank 指令完全一致的渲染方式）
        avatar_map = {}
        for gid_states in (getattr(p, "group_last_states", {}) or {}).values():
            for sid, state in gid_states.items():
                if state.get("avatarfull") or state.get("avatar"):
                    avatar_map[sid] = state.get("avatarfull") or state.get("avatar")

        for pl in rank_data:
            top = sorted(pl.get("games", []), key=lambda g: -g["minutes"])
            pl["top_game_id"] = top[0]["gameid"] if top else ""
            pl["avatar_url"] = avatar_map.get(pl["sid"])

        # Cover fetcher — 复用插件实例的 get_game_cover_url（水平封面 from Steam Store）
        async def cover_fetcher(gameid):
            if hasattr(p, "get_game_cover_url"):
                try:
                    return await p.get_game_cover_url(gameid)
                except Exception:
                    pass
            return None

        font_path = None
        try:
            fp = getattr(p, "get_font_path", None)
            if fp:
                font_path = fp("NotoSansHans-Regular.otf")
            if not font_path:
                font_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "fonts",
                    "NotoSansHans-Regular.otf",
                )
        except Exception:
            pass

        try:
            img_bytes = await render_rank_image(
                getattr(p, "data_dir", ""),
                rank_data, period_label,
                font_path=font_path,
                proxy=getattr(p, "proxy", None),
                cover_fetcher=cover_fetcher,
            )
            encoded = base64.b64encode(img_bytes).decode("ascii")
            return json_response({"data_url": f"data:image/png;base64,{encoded}"})
        except Exception as e:
            logger.error(f"[WebAdmin] rank_image 渲染失败: {e}", exc_info=True)
            return json_response(
                {"error": f"render failed: {e}"},
                status_code=500,
            )

    # ────── Gantt ──────

    async def _api_gantt_data(self, request):
        p = self.plugin
        from datetime import datetime, timedelta

        try:
            days = int(request.query.get("days", "1"))
        except (ValueError, TypeError):
            days = 1
        try:
            offset = int(request.query.get("offset", "0"))
        except (ValueError, TypeError):
            offset = 0

        now = datetime.now()
        # 日期范围：以凌晨4:00为边界，从今天往回推 N 天
        boundary_today = now.replace(hour=4, minute=0, second=0, microsecond=0)
        if now < boundary_today:
            boundary_today -= timedelta(days=1)
        range_end = boundary_today + timedelta(days=offset + 1)
        range_start = range_end - timedelta(days=days)

        start_key = range_start.strftime("%Y-%m-%d")
        end_key = range_end.strftime("%Y-%m-%d")

        players = []
        player_map = {}  # {sid: player_dict}

        # 1) 优先使用 session_records（有真实 start/end 时间戳）
        session_records = getattr(p, "session_records", {}) or {}
        for sid, sessions in session_records.items():
            sid_sessions = []
            for s in sessions:
                s_date = s.get("date", "")
                if not s_date or s_date < start_key or s_date >= end_key:
                    continue
                s_start = s.get("start_time", 0)
                s_end = s.get("end_time", 0)
                if not s_start or not s_end:
                    continue
                sid_sessions.append({
                    "gameid": s.get("gameid", ""),
                    "game_name": s.get("game_name", "Unknown"),
                    "start": s_start,
                    "end": s_end,
                    "duration_min": s.get("duration_min", 0),
                })
            if sid_sessions:
                player_map[sid] = {"sid": sid, "name": _get_player_display_name(p, sid), "sessions": sid_sessions}
                players.append(player_map[sid])

        # 2) 补充 play_records（没有 session_records 时，按实际分钟数构造 1:1 时间片）
        play_records = getattr(p, "play_records", {}) or {}
        for date_key, date_data in play_records.items():
            if date_key < start_key or date_key >= end_key:
                continue
            for sid, games in date_data.items():
                if sid in player_map:  # session_records 已覆盖此玩家，跳过 play_records 合成
                    continue
                day_start_ts = int(datetime.strptime(date_key, "%Y-%m-%d").timestamp()) + 4 * 3600
                offset_ts = day_start_ts
                for gid, ginfo in games.items():
                    mins = ginfo.get("minutes", 0) if isinstance(ginfo, dict) else 0
                    if mins <= 0:
                        continue
                    gname = ginfo.get("name", str(gid)) if isinstance(ginfo, dict) else str(gid)
                    if sid not in player_map:
                        player_map[sid] = {"sid": sid, "name": _get_player_display_name(p, sid), "sessions": []}
                        players.append(player_map[sid])
                    # 关键修复：每分钟映射为 60 秒（1:1），不按 24h 比例
                    span = min(mins * 60, 24 * 3600)
                    player_map[sid]["sessions"].append({
                        "gameid": str(gid),
                        "game_name": gname,
                        "start": offset_ts,
                        "end": min(offset_ts + span, day_start_ts + 24 * 3600),
                        "duration_min": mins,
                    })
                    offset_ts += span

        # 构建 game_details（饼图 tooltip 使用：每款游戏的 Top5 玩家）
        game_details = {}
        for pl in players:
            for s in pl.get("sessions", []):
                gid = s.get("gameid", "")
                if not gid:
                    continue
                if gid not in game_details:
                    game_details[gid] = {"name": s.get("game_name", gid), "players": []}
                existing = next((p for p in game_details[gid]["players"] if p["name"] == pl["name"]), None)
                if existing:
                    existing["minutes"] += s.get("duration_min", 0)
                else:
                    game_details[gid]["players"].append({"name": pl["name"], "minutes": s.get("duration_min", 0)})

        # 取每款游戏 Top5
        for gid, info in game_details.items():
            info["players"].sort(key=lambda x: -x["minutes"])
            info["players"] = info["players"][:5]

        # 从游戏封面提取主色调（饼图着色用）
        game_colors = {}
        try:
            from PIL import Image
            data_dir = getattr(p, "data_dir", "")
            covers_dir = os.path.join(data_dir, "covers")
            for gid in game_details:
                cover_path = os.path.join(covers_dir, f"{gid}.jpg")
                if os.path.exists(cover_path):
                    with Image.open(cover_path) as img:
                        # 缩放到 1x1 取平均色
                        avg = img.resize((1, 1)).getpixel((0, 0))
                        game_colors[gid] = "#{:02x}{:02x}{:02x}".format(avg[0], avg[1], avg[2])
        except Exception:
            pass

        return json_response({
            "players": players,
            "time_range": {
                "start": range_start.strftime("%Y-%m-%d %H:%M"),
                "end": range_end.strftime("%Y-%m-%d %H:%M"),
            },
            "game_details": game_details,
            "game_colors": game_colors,
        })

    # ────── Heatmap ──────

    async def _api_heatmap_data(self, request):
        try:
            period = int(request.query.get("period", "30"))
        except (ValueError, TypeError):
            period = 30
        period = max(1, min(period, 366))
        group_id = str(request.query.get("group_id", "")).strip()
        groups = getattr(self.plugin, "group_steam_ids", {}) or {}
        if group_id and group_id not in groups:
            return error_response("群组不存在", status=404)
        now = datetime.now()
        return await self._cached_response(
            ("heatmap", period, group_id or "all", now.strftime("%Y-%m-%d-%H")),
            30,
            lambda: build_heatmap_data(
                self.plugin, period, now, group_id or None
            ),
        )

    async def _api_heatmap_player(self, request):
        p = self.plugin
        from datetime import datetime, timedelta

        steamid = request.match_info.get("steamid", "")
        try:
            period = int(request.query.get("period", "30"))
        except (ValueError, TypeError):
            period = 30

        end_date = datetime.now().replace(hour=4, minute=0, second=0, microsecond=0)
        if end_date <= datetime.now():
            end_date += timedelta(days=1)
        start_date = end_date - timedelta(days=period)

        session_records = getattr(p, "session_records", {}) or {}
        player_sessions = session_records.get(steamid, [])

        heatmap_daily = {}
        heatmap_hourly = {}
        game_totals = {}
        total_minutes = 0
        days_played = set()

        # 1) 从 session_records 统计
        for s in player_sessions:
            s_date = s.get("date", "")
            if not s_date:
                continue
            try:
                s_dt = datetime.strptime(s_date, "%Y-%m-%d")
            except ValueError:
                continue
            if s_dt < start_date or s_dt > end_date:
                continue
            mins = s.get("duration_min", 0)
            heatmap_daily[s_date] = heatmap_daily.get(s_date, 0) + mins
            total_minutes += mins
            days_played.add(s_date)

            s_start = s.get("start_time", 0)
            if s_start:
                hour = datetime.fromtimestamp(s_start).hour
                heatmap_hourly.setdefault(s_date, {})
                heatmap_hourly[s_date][str(hour)] = heatmap_hourly[s_date].get(str(hour), 0) + mins

            gid = s.get("gameid", "")
            gname = s.get("game_name", "Unknown")
            game_totals.setdefault(gid, {"gameid": gid, "name": gname, "minutes": 0})
            game_totals[gid]["minutes"] += mins

        # 2) 从 play_records 补充
        play_records = getattr(p, "play_records", {}) or {}
        day = start_date
        while day <= end_date:
            date_key = day.strftime("%Y-%m-%d")
            day_data = (play_records.get(date_key, {}) or {}).get(steamid, {})
            if day_data:
                day_minutes = 0
                for gid, ginfo in day_data.items():
                    mins = ginfo.get("minutes", 0) if isinstance(ginfo, dict) else 0
                    if mins <= 0:
                        continue
                    gname = ginfo.get("name", str(gid)) if isinstance(ginfo, dict) else str(gid)
                    day_minutes += mins
                    total_minutes += mins
                    heatmap_daily[date_key] = heatmap_daily.get(date_key, 0) + mins
                    days_played.add(date_key)
                    game_totals.setdefault(gid, {"gameid": gid, "name": gname, "minutes": 0})
                    game_totals[gid]["minutes"] += mins
            else:
                # 填充空白日期（用户独热力图也要显示完整日历）
                heatmap_daily.setdefault(date_key, 0)
            day += timedelta(days=1)

        name = _get_player_display_name(p, steamid)
        avg_daily = round(total_minutes / max(period, 1), 1)
        top_games = sorted(
            [{"gameid": v["gameid"], "name": v["name"], "minutes": v["minutes"]} for v in game_totals.values()],
            key=lambda x: -x["minutes"],
        )[:10]

        return json_response({
            "sid": steamid,
            "name": name,
            "heatmap_daily": heatmap_daily,
            "heatmap_hourly": heatmap_hourly,
            "total_minutes": total_minutes,
            "top_games": top_games,
            "avg_daily_minutes": avg_daily,
            "days_played": len(days_played),
        })

    # ────── Groups ──────

    async def _api_groups_list(self, request):
        groups = await self._response_cache.get_or_create(
            ("groups",),
            10,
            lambda: asyncio.to_thread(build_groups, self.plugin),
        )
        return json_response({"groups": groups})

    async def _api_groups_add(self, request):
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        gid = str(data.get("group_id", ""))
        sid = str(data.get("steamid", ""))
        if not gid:
            return json_response({"error": "invalid group_id"}, status_code=400)
        if not sid:
            return json_response({"error": "steamid required"}, status_code=400)
        try:
            resolved = await p.resolve_steam_input(sid)
        except Exception:
            resolved = None
        if not resolved or not resolved.isdigit() or len(resolved) != 17:
            return json_response(
                {"error": "无效SteamID，支持格式：17位SteamID64 / 个人资料链接 / 8位好友码"},
                status_code=400,
            )
        sid = resolved
        result = self.admin.add_player(gid, sid)
        if not result.changed:
            if result.message in {"already exists", "already push group"}:
                return json_response({"ok": True, "message": result.message})
            return json_response({"error": result.message}, status_code=400)
        qq = str(data.get("qq", "")).strip()
        if qq:
            nick = str(data.get("nickname", "")).strip()
            self.admin.set_binding(qq, sid, nick or "*")
        self._invalidate_statistics_cache()
        return json_response({"ok": True, "message": result.message})

    async def _api_groups_delete(self, request):
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        gid = str(data.get("group_id", ""))
        sid = str(data.get("steamid", ""))
        if not gid or not sid:
            return json_response(
                {"error": "invalid group_id or steamid"},
                status_code=400,
            )
        result = self.admin.remove_player(gid, sid)
        if not result.changed:
            return json_response(
                {"ok": False, "error": result.message},
                status_code=404,
            )
        self._invalidate_statistics_cache()
        return json_response({"ok": True, "message": result.message})

    async def _api_groups_add_group(self, request):
        """新增一个空群聊"""
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        gid = str(data.get("group_id", "")).strip()
        if not gid:
            return json_response({"error": "invalid group_id"}, status_code=400)
        result = self.admin.add_group(gid)
        if result.changed:
            self._invalidate_statistics_cache()
        payload = {"ok": True}
        if result.message:
            payload["message"] = result.message
        return json_response(payload)

    async def _api_groups_delete_group(self, request):
        """删除一个群聊及其所有 SteamID"""
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        gid = str(data.get("group_id", "")).strip()
        if not gid:
            return json_response({"error": "invalid group_id"}, status_code=400)
        if self.admin.remove_group(gid):
            self._invalidate_statistics_cache()
        return json_response({"ok": True})

    async def _api_groups_import_batch(self, request):
        """批量导入：每行 steamid/链接 [qq] [备注]，空格分隔"""
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        gid = str(data.get("group_id", ""))
        text = str(data.get("text", ""))
        if not gid:
            return json_response({"error": "group_id required"}, status_code=400)
        if not text.strip():
            return json_response({"error": "text is empty"}, status_code=400)

        existing = set(self.admin.groups.get(gid, []))
        max_size = self.admin.max_group_size

        imported = []
        errors = []
        for i, line in enumerate(text.split("\n")):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            raw_sid = parts[0] if parts else ""
            qq = parts[1] if len(parts) > 1 else ""
            nickname = parts[2] if len(parts) > 2 else ""

            if not raw_sid:
                errors.append(f"第{i+1}行: SteamID为空")
                continue

            # 解析SteamID（支持好友码/URL/链接）
            try:
                sid = await p.resolve_steam_input(raw_sid)
            except Exception as e:
                errors.append(f"第{i+1}行: 解析失败 - {e}")
                continue
            if not sid or not sid.isdigit() or len(sid) != 17:
                errors.append(f"第{i+1}行: 无效SteamID - {raw_sid}")
                continue

            # 去重
            if sid in existing:
                errors.append(f"第{i+1}行: {sid} 已存在")
                continue

            primary_exists = any(
                sid in steam_ids
                for candidate, steam_ids in self.admin.groups.items()
                if candidate != gid
            )
            if len(existing) >= max_size and not primary_exists:
                errors.append(f"第{i+1}行: 群已满 (上限{max_size})")
                continue

            result = self.admin.add_player(gid, sid)
            if not result.changed:
                errors.append(f"第{i+1}行: {result.message}")
                continue
            if result.message == "added as primary monitor":
                existing.add(sid)
            imported.append(sid)

            # 绑定QQ
            if qq:
                self.admin.set_binding(str(qq), sid, nickname or "*")

        if imported:
            self._invalidate_statistics_cache()
        return json_response({
            "ok": True,
            "imported": len(imported),
            "errors": errors,
        })

    async def _api_group_players(self, request):
        p = self.plugin
        group_id = request.match_info.get("group_id", "")
        result = []
        for player in self.admin.list_group_players(group_id):
            sid = player["sid"]
            state = player["state"]
            result.append({
                "sid": sid,
                "name": _get_player_display_name(p, sid),
                "gameid": state.get("gameid", ""),
                "game": state.get("gameextrainfo", ""),
                "personastate": state.get("personastate", 0),
            })
        return json_response({"group_id": group_id, "players": result})

    # ────── Bindings ──────

    async def _api_bindings_list(self, request):
        p = self.plugin
        bind_data = getattr(p, "_bind_data", {}) or {}
        result = []
        for qq, info in bind_data.items():
            result.append({
                "qq": str(qq),
                "steamid": info.get("sid", ""),
                "nickname": info.get("nickname", ""),
            })
        return json_response({"bindings": result})

    async def _api_bindings_add(self, request):
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        qq = str(data.get("qq", ""))
        sid = str(data.get("steamid", ""))
        nickname = str(data.get("nickname", ""))
        if not qq or not sid:
            return json_response(
                {"error": "qq and steamid required"},
                status_code=400,
            )
        self.admin.set_binding(qq, sid, nickname)
        return json_response({"ok": True})

    async def _api_bindings_delete(self, request):
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        qq = str(data.get("qq", ""))
        self.admin.remove_binding(qq)
        return json_response({"ok": True})

    async def _api_bindings_update(self, request):
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        qq = str(data.get("qq", ""))
        nickname = str(data.get("nickname", ""))
        self.admin.update_binding_nickname(qq, nickname)
        return json_response({"ok": True})

    # ────── Push Settings ──────

    async def _api_push_settings(self, request):
        p = self.plugin
        return json_response({
            "rank_push_hour": getattr(p, "rank_push_hour", 8),
            "rank_push_minute": getattr(p, "rank_push_minute", 30),
            "rank_push_groups": getattr(p, "rank_push_groups", []),
            "rank_push_all": getattr(p, "rank_push_all", False),
            "all_groups": list((getattr(p, "group_steam_ids", {}) or {}).keys()),
        })

    async def _api_push_update(self, request):
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        try:
            hour = int(data.get("rank_push_hour", p.rank_push_hour))
            minute = int(data.get("rank_push_minute", p.rank_push_minute))
        except (TypeError, ValueError):
            return json_response(
                {"error": "invalid push time"},
                status_code=400,
            )
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            return json_response(
                {"error": "push time out of range"},
                status_code=400,
            )
        p.rank_push_hour = hour
        p.rank_push_minute = minute
        p.config["rank_push_hour"] = p.rank_push_hour
        p.config["rank_push_minute"] = p.rank_push_minute
        if hasattr(p.config, "save_config"):
            p.config.save_config()
        return json_response({"ok": True})

    async def _api_push_group_add(self, request):
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        gid = str(data.get("group_id", ""))
        if gid and gid not in (getattr(p, "rank_push_groups", []) or []):
            getattr(p, "rank_push_groups").append(gid)
        p._save_rank_push_groups()
        return json_response({"ok": True})

    async def _api_push_group_remove(self, request):
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        gid = str(data.get("group_id", ""))
        groups = getattr(p, "rank_push_groups", []) or []
        if gid in groups:
            groups.remove(gid)
        p._save_rank_push_groups()
        return json_response({"ok": True})

    async def _api_push_rank_scope(self, request):
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        scope = str(data.get("scope", "")).strip().lower()
        if scope not in {"group", "global"}:
            return json_response(
                {"error": "scope must be group or global"},
                status_code=400,
            )
        p.rank_push_all = scope == "global"
        p._save_rank_push_groups()
        return json_response({
            "ok": True,
            "rank_push_all": p.rank_push_all,
            "scope": scope,
        })

    # ────── Settings ──────

    async def _api_settings_get(self, request):
        p = self.plugin
        config = dict(p.config)
        for key in ("steam_api_key", "sgdb_api_key"):
            if config.get(key):
                config[key] = "******" + config[key][-4:] if len(config[key]) > 4 else "******"
        config.pop("web_port", None)
        return json_response(config)

    async def _api_settings_update(self, request):
        p = self.plugin
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "invalid JSON"}, status_code=400)
        skip_keys = {"steam_api_key", "sgdb_api_key"}
        for key, value in data.items():
            if key in skip_keys:
                raw = str(value)
                if not raw.startswith("******"):
                    p.config[key] = raw
                continue
            p.config[key] = value
            if key == "fixed_poll_interval":
                p.fixed_poll_interval = int(value) if value else 0
            elif key == "smart_poll_intervals":
                if isinstance(value, str):
                    p.smart_poll_intervals = [int(x.strip()) for x in value.split(",") if x.strip()]
                    p.config[key] = ",".join(str(x) for x in p.smart_poll_intervals)
            elif key == "enable_game_start_notify":
                p.config[key] = bool(value)
            elif key == "enable_game_end_notify":
                p.config[key] = bool(value)
            elif key == "enable_network_fluctuation_notify":
                p.config[key] = bool(value)
            elif key == "enable_achievement_poll":
                p.config[key] = bool(value)
            elif key == "enable_steam_style":
                p.config[key] = bool(value)
            elif key == "notify_send_image":
                p.config[key] = bool(value)
            elif key == "notify_send_text":
                p.config[key] = bool(value)
            elif key == "enable_proxy":
                p.config[key] = bool(value)
                p.proxy = p.config.get("proxy_url") if bool(value) and p.config.get("proxy_url") else None
            elif key == "proxy_url":
                p.config[key] = str(value)
                p.proxy = str(value) if p.config.get("enable_proxy") and value else None
            elif key in {"steam_api_base", "steam_store_base", "sgdb_api_base"}:
                base_url = str(value or "").strip().rstrip("/")
                if base_url and not base_url.startswith(("http://", "https://")):
                    return error_response(f"{key} 必须以 http:// 或 https:// 开头", status_code=400)
                p.config[key] = base_url
                if key == "steam_api_base":
                    p.STEAM_API_BASE = base_url or "https://api.steampowered.com"
                elif key == "steam_store_base":
                    p.STEAM_STORE_BASE = base_url or "https://store.steampowered.com"
                else:
                    p.SGDB_API_BASE = base_url or "https://www.steamgriddb.com"
                if hasattr(p, "achievement_monitor"):
                    p.achievement_monitor.steam_api_base = p.STEAM_API_BASE
            elif key == "ssl_ca_file":
                try:
                    configure_tls(value)
                except ValueError as exc:
                    return error_response(str(exc), status_code=400)
                p.SSL_CA_FILE = str(value or "")
        if hasattr(p, "achievement_monitor"):
            p.achievement_monitor.proxy = p.proxy
        if hasattr(p.config, "save_config"):
            p.config.save_config()
        return json_response({"ok": True})

    # ────── QQ Official Settings ──────

    def _qq_official_settings_payload(self):
        config = self.plugin.config
        payload = {
            key: config.get(key, default)
            for key, default in QQ_OFFICIAL_DEFAULTS.items()
        }
        payload["qq_official_secret"] = mask_secret(
            payload["qq_official_secret"]
        )
        return payload

    async def _api_qq_official_settings_get(self, request):
        return json_response(self._qq_official_settings_payload())

    async def _api_qq_official_settings_update(self, request):
        try:
            data = await request.json()
        except Exception:
            return json_response({"error": "请求内容必须是有效 JSON"}, status_code=400)
        if not isinstance(data, dict):
            return json_response({"error": "配置内容必须是对象"}, status_code=400)

        try:
            settings = normalise_qq_official_settings(
                data,
                current_secret=str(
                    self.plugin.config.get("qq_official_secret", "") or ""
                ),
            )
        except ValueError as exc:
            return json_response({"error": str(exc)}, status_code=400)

        for key, value in settings.items():
            self.plugin.config[key] = value
        self.plugin.config.save_config()
        return json_response({
            "ok": True,
            "settings": self._qq_official_settings_payload(),
        })

    async def _api_qq_official_settings_reset(self, request):
        for key, value in QQ_OFFICIAL_DEFAULTS.items():
            self.plugin.config[key] = list(value) if isinstance(value, list) else value
        self.plugin.config["qq_menu_panel_id"] = ""
        self.plugin.config.save_config()
        return json_response({
            "ok": True,
            "settings": self._qq_official_settings_payload(),
        })

    # ────── Search ──────

    async def _api_players_search(self, request):
        query = (request.query.get("q", "") or "").lower()
        index = await self._response_cache.get_or_create(
            ("player_search_index",),
            30,
            lambda: asyncio.to_thread(build_player_search_index, self.plugin),
        )
        results = await asyncio.to_thread(
            lambda: [
                item
                for item in index
                if not query
                or query in item["name"].lower()
                or query in item["sid"]
            ][:50]
        )
        return json_response({"results": results})

    # ────── Player Avatar / Info ──────

    async def _api_player_avatar(self, request):
        """获取玩家头像URL（从 last_states 缓存中读取，不触发网络请求）"""
        p = self.plugin
        steamid = request.match_info.get("steamid", "")
        avatar_url = ""
        # 从 last_states 获取头像 URL（纯内存操作，无网络请求）
        for gid_states in (getattr(p, "group_last_states", {}) or {}).values():
            state = gid_states.get(steamid, {})
            if state:
                avatar_url = state.get("avatarfull") or state.get("avatar") or ""
                if avatar_url:
                    break
        return json_response({"steamid": steamid, "avatar_url": avatar_url})

    async def _api_player_info(self, request):
        """获取玩家详细信息卡片数据"""
        p = self.plugin
        from datetime import datetime
        steamid = request.match_info.get("steamid", "")
        name = _get_player_display_name(p, steamid)

        # 获取当前状态
        current_game = ""
        personastate = 0
        for gid_states in (getattr(p, "group_last_states", {}) or {}).values():
            state = gid_states.get(steamid, {})
            if state:
                current_game = state.get("gameextrainfo", "")
                personastate = state.get("personastate", 0)
                break

        # 累计统计（session_records + play_records）
        total_minutes = 0
        total_sessions = 0
        session_records = getattr(p, "session_records", {}) or {}
        for s in session_records.get(steamid, []):
            total_minutes += s.get("duration_min", 0)
            total_sessions += 1
        # 补充 play_records 的分钟数（session_records 没有精确 session 时）
        play_records = getattr(p, "play_records", {}) or {}
        for date_str, sid_data in play_records.items():
            gdata = sid_data.get(steamid, {})
            if gdata:
                for gid, ginfo in gdata.items():
                    mins = ginfo.get("minutes", 0) if isinstance(ginfo, dict) else 0
                    # 避免重复（session_records 已经包含精确数据时）
                    has_in_session = any(
                        s.get("gameid") == str(gid) and s.get("date") == date_str
                        for s in session_records.get(steamid, [])
                    )
                    if not has_in_session:
                        total_minutes += mins

        # 今天游戏
        today = p._get_day_key(0) if hasattr(p, "_get_day_key") else datetime.now().strftime("%Y-%m-%d")
        play_records = getattr(p, "play_records", {}) or {}
        today_games = []
        today_data = play_records.get(today, {}).get(steamid, {})
        for gid, ginfo in today_data.items():
            today_games.append({
                "name": ginfo.get("name", str(gid)) if isinstance(ginfo, dict) else str(gid),
                "minutes": ginfo.get("minutes", 0) if isinstance(ginfo, dict) else 0,
            })

        # Steam 个人资料链接 (通过 SteamID)
        sid_num = int(steamid) if steamid.isdigit() else None
        profile_url = f"https://steamcommunity.com/profiles/{sid_num}" if sid_num else ""

        return json_response({
            "steamid": steamid,
            "name": name,
            "current_game": current_game,
            "personastate": personastate,
            "total_minutes": total_minutes,
            "total_sessions": total_sessions,
            "today_games": today_games,
            "profile_url": profile_url,
        })

    async def _api_game_cover(self, request):
        """获取游戏横版封面图（水平封面，与 rank 一致）"""
        p = self.plugin
        gameid = request.match_info.get("gameid", "")
        if not gameid:
            return json_response({"error": "no gameid"}, status_code=400)
        try:
            if hasattr(p, "get_game_cover_url"):
                cover_path = await p.get_game_cover_url(gameid)
                if cover_path and os.path.exists(str(cover_path)):
                    path = str(cover_path)
                    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
                    with open(path, "rb") as cover_file:
                        encoded = base64.b64encode(cover_file.read()).decode("ascii")
                    return json_response({
                        "gameid": gameid,
                        "data_url": f"data:{mime};base64,{encoded}",
                    })
        except Exception as e:
            logger.warning(f"[WebAdmin] 读取游戏封面失败 gameid={gameid}: {e}")
        return json_response({"gameid": gameid, "data_url": ""})

    # ────── Test APIs ──────

    async def _api_test_steam(self, request):
        """测试 Steam API、Steam Store（横版封面）、SGDB（竖版封面）连通性"""
        p = self.plugin
        import httpx
        results = {"steam_api": "unknown", "steam_store": "unknown", "cover_horizontal": "unknown", "sgdb": "unknown", "sgdb_cover": "unknown"}
        proxy = getattr(p, "proxy", None)
        ak = getattr(p, "API_KEY", "")
        sgdb_k = getattr(p, "SGDB_API_KEY", "")
        log = []

        # Steam API
        log.append("[Steam API] 开始测试...")
        if not ak:
            results["steam_api"] = "no_key"
            log.append("[Steam API] 未配置 API Key")
        else:
            try:
                url = f"{p.STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/?key={ak}&steamids=0"
                async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(proxy)) as c:
                    r = await c.get(url)
                    results["steam_api"] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
                    log.append(f"[Steam API] HTTP {r.status_code}")
            except Exception as e:
                results["steam_api"] = f"error: {e}"
                log.append(f"[Steam API] 异常: {e}")

        # Steam Store + 横版封面
        log.append("[Steam Store] 开始测试...")
        try:
            async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(proxy)) as c:
                r = await c.get(f"{p.STEAM_STORE_BASE}/api/appdetails?appids=730")
                results["steam_store"] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
                log.append(f"[Steam Store] HTTP {r.status_code}")
        except Exception as e:
            results["steam_store"] = f"error: {e}"
            log.append(f"[Steam Store] 异常: {e}")
        log.append("[横版封面] 开始测试...")
        if hasattr(p, "get_game_cover_url"):
            try:
                path = await p.get_game_cover_url("730")
                if path and os.path.exists(str(path)):
                    results["cover_horizontal"] = "ok"
                    log.append(f"[横版封面] 成功: {path}")
                else:
                    results["cover_horizontal"] = "not_found"
                    log.append(f"[横版封面] 未找到本地缓存, 尝试下载: {path}")
            except Exception as e:
                results["cover_horizontal"] = f"error: {e}"
                log.append(f"[横版封面] 异常: {e}")
        else:
            log.append("[横版封面] get_game_cover_url 方法不存在")

        # SGDB API + 竖版封面
        if not sgdb_k:
            results["sgdb"] = results["sgdb_cover"] = "no_key"
            log.append("[SGDB] 未配置 API Key")
        else:
            log.append("[SGDB API] 开始测试...")
            try:
                async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(proxy)) as c:
                    r = await c.get(f"{p.SGDB_API_BASE}/api/v2/games/steam/385800", headers={"Authorization": f"Bearer {sgdb_k}"})
                    results["sgdb"] = "ok" if r.status_code == 200 else f"http_{r.status_code}"
                    log.append(f"[SGDB API] HTTP {r.status_code}, url=games/steam/385800")
            except Exception as e:
                results["sgdb"] = f"error: {e}"
                log.append(f"[SGDB API] 异常: {e}")
            log.append("[竖版封面] 开始测试...")
            try:
                from ..renderers.game_start import get_sgdb_vertical_cover
                sgdb_url = await get_sgdb_vertical_cover("NEKOPARA Vol. 0", sgdb_api_key=sgdb_k, appid="385800", proxy=proxy)
                if sgdb_url:
                    results["sgdb_cover"] = "ok"
                    log.append(f"[竖版封面] 成功: {sgdb_url}")
                else:
                    results["sgdb_cover"] = "not_found"
                    log.append("[竖版封面] SGDB 未收录或下载失败")
            except Exception as e:
                results["sgdb_cover"] = f"error: {e}"
                log.append(f"[竖版封面] 异常: {e}")

        # 打印日志
        for line in log:
            logger.info(f"[WebAdmin] 测试: {line}")
            print(f"  [测试] {line}")

        return json_response(results)

    async def _api_test_cover(self, request):
        """测试游戏封面获取"""
        p = self.plugin
        test_gid = "730"  # CS2
        if hasattr(p, "get_game_cover_url"):
            try:
                path = await p.get_game_cover_url(test_gid)
                if path and os.path.exists(str(path)):
                    return json_response({
                        "cover": "ok",
                        "path": str(path),
                        "size_kb": round(os.path.getsize(str(path)) / 1024, 1),
                    })
                return json_response({"cover": "not_found", "path": str(path)})
            except Exception as e:
                return json_response({"cover": "error", "message": str(e)})
        return json_response({"cover": "method_not_found"})

    async def _api_test_steamid(self, request):
        """测试 SteamID 查询"""
        p = self.plugin
        steamid = request.match_info.get("steamid", "")
        if not steamid.isdigit():
            return json_response({"error": "invalid steamid"}, status_code=400)
        import httpx
        try:
            # 直接从 last_states 读取缓存
            for gid_states in (getattr(p, "group_last_states", {}) or {}).values():
                state = gid_states.get(steamid, {})
                if state:
                    return json_response({
                        "from_cache": True,
                        "name": state.get("name"),
                        "game": state.get("gameextrainfo"),
                        "personastate": state.get("personastate"),
                        "avatar": state.get("avatarfull") or state.get("avatar"),
                    })
            # 未缓存则调用 Steam API
            ak = getattr(p, "API_KEY", "")
            if not ak:
                return json_response({"error": "no api key"})
            url = f"{p.STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/?key={ak}&steamids={steamid}"
            async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(getattr(p, "proxy", None))) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    players = r.json().get("response", {}).get("players", [])
                    if players:
                        return json_response({"from_api": True, "player": players[0]})
                    return json_response({"error": "player_not_found"})
                return json_response({"error": f"http_{r.status_code}"})
        except Exception as e:
            return json_response({"error": str(e)})
