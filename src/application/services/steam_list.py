import io
import tempfile
from typing import Optional

from ...presentation.renderers.steam_list import render_steam_list_image
from ...presentation.renderers.game_start import get_avatar_frame_url, get_avatar_frame_path
from .player_status_view import PlayerStatusViewService, build_player_row

__all__ = ["build_player_row", "handle_steam_list", "render_user_list_image", "list_parent"]


def list_parent(event):
    """返回 (触发者昵称, QQ头像URL)；获取失败返回 (None, None)。"""
    try:
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
    except Exception:
        return None, None
    url = f"https://q1.qlogo.cn/g?b=qq&nk={sender_id}&s=640" if sender_id else None
    return sender_name, url


async def collect_list_assets(plugin, user_list, *, proxy=None):
    avatar_frame_paths = {}
    for user in user_list:
        sid = user.get("sid", "")
        if not sid:
            continue
        fp = await get_avatar_frame_path(plugin.data_dir, sid, proxy=proxy)
        if not fp:
            frame_url = await get_avatar_frame_url(sid, proxy=proxy)
            if frame_url:
                fp = await get_avatar_frame_path(plugin.data_dir, sid, frame_url, proxy=proxy)
        if fp:
            avatar_frame_paths[sid] = fp
    steam_style = (getattr(plugin, "config", {}) or {}).get("enable_steam_style", False)
    covers = {}
    if not steam_style:
        from ...presentation.renderers.game_start import get_cover_path

        for user in user_list:
            gid = user.get("gameid", "")
            if not gid:
                continue
            cp = await get_cover_path(
                plugin.data_dir,
                gid,
                user.get("game", ""),
                sgdb_api_key=getattr(plugin, "SGDB_API_KEY", None),
                appid=gid,
                proxy=proxy,
                sgdb_api_base=getattr(plugin, "SGDB_API_BASE", None),
            )
            if cp:
                covers[user["sid"]] = cp
    return avatar_frame_paths, covers, steam_style


async def render_user_list_image(plugin, event, user_list, *, font_path: Optional[str] = None, proxy=None):
    proxy = plugin.proxy if proxy is None else proxy
    avatar_frame_paths, covers, steam_style = await collect_list_assets(plugin, user_list, proxy=proxy)
    parent_name, parent_avatar_url = list_parent(event)
    img_bytes = await render_steam_list_image(
        plugin.data_dir,
        user_list,
        font_path=font_path,
        proxy=proxy,
        avatar_frame_paths=avatar_frame_paths,
        covers=covers,
        steam_style=steam_style,
        parent_name=parent_name,
        parent_avatar_url=parent_avatar_url,
    )
    if not img_bytes:
        return None
    with io.BytesIO(img_bytes) as buf:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(buf.read())
            return tmp.name


async def handle_steam_list(self, event, *, font_path: Optional[str] = None, proxy: str = None, **_kwargs):
    """列出所有玩家当前状态（图片美化版，分群支持）"""
    group_id = None
    if hasattr(event, "get_group_id"):
        group_id = str(event.get_group_id())
    elif hasattr(event, "group_id"):
        group_id = str(event.group_id)
    else:
        group_id = "default"
    view = getattr(self, "player_status_view", None) or PlayerStatusViewService(self)
    user_list = await view.build_group_rows(group_id)
    tmp_path = await render_user_list_image(self, event, user_list, font_path=font_path, proxy=proxy)
    if tmp_path:
        yield event.image_result(tmp_path)
    else:
        yield event.plain_result("渲染图片失败")
