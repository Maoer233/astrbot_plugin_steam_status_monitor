import base64
import re
import tempfile

from ...application.services.openbox import handle_openbox
from ...application.services.price_query import contains_chinese
from ...presentation.renderers.game_detail import render_game_detail_image
from ...shared.fonts import resolve_font_path
from ...shared.logging import logger
from ...shared.utils.price import extract_price_query


def search_session_key(event) -> str:
    origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
    if origin:
        return origin
    session_id = str(event.get_session_id() or "").strip()
    return session_id or "default"


async def translate_game_query(plugin, query: str) -> str:
    if not contains_chinese(query):
        return query
    try:
        provider = plugin.context.get_using_provider()
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
        raw = str(response.completion_text or "").strip()
        closes = list(re.finditer(r"</(?:thinking|think)[^>]*>", raw, flags=re.I))
        if closes:
            raw = raw[closes[-1].end():]
        else:
            raw = re.sub(r"<think[^>]*>", "", raw, flags=re.I)
            raw = re.sub(r"</?(?:thinking|think)[^>]*>", "", raw, flags=re.I)
        translated = re.sub(
            r"^(?:英文名|翻译结果|Translation)\s*[:：]?\s*",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip().strip('`\"“”')
        if translated:
            logger.info("[LLM][翻译游戏名] %s -> %s", query, translated)
            return translated
    except Exception as exc:
        logger.warning("LLM 翻译游戏名失败，将使用原始查询: %s", exc)
    return query


async def game(plugin, event, appid: str):
    appid = str(appid).strip()
    if not appid.isdigit():
        yield event.plain_result("用法：/steam game <Steam AppID>")
        return
    card = await plugin.price_query.build_store_card(appid)
    if not card or not card.detail:
        yield event.plain_result(f"未找到 Steam 游戏 AppID：{appid}，或 Steam 商店暂时无法访问。")
        return
    try:
        img_bytes = await render_game_detail_image(
            card.card_data,
            font_path=resolve_font_path("NotoSansHans-Regular.otf"),
            proxy=plugin.proxy,
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(img_bytes)
            image_path = tmp.name
        yield event.image_result(image_path)
    except Exception as exc:
        logger.exception("渲染 Steam 游戏详情卡片失败: %s", exc)
        yield event.plain_result(f"游戏详情获取成功，但卡片生成失败：{exc}")


async def handle_selection(plugin, event):
    session_key = search_session_key(event)
    cache = plugin.price_candidates
    if cache.get(session_key) is None and not cache.has_expired(session_key):
        return
    message = str(event.get_message_str() or "").strip()
    if message.startswith("/"):
        return
    match = re.search(r"([1-9]\d?)\s*$", message)
    if not match:
        return
    event.stop_event()
    async for result in price(plugin, event, False, "price"):
        yield result


async def price(plugin, event, auto_first: bool, prefix: str):
    raw_msg = getattr(event, "message_str", None)
    if raw_msg is None:
        getter = getattr(event, "get_message_str", None)
        raw_msg = getter() if callable(getter) else ""
    query = extract_price_query(str(raw_msg or ""), prefix)
    if not query:
        yield event.plain_result(f"用法：/steam {prefix} <游戏名或 Steam 链接>")
        return
    session_key = search_session_key(event)
    cache = plugin.price_candidates
    selected = None
    if not auto_first and query.isdigit():
        selected = cache.get(session_key)
        if selected is None and cache.has_expired(session_key):
            cache.take_expired(session_key)
            yield event.plain_result("候选已过期，请重新查询。")
            return
        if selected is None:
            yield event.plain_result("没有待选择的候选，请重新查询游戏名。")
            return
        index = int(query) - 1
        if 0 <= index < len(selected.games):
            game_item = selected.games[index]
        else:
            yield event.plain_result("候选序号无效，请重新回复序号。")
            return
    if selected is None:
        resolved = await plugin.price_query.resolve_games(query)
        games = list(resolved.get("games") or [])
        if not games:
            yield event.plain_result(plugin.price_query._search_message(resolved.get("status")))
            return
        game_item = games[0]
        if not auto_first and len(games) > 1:
            current = cache.get(session_key)
            if current is not None:
                yield event.plain_result(
                    f"已有未完成的查询 #{current.query_id}，请先回复序号，或等待过期后重新查询。"
                )
                return
            saved = cache.put(session_key, games)
            lines = [f"找到多个匹配游戏，请回复序号（查询 #{saved.query_id}）："]
            for index, item in enumerate(saved.games, 1):
                lines.append(f"{index}. {item.title}")
            yield event.plain_result("\n".join(lines))
            return
    try:
        card = await plugin.price_query.build_card(game_item)
    except TimeoutError:
        cache.pop(session_key)
        yield event.plain_result("价格查询超时，请稍后重试。")
        return
    except Exception:
        logger.exception("构建 Steam 价格卡失败 (query=%s)", query)
        cache.pop(session_key)
        yield event.plain_result("价格服务暂时不可用，请稍后重试。")
        return
    cache.pop(session_key)
    try:
        img_bytes = await render_game_detail_image(
            card.card_data,
            font_path=resolve_font_path("NotoSansHans-Regular.otf"),
            proxy=plugin.proxy,
            itad_summary=card.summary,
            region_prices=card.region_prices,
            current_price=card.current_price,
            history_low=card.history_low,
        )
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(img_bytes)
            image_path = tmp.name
        with open(image_path, "rb") as image_file:
            image_base64 = base64.b64encode(image_file.read()).decode("ascii")
        result = event.make_result().base64_image(image_base64)
        if card.store_message:
            result.message(card.store_message)
        yield result
        return
    except Exception:
        logger.exception("渲染 Steam 价格详情卡片失败")
        if card.store_message:
            yield event.plain_result(f"价格已获取，但卡片生成失败。\n{card.store_message}")
        else:
            yield event.plain_result("价格已获取，但卡片生成失败，请稍后重试。")
        return

    if card.store_message:
        yield event.plain_result(card.store_message)
    else:
        yield event.plain_result("未找到对应的 Steam 商店链接。")


async def openbox(plugin, event, steamid: str):
    if not plugin.API_KEY:
        yield event.plain_result("未配置 Steam API Key，请先在插件配置中填写 steam_api_key。")
        return
    sid = await plugin.resolve_steam_input(steamid)
    if not sid or not sid.isdigit() or len(sid) != 17:
        yield event.plain_result("无法解析为有效SteamID，支持格式：17位SteamID64 / 个人资料链接 / 自定义ID链接 / 8位好友码")
        return
    async for result in handle_openbox(plugin, event, sid):
        yield result
