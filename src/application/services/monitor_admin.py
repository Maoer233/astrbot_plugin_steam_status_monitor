from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ...domain.monitoring import MonitorStateStore


@dataclass(frozen=True)
class GroupMutationResult:
    changed: bool
    message: str = ""


class MonitorAdminService:
    """管理后台使用的状态查询与修改边界。"""

    def __init__(self, plugin):
        self._plugin = plugin
        self._state: MonitorStateStore = plugin.monitor_state

    @property
    def groups(self) -> Dict[str, List[str]]:
        return self._state.group_steam_ids

    @property
    def bindings(self) -> Dict[str, Dict[str, str]]:
        return self._plugin._bind_data

    @property
    def max_group_size(self) -> int:
        return self._plugin.max_group_size

    async def resolve_steam_input(self, value: str) -> Optional[str]:
        return await self._plugin.resolve_steam_input(value)

    def add_player(self, group_id: str, steam_id: str) -> GroupMutationResult:
        steam_ids = self.groups.setdefault(group_id, [])
        if steam_id in steam_ids:
            return GroupMutationResult(False, "already exists")

        primary_group = next(
            (
                candidate
                for candidate, candidate_ids in self.groups.items()
                if candidate != group_id and steam_id in candidate_ids
            ),
            None,
        )
        if primary_group is not None:
            push_groups = getattr(self._plugin, "push_groups", None)
            if push_groups is None:
                push_groups = self._plugin.push_groups = {}
            targets = push_groups.setdefault(steam_id, [])
            if group_id not in targets:
                targets.append(group_id)
                self._plugin._save_push_groups()
                return GroupMutationResult(True, "added as push group")
            return GroupMutationResult(False, "already push group")

        if len(steam_ids) >= self.max_group_size:
            return GroupMutationResult(
                False,
                f"group limit reached ({self.max_group_size})",
            )
        steam_ids.append(steam_id)
        self._plugin._save_group_steam_ids()
        return GroupMutationResult(True, "added as primary monitor")

    def remove_player(self, group_id: str, steam_id: str) -> bool:
        steam_ids = self.groups.get(group_id)
        if not steam_ids or steam_id not in steam_ids:
            return False
        steam_ids.remove(steam_id)
        if not steam_ids:
            del self.groups[group_id]
        self._plugin._save_group_steam_ids()
        return True

    def add_group(self, group_id: str) -> GroupMutationResult:
        if group_id in self.groups:
            return GroupMutationResult(False, "already exists")
        self.groups[group_id] = []
        self._plugin._save_group_steam_ids()
        return GroupMutationResult(True)

    def remove_group(self, group_id: str) -> bool:
        if group_id not in self.groups:
            return False
        del self.groups[group_id]
        self._plugin._save_group_steam_ids()
        return True

    def list_group_players(self, group_id: str) -> List[Dict[str, Any]]:
        last_states = self._state.group_last_states.get(group_id, {})
        return [
            {
                "sid": sid,
                "state": last_states.get(sid, {}),
            }
            for sid in self.groups.get(group_id, [])
        ]

    def set_binding(self, qq: str, steam_id: str, nickname: str = "") -> None:
        self.bindings[qq] = {"sid": steam_id, "nickname": nickname}
        self._plugin._save_bind_data()

    def remove_binding(self, qq: str) -> bool:
        if qq not in self.bindings:
            return False
        del self.bindings[qq]
        self._plugin._save_bind_data()
        return True

    def update_binding_nickname(self, qq: str, nickname: str) -> bool:
        if qq not in self.bindings:
            return False
        self.bindings[qq]["nickname"] = nickname
        self._plugin._save_bind_data()
        return True
