import re

from ...application.services.steam_list import handle_steam_list, render_user_list_image
from ...application.services.player_status_view import format_alllist_text, sort_rows_for_image
from ...shared.fonts import resolve_font_path
from ...shared.logging import logger
from ...shared.utils.notify_session import is_valid_group_id
from . import group_id_of


async def on(plugin, event):
    group_id = group_id_of(event)
    result = await plugin.monitor_control.start(group_id, notify_session=event.unified_msg_origin)
    if result.ok:
        plugin._record_platform_id(event)
    yield event.plain_result(result.message)


async def addid(plugin, event, steamid: str, at_user: str = "", nickname: str = ""):
    group_id = group_id_of(event)
    if not is_valid_group_id(group_id):
        yield event.plain_result("请在群聊中使用该命令，或到 WebUI 填写有效群号后再添加。")
        return
    bind_qq = None
    bind_nickname = None
    if at_user:
        match = re.search(r'\[CQ:at,qq=(\d+)\]|\[At:(\d+)\]|@.+?\((\d+)\)|@(\d+)', at_user.strip())
        bind_qq = (match.group(1) or match.group(2) or match.group(3) or match.group(4)) if match else None
    if nickname:
        bind_nickname = nickname.strip()
    raw_list = [item.strip() for item in re.split(r'[,，]+', steamid) if item.strip()]
    resolved_list = []
    invalid_list = []
    for raw in raw_list:
        sid = await plugin.resolve_steam_input(raw)
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
    seen = set()
    steamid_list = []
    for sid in resolved_list:
        if sid not in seen:
            seen.add(sid)
            steamid_list.append(sid)
    result = plugin.monitor_admin.add_players(
        group_id,
        steamid_list,
        bind_qq=bind_qq,
        bind_nickname=bind_nickname,
        notify_session=event.unified_msg_origin,
    )
    if result.started:
        plugin._record_platform_id(event)
    yield event.plain_result(result.message)


async def delid(plugin, event, steamid: str, group_id_param: str = ""):
    group_id = group_id_param.strip() if group_id_param.strip() else group_id_of(event)
    sid = await plugin.resolve_steam_input(steamid)
    if not sid or not sid.isdigit() or len(sid) != 17:
        yield event.plain_result("无法解析为有效SteamID，支持格式：17位SteamID64 / 个人资料链接 / 8位好友码")
        return
    result = plugin.monitor_admin.remove_player(group_id, sid)
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


async def list_status(plugin, event):
    group_id = group_id_of(event)
    _, _, steam_ids = plugin.player_status_view.steam_ids_for_group(group_id)
    if not plugin.API_KEY:
        yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
        return
    if not steam_ids:
        yield event.plain_result("本群未设置监控的 SteamID 列表，请先添加。")
        return
    event.group_steam_ids = steam_ids
    font_path = resolve_font_path('NotoSansHans-Regular.otf')
    logger.info(f"[Font] steam_list 渲染传入字体路径: {font_path}")
    async for result in handle_steam_list(plugin, event, group_id=group_id, font_path=font_path, proxy=plugin.proxy):
        yield result


async def who(plugin, event, qq: str):
    match = re.search(r'\[CQ:at,qq=(\d+)\]|\[At:(\d+)\]|@.+?\((\d+)\)|@(\d+)', qq.strip())
    qq_clean = match.group(1) or match.group(2) or match.group(3) or match.group(4) if match else qq.strip().lstrip('@')
    info = getattr(plugin, "_bind_data", {}).get(qq_clean)
    if not info:
        yield event.plain_result(f"QQ {qq_clean} 未绑定任何SteamID，请先使用 /steam addid SteamID @{qq_clean}")
        return
    sid = info.get("sid", "")
    if not sid:
        yield event.plain_result(f"QQ {qq_clean} 的绑定数据异常")
        return
    status = await plugin.fetch_player_status(sid)
    if not status:
        yield event.plain_result(f"无法获取 {sid} 的Steam状态")
        return
    group_id = group_id_of(event)
    primary = plugin.player_status_view.primary_group_of(sid, group_id)
    user_list = [await plugin.player_status_view.build_row(sid, status, group_id=primary)]
    font_path = resolve_font_path('NotoSansHans-Regular.otf')
    tmp_path = await render_user_list_image(plugin, event, user_list, font_path=font_path, proxy=plugin.proxy)
    if tmp_path:
        yield event.image_result(tmp_path)
    else:
        yield event.plain_result("渲染图片失败")


async def off(plugin, event):
    result = plugin.monitor_control.stop(group_id_of(event))
    yield event.plain_result(result.message)


async def achievement_on(plugin, event):
    result = plugin.monitor_control.set_achievement(group_id_of(event), True)
    yield event.plain_result(result.message)


async def achievement_off(plugin, event):
    result = plugin.monitor_control.set_achievement(group_id_of(event), False)
    yield event.plain_result(result.message)


async def clear_allids(plugin, event):
    yield event.plain_result(plugin.monitor_admin.clear_all_ids().message)


async def clear_groupids(plugin, event, group_id: str):
    yield event.plain_result(plugin.monitor_admin.remove_group(group_id).message)


async def alllist(plugin, event, mode: str = "img"):
    user_list = await plugin.player_status_view.build_all_rows()
    if mode.lower() == 'text':
        yield event.plain_result(format_alllist_text(user_list))
        return
    user_list = sort_rows_for_image(user_list)
    font_path = resolve_font_path('NotoSansHans-Regular.otf')
    tmp_path = await render_user_list_image(plugin, event, user_list, font_path=font_path, proxy=plugin.proxy)
    if tmp_path:
        yield event.image_result(tmp_path)
    else:
        yield event.plain_result("渲染图片失败")


async def push_group(plugin, event, steamid: str):
    yield event.plain_result(plugin.monitor_admin.add_push_route(group_id_of(event), steamid).message)


async def delpush_group(plugin, event, steamid: str, target_group: str = ''):
    explicit = bool(target_group.strip())
    group_id = target_group.strip() if explicit else group_id_of(event)
    yield event.plain_result(
        plugin.monitor_admin.remove_push_route(group_id, steamid, explicit_target=explicit).message
    )
