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

    def remove_player(self, group_id: str, steam_id: str) -> GroupMutationResult:
        """删除当前群关系：分发群只移除自身路由，主群删除全局主监控与路由。"""
        push_groups = getattr(self._plugin, "push_groups", {}) or {}
        direct_owner = next(
            (
                owner
                for owner, owner_ids in self.groups.items()
                if steam_id in owner_ids
            ),
            None,
        )

        targets = push_groups.get(steam_id, [])
        if str(group_id) != str(direct_owner):
            if str(group_id) not in {str(target) for target in targets}:
                return GroupMutationResult(False, "player not found")
            targets[:] = [target for target in targets if str(target) != str(group_id)]
            if not targets:
                push_groups.pop(steam_id, None)
            self._plugin._save_push_groups()
            return GroupMutationResult(True, "removed push route")

        for owner, owner_ids in list(self.groups.items()):
            self.groups[owner] = [sid for sid in owner_ids if sid != steam_id]
            if not self.groups[owner]:
                del self.groups[owner]
        push_groups.pop(steam_id, None)
        self._plugin._save_group_steam_ids()
        self._plugin._save_push_groups()
        self._plugin.session_service.discard_player(steam_id)
        self._clear_runtime_state(steam_id)
        self._remove_bindings(steam_id)
        return GroupMutationResult(True, "removed primary monitor and all push routes")

    def _clear_runtime_state(self, steam_id: str) -> None:
        state = self._state
        for mapping_name in (
            "group_last_states",
            "group_last_quit_times",
            "next_poll_time",
        ):
            mapping = getattr(state, mapping_name, {})
            for group_id in list(mapping):
                mapping[group_id].pop(steam_id, None)
                if not mapping[group_id]:
                    mapping.pop(group_id, None)
        mapping = getattr(state, "group_pending_logs", {})
        for group_id in list(mapping):
            mapping[group_id].pop(steam_id, None)
            if not mapping[group_id]:
                mapping.pop(group_id, None)
        pending = getattr(state, "pending_end_notifications", {})
        for group_id in list(pending):
            pending[group_id] = [item for item in pending[group_id] if str(item.get("sid", item.get("steamid", ""))) != steam_id]
            if not pending[group_id]:
                pending.pop(group_id, None)
        for attr in ("achievement_poll_tasks", "achievement_snapshots", "achievement_fail_count"):
            cache = getattr(self._plugin, attr, {})
            for key in list(cache):
                if len(key) >= 2 and str(key[1]) == steam_id:
                    value = cache.pop(key, None)
                    if attr == "achievement_poll_tasks" and value:
                        value.cancel()
        self._plugin._save_persistent_data(force=True)

    def _remove_bindings(self, steam_id: str) -> None:
        bindings = getattr(self._plugin, "_bind_data", {})
        removed = [qq for qq, info in bindings.items() if str(info.get("sid")) == steam_id]
        for qq in removed:
            del bindings[qq]
        if removed:
            self._plugin._save_bind_data()

    def add_group(self, group_id: str) -> GroupMutationResult:
        if group_id in self.groups:
            return GroupMutationResult(False, "already exists")
        self.groups[group_id] = []
        self._plugin._save_group_steam_ids()
        return GroupMutationResult(True)

    def remove_group(self, group_id: str) -> bool:
        has_primary = group_id in self.groups
        routed_sids = [
            sid
            for sid, targets in (getattr(self._plugin, "push_groups", {}) or {}).items()
            if str(group_id) in {str(target) for target in targets}
        ]
        if not has_primary and not routed_sids:
            return False

        push_groups = getattr(self._plugin, "push_groups", {}) or {}
        for sid in list(self.groups.get(group_id, [])):
            push_groups.pop(sid, None)
        for sid in routed_sids:
            targets = [target for target in push_groups.get(sid, []) if str(target) != str(group_id)]
            if targets:
                push_groups[sid] = targets
            else:
                push_groups.pop(sid, None)

        self.groups.pop(group_id, None)
        self._state.group_last_states.pop(group_id, None)
        self._state.group_last_quit_times.pop(group_id, None)
        self._state.group_pending_logs.pop(group_id, None)
        self._plugin.session_service.discard_group(group_id)
        self._state.group_recent_games.pop(group_id, None)
        self._state.next_poll_time.pop(group_id, None)
        self._plugin._save_group_steam_ids()
        self._plugin._save_push_groups()
        self._plugin._save_persistent_data(force=True)
        return True

    def list_group_players(self, group_id: str) -> List[Dict[str, Any]]:
        direct_ids = [str(sid) for sid in self.groups.get(group_id, [])]
        push_ids = [
            str(sid)
            for sid, target_groups in (getattr(self._plugin, "push_groups", {}) or {}).items()
            if str(group_id) in {str(target) for target in target_groups}
        ]
        steam_ids = list(dict.fromkeys([*direct_ids, *push_ids]))
        group_states = self._state.group_last_states.get(group_id, {})
        primary_states = self._state.group_last_states
        return [
            {
                "sid": sid,
                "state": group_states.get(sid)
                or next(
                    (
                        states.get(sid, {})
                        for owner_group, owner_ids in self.groups.items()
                        if sid in {str(owner_sid) for owner_sid in owner_ids}
                        for states in [primary_states.get(owner_group, {})]
                    ),
                    {},
                ),
            }
            for sid in steam_ids
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
