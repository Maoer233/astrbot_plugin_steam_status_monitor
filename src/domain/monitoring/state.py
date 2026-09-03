from dataclasses import dataclass, field
from typing import Any, Dict, List, MutableMapping, Tuple


GroupId = str
SteamId = str
GameId = str
PlayerState = Dict[str, Any]


@dataclass
class MonitorStateStore:
    """Steam 监控运行状态的统一所有者。"""

    group_steam_ids: Dict[GroupId, List[SteamId]] = field(default_factory=dict)
    group_last_states: Dict[GroupId, Dict[SteamId, PlayerState]] = field(default_factory=dict)
    group_last_quit_times: Dict[GroupId, Dict[SteamId, Dict[GameId, int]]] = field(default_factory=dict)
    group_pending_logs: Dict[GroupId, Dict[str, Any]] = field(default_factory=dict)
    group_recent_games: Dict[GroupId, List[GameId]] = field(default_factory=dict)
    playing_sessions: Dict[Tuple[GroupId, SteamId], Any] = field(default_factory=dict)
    next_poll_time: Dict[GroupId, Dict[SteamId, float]] = field(default_factory=dict)
    startup_stale_groups: Dict[GroupId, bool] = field(default_factory=dict)
    pending_end_notifications: Dict[GroupId, List[Dict[str, Any]]] = field(default_factory=dict)


class StateBackedMonitorMixin:
    """保留旧字段接口，将共享状态统一代理到 ``monitor_state``。"""

    monitor_state: MonitorStateStore

    def _state(self) -> MonitorStateStore:
        state = getattr(self, "monitor_state", None)
        if state is None:
            state = MonitorStateStore()
            self.monitor_state = state
        return state


def _state_property(attribute: str):
    def getter(instance: StateBackedMonitorMixin):
        return getattr(instance._state(), attribute)

    def setter(instance: StateBackedMonitorMixin, value: MutableMapping):
        setattr(instance._state(), attribute, value)

    return property(getter, setter)


for _legacy_name, _state_name in {
    "group_steam_ids": "group_steam_ids",
    "group_last_states": "group_last_states",
    "group_last_quit_times": "group_last_quit_times",
    "group_pending_logs": "group_pending_logs",
    "group_recent_games": "group_recent_games",
    "playing_sessions": "playing_sessions",
    "next_poll_time": "next_poll_time",
    "_startup_stale_groups": "startup_stale_groups",
    "_pending_end_notifications": "pending_end_notifications",
}.items():
    setattr(StateBackedMonitorMixin, _legacy_name, _state_property(_state_name))
