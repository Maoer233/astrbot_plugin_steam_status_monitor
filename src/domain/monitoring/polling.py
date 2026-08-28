from typing import Optional, Sequence, Tuple


DEFAULT_POLL_INTERVALS = (1, 3, 5, 10, 20, 30)


def calculate_poll_schedule(
    *,
    now: int,
    gameid: Optional[str],
    personastate: object,
    lastlogoff: object,
    fixed_interval: int,
    intervals: Optional[Sequence[int]] = None,
) -> Tuple[int, str]:
    """根据玩家状态计算下一次轮询间隔。

    该函数只处理轮询策略，不依赖 AstrBot、Steam 客户端或插件实例，
    便于独立测试及未来复用。
    """
    configured = list(intervals or DEFAULT_POLL_INTERVALS)
    if len(configured) != 6:
        configured = list(DEFAULT_POLL_INTERVALS)

    if fixed_interval and fixed_interval > 0:
        label = (
            f"固定{fixed_interval // 60 if fixed_interval >= 60 else fixed_interval}"
            f"{'分钟' if fixed_interval >= 60 else '秒'}轮询"
        )
        return fixed_interval, label

    if gameid:
        level = 0
    elif personastate and int(personastate) > 0:
        level = 1
    elif lastlogoff:
        minutes_ago = (now - int(lastlogoff)) / 60
        if minutes_ago <= 12:
            level = 1
        elif minutes_ago <= 180:
            level = 2
        elif minutes_ago <= 1440:
            level = 3
        elif minutes_ago <= 2880:
            level = 4
        else:
            level = 5
    else:
        level = 5

    minutes = configured[level]
    return minutes * 60, f"{minutes}分钟轮询"
