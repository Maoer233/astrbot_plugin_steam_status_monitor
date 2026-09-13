import os
import random
import shutil
import tempfile
import time
import traceback
from datetime import datetime

from PIL import Image as PILImage

from ...presentation.renderers.game_end import render_game_end
from ...presentation.renderers.game_start import render_game_start
from ...presentation.renderers.image_crop import crop_image_auto
from ...plugin.runtime_config import apply_hot_update
from ...shared.fonts import resolve_font_path
from ...shared.logging import logger

HELP_TEXT = (
    "Steam状态监控插件指令：\n"
    "/steam on - 启动监控\n"
    "/steam off - 停止监控\n"
    "/steam price [游戏名或Steam链接] - 查询游戏价格、史低与地区对比\n"
    "/steam px [游戏名] - 价格查询快捷版，直接返回第一条匹配\n"
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
    "/steam fonts - 查看字体包状态（检测CJK字体是否就绪）\n"
    "/steam fonts download - 立即下载字体包\n"
    "/steam fonts clean - 清理已下载字体缓存\n"
    "/steam rs - 清除状态并初始化\n"
    "/steamwho @用户 / 在干嘛 @用户 - 即时查询绑定玩家的Steam状态\n"
    "/steam help - 显示本帮助\n"
)


async def config(plugin, event):
    lines = []
    hidden_keys = {"steam_api_key", "sgdb_api_key"}
    for key, value in plugin.config.items():
        if key in hidden_keys:
            lines.append(f"{key}: ****** (已隐藏)")
        else:
            lines.append(f"{key}: {value}")
    if hasattr(plugin, "smart_poll_intervals"):
        intervals = plugin.smart_poll_intervals
        lines.append(f"智能轮询间隔（分钟）: {intervals}（依次为[游戏中, 12分钟内, 12分钟~3小时, 3小时~24小时, 24~48小时, 超过48小时]）")
    yield event.plain_result("当前配置：\n" + "\n".join(lines))


async def set_config(plugin, event, key: str, value: str):
    _, msg = apply_hot_update(plugin, key, value)
    yield event.plain_result(msg)


async def rs(plugin, event):
    plugin.group_last_states.clear()
    plugin.group_last_quit_times.clear()
    plugin.group_pending_logs.clear()
    plugin.playing_sessions.clear()
    getattr(plugin, "_session_meta", {}).clear()
    plugin.group_recent_games.clear()
    plugin.superpower.clear()
    plugin._game_name_cache.clear()
    plugin.achievement_poll_tasks.clear()
    plugin.achievement_snapshots.clear()
    plugin.running_groups.clear()
    plugin.group_monitor_enabled.clear()
    plugin.group_achievement_enabled.clear()
    plugin.notify_sessions = {}
    plugin._save_group_switches()
    plugin._save_persistent_data(force=True)
    yield event.plain_result("Steam状态监控插件已重置，所有状态已清空。")


async def qq_menu_sync(plugin, event):
    yield event.plain_result(await plugin.qq_menu_sync(event))


async def qq_menu_status(plugin, event):
    yield event.plain_result(await plugin.qq_menu_status(event))


async def qq_menu_delete(plugin, event):
    yield event.plain_result(await plugin.qq_menu_delete(event))


async def help(plugin, event):
    yield event.plain_result(HELP_TEXT)


