import time
import io
from typing import Optional
from ...presentation.renderers.steam_list import render_steam_list_image
from ...presentation.renderers.game_start import get_avatar_frame_url, get_avatar_frame_path

async def handle_steam_list(self, event, *, font_path: Optional[str] = None, proxy: str = None, **_kwargs):
    '''列出所有玩家当前状态（图片美化版，分群支持）'''
    # 获取分群ID
    group_id = None
    if hasattr(event, 'get_group_id'):
        group_id = str(event.get_group_id())
    elif hasattr(event, 'group_id'):
        group_id = str(event.group_id)
    else:
        group_id = 'default'
    steam_ids = self.group_steam_ids.get(group_id, [])
    start_play_times = self.group_start_play_times.get(group_id, {})
    user_list = []
    now = int(time.time())
    # 批量查询所有玩家状态，减少API调用次数
    status_map = await self.fetch_player_statuses_batch(steam_ids) if steam_ids else {}
    for sid in steam_ids:
        status = status_map.get(sid)
        if not status:
            user_list.append({
                'sid': sid,
                'name': sid,
                'status': 'error',
                'avatar_url': '',
                'game': '',
                'gameid': '',
                'play_str': '获取失败',
                'lastlogoff': None
            })
            continue
        name = self._resolve_bind_name(sid, status.get('name') or sid)
        gameid = status.get('gameid')
        game = status.get('gameextrainfo')
        lastlogoff = status.get('lastlogoff')
        personastate = status.get('personastate', 0)
        avatar_url = status.get('avatarfull') or status.get('avatar') or ''
        zh_game_name = await self.get_chinese_game_name(gameid, game) if gameid else (game or "未知游戏")
        if gameid:
            # 修复: start_play_times[sid] 可能为 dict
            start_time = None
            if isinstance(start_play_times.get(sid), dict):
                # 优先取当前游戏的开始时间
                if gameid and gameid in start_play_times[sid]:
                    start_time = start_play_times[sid][gameid]
                else:
                    # 如果没有当前游戏，取所有游戏的最晚开始时间
                    if start_play_times[sid]:
                        start_time = max(start_play_times[sid].values())
            else:
                start_time = start_play_times.get(sid)
            play_seconds = now - start_time if start_time else 0
            play_minutes = play_seconds / 60
            if play_minutes < 60:
                play_str = f"{play_minutes:.1f}分钟"
            else:
                play_str = f"{play_minutes/60:.1f}小时"
            user_list.append({
                'sid': sid,
                'name': name,
                'status': 'playing',
                'avatar_url': avatar_url,
                'game': zh_game_name,
                'gameid': gameid,
                'play_str': play_str,
                'lastlogoff': lastlogoff
            })
        elif personastate and int(personastate) > 0:
            user_list.append({
                'sid': sid,
                'name': name,
                'status': 'online',
                'avatar_url': avatar_url,
                'game': '',
                'gameid': '',
                'play_str': '',
                'lastlogoff': lastlogoff
            })
        elif lastlogoff:
            hours_ago = (now - int(lastlogoff)) / 3600
            user_list.append({
                'sid': sid,
                'name': name,
                'status': 'offline',
                'avatar_url': avatar_url,
                'game': '',
                'gameid': '',
                'play_str': f"上次在线 {hours_ago:.1f} 小时前",
                'lastlogoff': lastlogoff
            })
        else:
            user_list.append({
                'sid': sid,
                'name': name,
                'status': 'offline',
                'avatar_url': avatar_url,
                'game': '',
                'gameid': '',
                'play_str': '',
                'lastlogoff': lastlogoff
            })
    # 获取所有用户的头像框
    avatar_frame_paths = {}
    for u in user_list:
        sid = u.get("sid", "")
        if sid:
            fp = await get_avatar_frame_path(self.data_dir, sid, proxy=proxy)
            if not fp:
                frame_url = await get_avatar_frame_url(sid, proxy=proxy)
                if frame_url:
                    fp = await get_avatar_frame_path(self.data_dir, sid, frame_url, proxy=proxy)
            if fp:
                avatar_frame_paths[sid] = fp
    # 渲染图片（新版 steam 风格不展示封面；旧版卡片风格需要封面，仅在关闭新风格时预取）
    steam_style = self.config.get('enable_steam_style', False)
    covers = {}
    if not steam_style:
        for u in user_list:
            gid = u.get('gameid', '')
            if gid:
                from ...presentation.renderers.game_start import get_cover_path
                cp = await get_cover_path(self.data_dir, gid, u.get('game', ''), proxy=proxy)
                if cp:
                    covers[u['sid']] = cp
    img_bytes = await render_steam_list_image(self.data_dir, user_list, font_path=font_path, proxy=proxy, avatar_frame_paths=avatar_frame_paths, covers=covers, steam_style=steam_style)
    if img_bytes:
        with io.BytesIO(img_bytes) as buf:
            import tempfile
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(buf.read())
                tmp_path = tmp.name
            yield event.image_result(tmp_path)
    else:
        yield event.plain_result("渲染图片失败")
