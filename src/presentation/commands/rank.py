import traceback

from ...application.services.rank_view import parse_rank_period
from ...shared.logging import logger


async def render_and_send(plugin, event, group_id, days, period_label, is_all=False):
    try:
        tmp_path = await plugin.rank_view.render_aggregated(
            days=days,
            period_label=period_label,
            group_id=None if is_all else group_id,
        )
        if not tmp_path:
            yield event.plain_result(f"暂无{period_label}游玩记录，玩家游戏结束后才会有数据。")
            return
        yield event.image_result(tmp_path)
    except Exception as exc:
        logger.error(f"[排行榜] 渲染失败: {exc}\n{traceback.format_exc()}")
        yield event.plain_result(f"排行榜生成失败: {exc}")


async def rank(plugin, event, period: str = ""):
    group_id = event.get_group_id() or "default"
    days, label = parse_rank_period(period)
    async for result in render_and_send(plugin, event, group_id, days, label, is_all=False):
        yield result


async def allrank(plugin, event, period: str = ""):
    days, label = parse_rank_period(period)
    async for result in render_and_send(plugin, event, None, days, label, is_all=True):
        yield result


async def rank_on(plugin, event, param: str = ""):
    result = plugin.rank_view.configure_push(param, event.get_group_id() or "default")
    yield event.plain_result(result.message)
    if result.should_push:
        await plugin.rank_view.push_daily()