async def test_achievement_render(plugin, event, steamid: str, gameid: int, count: int = 3):
    player_name = steamid
    game_name = await plugin.get_chinese_game_name(gameid)
    group_id = plugin.GROUP_ID or 'default'
    achievements = await plugin.achievement_monitor.get_player_achievements(
        plugin.API_KEY, group_id, steamid, gameid
    )
    if not achievements:
        yield event.plain_result("未获取到任何成就，可能为隐私或无成就。")
        return
    details = await plugin.achievement_monitor.get_achievement_details(
        group_id, gameid, lang="schinese", api_key=plugin.API_KEY, steamid=steamid
    )
    count = max(1, min(count, len(achievements)))
    unlocked = set(random.sample(list(achievements), count))
    font_path = resolve_font_path('NotoSansHans-Regular.otf')
    try:
        img_bytes = await plugin.achievement_monitor.render_achievement_image(
            details, unlocked, player_name=player_name, font_path=font_path
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name
        yield event.image_result(tmp_path)
    except Exception as exc:
        logger.error(f"成就图片渲染失败: {exc}\n{traceback.format_exc()}")
        msg = plugin.achievement_monitor.render_achievement_message(
            details, unlocked, player_name=player_name
        )
        yield event.plain_result(msg)


async def test_game_start_render(plugin, event, steamid: str, gameid: int):
    try:
        status = await plugin.fetch_player_status(steamid)
        player_name = plugin._resolve_bind_name(steamid, status.get("name") if status else steamid)
        avatar_url = status.get("avatarfull") or status.get("avatar") or "" if status else ""
        zh_game_name, en_game_name = await plugin.get_game_names(gameid)
        logger.info(
            f"[测试开始游戏渲染] steamid={steamid} gameid={gameid} player_name={player_name} "
            f"avatar_url={avatar_url} zh_game_name={zh_game_name} en_game_name={en_game_name}"
        )
        superpower = plugin.superpower.get(steamid)
        print(f"[superpower] test_game_start_render superpower={superpower}")
        font_path = resolve_font_path('NotoSansHans-Regular.otf')
        online_count = await plugin.get_game_online_count(gameid)
        img_bytes = await render_game_start(
            plugin.data_dir, steamid, player_name, avatar_url, gameid, zh_game_name,
            api_key=plugin.API_KEY, superpower=superpower, sgdb_api_key=plugin.SGDB_API_KEY,
            font_path=font_path, sgdb_game_name=en_game_name, online_count=online_count, appid=gameid,
            proxy=plugin.proxy, version=plugin._plugin_version, sgdb_api_base=plugin.SGDB_API_BASE,
            steam_store_base=plugin.STEAM_STORE_BASE,
        )
        logger.info(
            f"[测试开始游戏渲染] render_game_start 返回类型: {type(img_bytes)} "
            f"长度: {len(img_bytes) if img_bytes else 'None'}"
        )
        if img_bytes:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                tmp_path = tmp.name
            img = PILImage.open(tmp_path).convert("RGB")
            cropped_img = crop_image_auto(img, bg_color=(51, 81, 66), threshold=15)
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp2:
                cropped_img.save(tmp2, format="PNG")
                tmp_path = tmp2.name
            logger.info(f"[测试开始游戏渲染] 已保存裁剪图到 {tmp_path}")
            yield event.image_result(tmp_path)
        else:
            yield event.plain_result("渲染失败，未获取到图片数据。")
    except Exception as exc:
        logger.error(f"测试开始游戏图片渲染失败: {exc}\n{traceback.format_exc()}")
        yield event.plain_result(f"渲染异常: {exc}")


async def test_game_end_render(
    plugin,
    event,
    steamid: str,
    gameid: int,
    duration_min: float = 120,
    end_time: str = None,
    tip_text: str = None,
):
    try:
        status = await plugin.fetch_player_status(steamid)
        player_name = plugin._resolve_bind_name(steamid, status.get("name") if status else steamid)
        avatar_url = status.get("avatarfull") or status.get("avatar") or "" if status else ""
        zh_game_name, en_game_name = await plugin.get_game_names(gameid)
        logger.info(f"[get_game_names] zh_game_name={zh_game_name}, en_game_name={en_game_name}")
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
        font_path = resolve_font_path('NotoSansHans-Regular.otf')
        img_bytes = await render_game_end(
            plugin.data_dir, steamid, player_name, avatar_url, gameid, zh_game_name,
            end_time_str, tip_text, duration_h, sgdb_api_key=plugin.SGDB_API_KEY,
            font_path=font_path, sgdb_game_name=en_game_name, appid=gameid,
            proxy=plugin.proxy, api_key=plugin.API_KEY,
        )
        msg = f"👋 {player_name} 不玩 {zh_game_name} 了\n游玩时间 {duration_h:.1f}小时"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name
        yield event.plain_result(msg)
        yield event.image_result(tmp_path)
    except Exception as exc:
        logger.error(f"测试游戏结束图片渲染失败: {exc}\n{traceback.format_exc()}")
        yield event.plain_result(f"渲染异常: {exc}")


async def fonts(plugin, event, param: str = ""):
    service = getattr(plugin, "font_pack", None)
    if service is None:
        yield event.plain_result("字体服务未初始化。")
        return
    action = (param or "").strip().lower()
    if action in ("", "status"):
        yield event.plain_result(service.format_status_text())
        return
    if action == "clean":
        yield event.plain_result(service.clean())
        return
    if action in ("download", "update"):
        last_sent = 0.0

        async def progress_cb(payload):
            nonlocal last_sent
            now = time.time()
            if not payload.get("force") and now - last_sent < 2:
                return
            last_sent = now
            text = (
                "正在下载字体包...\n"
                f"{payload['bar']}\n"
                f"预计剩余：{payload['eta_text']}"
            )
            try:
                await event.send(event.plain_result(text))
            except Exception:
                logger.info("[Font] %s", text.replace("\n", " "))

        yield event.plain_result("开始下载字体包，完成后会更新状态。")
        result = await service.download_now(progress_cb=progress_cb)
        yield event.plain_result(result)
        return
    yield event.plain_result("用法：/steam fonts、/steam fonts download、/steam fonts clean")


async def clear_cache(plugin, event):
    try:
        cache_dirs = [
            os.path.join(plugin.data_dir, "avatars"),
            os.path.join(plugin.data_dir, "covers"),
            os.path.join(plugin.data_dir, "covers_v"),
        ]
        cleared = []
        for directory in cache_dirs:
            if os.path.exists(directory):
                shutil.rmtree(directory)
                cleared.append(directory)
        msg = "已清除以下缓存目录：\n" + "\n".join(cleared) if cleared else "未找到任何缓存目录，无需清理。"
        yield event.plain_result(msg)
    except Exception as exc:
        yield event.plain_result(f"清除缓存失败: {exc}")
