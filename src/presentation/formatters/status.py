from typing import Any, Mapping, Optional


_PERSONA_TEXT = {1: "在线", 2: "忙碌", 3: "离开", 4: "打盹"}
_PERSONA_ICON = {1: "🟡", 2: "🔴", 3: "🟣", 4: "🟣"}


def format_player_status(
    *,
    name: str,
    game_name: Optional[str],
    gameid: Any,
    personastate: Any,
    lastlogoff: Any,
    now: int,
    poll_level: str,
) -> str:
    """将 Steam 玩家快照格式化为监控日志行。"""
    if gameid:
        return f"🟢【{name}】正在玩 {game_name or '未知游戏'}（{poll_level}）"

    try:
        state = int(personastate or 0)
    except (TypeError, ValueError):
        state = 0
    if state > 0:
        return f"{_PERSONA_ICON.get(state, '🟡')}【{name}】{_PERSONA_TEXT.get(state, '在线')}（{poll_level}）"

    if lastlogoff:
        hours_ago = (now - int(lastlogoff)) / 3600
        return f"⚪️【{name}】离线 上次在线 {hours_ago:.1f} 小时前（{poll_level}）"
    return f"⚪️【{name}】离线（{poll_level}）"


def format_play_duration(duration_min: float) -> str:
    """格式化游戏持续时间，供通知适配器复用。"""
    if duration_min < 60:
        return f"{duration_min:.1f}分钟"
    return f"{duration_min / 60:.1f}小时"
