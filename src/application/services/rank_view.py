import os
import tempfile
from dataclasses import dataclass
from typing import List, Optional, Tuple

from ...domain.ranking.push_scopes import build_rank_push_scopes
from ...presentation.renderers.game_start import get_avatar_frame_path, get_avatar_frame_url
from ...presentation.renderers.rank import render_rank_image
from ...shared.fonts import resolve_font_path
from ...shared.logging import logger
from ...shared.utils.notify_session import is_sendable_group_session, is_valid_group_id


def parse_rank_period(period: str = "") -> Tuple[int, str]:
    period = (period or "").strip().lower()
    if period == "week":
        return 7, "最近7天"
    if period == "month":
        return 30, "最近30天"
    if period.isdigit():
        days = int(period) or 1
        if days <= 0:
            days = 1
        return days, f"最近{days}天"
    return 1, "今日"


def lookup_top_game_id(play_records, ranking, sid, top_name, days, base_day_offset) -> Optional[str]:
    for index in range(days):
        day_data = play_records.get(ranking.day_key(base_day_offset - index), {})
        for game_id, game_info in day_data.get(sid, {}).items():
            if game_info.get("name") == top_name:
                return game_id
    return None


class RankViewService:
    """排行展示：补昵称/头像/主玩游戏，再出图。不记账。"""

    def __init__(self, plugin):
        self._plugin = plugin

    async def enrich(self, rank_data: List[dict], *, days: int, base_day_offset: int = 0) -> List[dict]:
        plugin = self._plugin
        ranking = plugin.ranking_service
        sid_set = {player["sid"] for player in rank_data}
        sid_info = {}
        if sid_set:
            status_map = await plugin.fetch_player_statuses_batch(list(sid_set))
            for sid, info in status_map.items():
                sid_info[sid] = {
                    "name": info.get("name") or sid,
                    "avatar_url": info.get("avatarfull") or info.get("avatar"),
                }
        play_records = getattr(plugin, "play_records", {}) or {}
        for player in rank_data:
            sid = player["sid"]
            info = sid_info.get(sid, {})
            fallback = info.get("name", sid[-8:] if len(sid) >= 8 else sid)
            player["name"] = plugin._resolve_bind_name(sid, fallback)
            player["avatar_url"] = info.get("avatar_url")
            player["top_game_id"] = None
            games = player.get("games") or []
            if not games:
                continue
            player["top_game_id"] = lookup_top_game_id(
                play_records,
                ranking,
                sid,
                games[0]["name"],
                days,
                base_day_offset,
            )
            for game in games:
                gid = game.get("gameid")
                if not gid:
                    continue
                resolved = await plugin.get_chinese_game_name(str(gid), game.get("name"))
                if resolved:
                    game["name"] = resolved
        return rank_data

    async def collect_frames(self, rank_data: List[dict]) -> dict:
        plugin = self._plugin
        avatar_frame_paths = {}
        for player in rank_data:
            sid = player.get("sid", "")
            if not sid:
                continue
            frame_path = await get_avatar_frame_path(plugin.data_dir, sid, proxy=plugin.proxy)
            if not frame_path:
                frame_url = await get_avatar_frame_url(sid, proxy=plugin.proxy)
                if frame_url:
                    frame_path = await get_avatar_frame_path(
                        plugin.data_dir,
                        sid,
                        frame_url,
                        proxy=plugin.proxy,
                    )
            if frame_path:
                avatar_frame_paths[sid] = frame_path
        return avatar_frame_paths

    async def render_file(self, rank_data: List[dict], period_label: str) -> Optional[str]:
        plugin = self._plugin
        avatar_frame_paths = await self.collect_frames(rank_data)

        async def cover_fetcher(gameid):
            return await plugin.get_game_cover_url(gameid)

        font_path = resolve_font_path("NotoSansHans-Regular.otf")
        img_bytes = await render_rank_image(
            plugin.data_dir,
            rank_data,
            period_label,
            font_path=font_path,
            proxy=plugin.proxy,
            cover_fetcher=cover_fetcher,
            avatar_frame_paths=avatar_frame_paths,
        )
        if not img_bytes:
            return None
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(img_bytes)
            return tmp.name

    async def render_aggregated(
        self,
        *,
        days: int,
        period_label: str,
        group_id: Optional[str] = None,
        base_day_offset: int = 0,
    ) -> Optional[str]:
        plugin = self._plugin
        ranking = plugin.ranking_service
        rank_data = ranking.aggregate(
            days=days,
            sids=ranking.target_sids(group_id),
            base_day_offset=base_day_offset,
        )
        if not rank_data:
            return None
        await self.enrich(rank_data, days=days, base_day_offset=base_day_offset)
        tmp_path = await self.render_file(rank_data, period_label)
        if not tmp_path:
            raise RuntimeError("渲染图片失败")
        return tmp_path

    def configure_push(self, param: str, group_id: Optional[str] = None) -> "RankPushConfigResult":
        plugin = self._plugin
        action = (param or "").strip().lower()
        groups = list(getattr(plugin, "rank_push_groups", []) or [])
        if action == "list":
            if groups:
                mode = "全局" if getattr(plugin, "rank_push_all", False) else "分群"
                return RankPushConfigResult(
                    f"当前排行榜推送模式：{mode}排行，推送群：{', '.join(groups)}"
                )
            return RankPushConfigResult(
                "当前未开启任何排行榜推送。使用 /steam rank_on 或 /steam rank_on all 开启。"
            )
        if action == "test":
            return RankPushConfigResult("正在生成昨日排行榜，稍等...", should_push=True)
        if action.startswith("del"):
            parts = action.split()
            target = parts[1] if len(parts) >= 2 else (group_id or "default")
            if target in plugin.rank_push_groups:
                plugin.rank_push_groups.remove(target)
                plugin._save_rank_push_groups()
                return RankPushConfigResult(f"已关闭群 {target} 的每日排行榜推送。")
            return RankPushConfigResult(f"群 {target} 未在推送列表中。")
        if not is_valid_group_id(group_id):
            return RankPushConfigResult("请在群聊中开启排行榜推送。")
        plugin.rank_push_all = action == "all"
        if group_id not in plugin.rank_push_groups:
            plugin.rank_push_groups.append(group_id)
        plugin._save_rank_push_groups()
        if plugin.rank_push_all:
            return RankPushConfigResult("已开启每日排行榜自动推送（全局排行）")
        return RankPushConfigResult("已开启本群每日排行榜自动推送。")

    async def push_daily(self) -> None:
        plugin = self._plugin
        scopes = build_rank_push_scopes(
            getattr(plugin, "rank_push_groups", []),
            use_global_rank=getattr(plugin, "rank_push_all", False),
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
                    try:
                        tmp_path = await self.render_aggregated(
                            days=1,
                            period_label="昨日",
                            group_id=data_group_id,
                            base_day_offset=-1,
                        )
                        if not tmp_path:
                            scope_label = (
                                "全部群"
                                if data_group_id is None
                                else f"群 {data_group_id}"
                            )
                            logger.info(f"[排行榜] {scope_label}昨日无游玩记录，跳过推送")
                        rendered_files[render_key] = tmp_path
                    except Exception as exc:
                        logger.error(
                            f"[排行榜] 渲染群 {data_group_id or '全局'} 昨日榜单失败: {exc}"
                        )
                        rendered_files[render_key] = None

                tmp_path = rendered_files[render_key]
                if not tmp_path:
                    continue
                try:
                    session = getattr(plugin, "notify_sessions", {}).get(target_group_id)
                    if not is_sendable_group_session(session):
                        logger.warning(
                            f"[排行榜] 群 {target_group_id} 未找到有效推送会话，跳过"
                        )
                        continue
                    await self.send_rank_image(session, tmp_path)
                    logger.info(f"[排行榜] 已推送昨日排行榜到群 {target_group_id}")
                except Exception as exc:
                    logger.error(f"[排行榜] 推送群 {target_group_id} 失败: {exc}")
        except Exception as exc:
            logger.error(f"[排行榜] 每日推送异常: {exc}")
        finally:
            for tmp_path in {path for path in rendered_files.values() if path}:
                try:
                    os.unlink(tmp_path)
                except OSError as exc:
                    logger.warning(f"[排行榜] 清理临时图片失败 {tmp_path}: {exc}")

    async def send_rank_image(self, session: str, tmp_path: str) -> None:
        from astrbot.api.event import MessageChain
        from astrbot.api.message_components import Image, Plain

        await self._plugin.context.send_message(
            session,
            MessageChain([
                Plain("📊 昨日游戏时长排行榜来啦！\n"),
                Image.fromFileSystem(tmp_path),
            ]),
        )


@dataclass(frozen=True)
class RankPushConfigResult:
    message: str
    should_push: bool = False
