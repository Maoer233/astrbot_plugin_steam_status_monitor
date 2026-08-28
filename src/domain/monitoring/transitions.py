from dataclasses import dataclass
from typing import Any, Mapping, Optional


_EMPTY_GAME_IDS = (None, "", "0")
_NETWORK_FLUCTUATION_WINDOW = 180


@dataclass(frozen=True)
class GameTransition:
    """描述一次 Steam 游戏状态变化，不依赖插件或外部服务。"""

    kind: str
    previous_gameid: Optional[str]
    current_gameid: Optional[str]

    network_fluctuation: bool = False

    @property
    def has_exit(self) -> bool:
        return self.kind in {"exit", "switch"}

    @property
    def has_start(self) -> bool:
        return self.kind in {"start", "switch"}

    @property
    def is_network_fluctuation(self) -> bool:
        return self.network_fluctuation


def classify_game_transition(
    previous_state: Optional[Mapping[str, Any]],
    current_state: Mapping[str, Any],
    *,
    pending_quit: Optional[Mapping[str, Any]] = None,
    now: Optional[int] = None,
) -> GameTransition:
    """将原始 Steam 状态归类为首次、开始、退出、切换或网络波动。"""
    previous_gameid = previous_state.get("gameid") if previous_state else None
    current_gameid = current_state.get("gameid")

    if previous_state is None:
        kind = "start" if current_gameid not in _EMPTY_GAME_IDS else "initial"
    elif previous_gameid and (
        current_gameid in _EMPTY_GAME_IDS or current_gameid != previous_gameid
    ):
        kind = "switch" if current_gameid not in _EMPTY_GAME_IDS else "exit"
    elif current_gameid not in _EMPTY_GAME_IDS and current_gameid != previous_gameid:
        kind = "start"
    else:
        kind = "unchanged"

    is_network_fluctuation = False
    if (
        kind in {"start", "switch"}
        and pending_quit
        and now is not None
        and current_gameid in pending_quit
    ):
        quit_info = pending_quit[current_gameid]
        quit_time = quit_info.get("quit_time")
        if (
            quit_time is not None
            and 0 <= now - quit_time <= _NETWORK_FLUCTUATION_WINDOW
            and not quit_info.get("notified")
        ):
            is_network_fluctuation = True

    return GameTransition(
        kind,
        previous_gameid,
        current_gameid,
        network_fluctuation=is_network_fluctuation,
    )
