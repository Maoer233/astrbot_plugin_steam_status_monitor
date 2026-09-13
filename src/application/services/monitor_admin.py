from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...domain.monitoring import MonitorStateStore
from ...shared.logging import logger
from ...shared.utils.notify_session import is_valid_group_id


@dataclass(frozen=True)
class GroupMutationResult:
    changed: bool
    message: str = ""


@dataclass(frozen=True)
class AddPlayersResult:
    added: List[str] = field(default_factory=list)
    started: bool = False
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

    def add_player(self, group_id: str, steam_id: str) -> GroupMutationResult:
        if not is_valid_group_id(group_id):
            return GroupMutationResult(False, "invalid group_id")
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

    def add_players(
        self,
        group_id: str,
        steam_ids: List[str],
        *,
        bind_qq: Optional[str] = None,
        bind_nickname: Optional[str] = None,
        notify_session: Optional[str] = None,
    ) -> AddPlayersResult:
        added = []
        pushed = []
        already = []
        already_pushed = []
        binding_updated = []
        pushed_primary_groups = {}
        limit = self.max_group_size
        for sid in steam_ids:
            result = self.add_player(group_id, sid)
            if result.message == "already exists":
                already.append(sid)
                if bind_qq or bind_nickname:
                    binding_updated.append(sid)
                continue
            if result.message == "already push group":
                already_pushed.append(sid)
                pushed_primary_groups[sid] = self.primary_group_of(sid)
                continue
            if result.message == "added as push group":
                pushed.append(sid)
                pushed_primary_groups[sid] = self.primary_group_of(sid)
                continue
            if result.message == "added as primary monitor":
                added.append(sid)
                continue
            if "group limit reached" in result.message:
                break
        if steam_ids and (bind_qq or bind_nickname):
            for sid in steam_ids:
                self.bind_player(sid, qq=bind_qq, nickname=bind_nickname)
            logger.info(
                f"[绑定] {'QQ'+str(bind_qq) if bind_qq else '备注'} -> SteamID {steam_ids[-1]}，备注={bind_nickname or '无'}"
            )
        msg = ""
        if added:
            msg += f"已为本群添加SteamID: {', '.join(added)}\n"
        if pushed:
            push_details = []
            for sid in pushed:
                primary_group = pushed_primary_groups.get(sid)
                suffix = f"（主监控群：{primary_group}）" if primary_group else ""
                push_details.append(f"{sid}{suffix}")
            msg += (
                "以下SteamID已被其他群监控，当前群不会重复监控，已自动设置为分发路由（push_group）："
                f"{', '.join(push_details)}\n"
            )
        if binding_updated:
            msg += f"以下SteamID已在本群监控，备注/绑定已更新：{', '.join(binding_updated)}\n"
        already_plain = [sid for sid in already if sid not in binding_updated]
        if already_plain:
            msg += f"以下SteamID已经在本群监控，无需重复添加：{', '.join(already_plain)}\n"
        if already_pushed:
            push_details = []
            for sid in already_pushed:
                primary_group = pushed_primary_groups.get(sid)
                suffix = f"（主监控群：{primary_group}）" if primary_group else ""
                push_details.append(f"{sid}{suffix}")
            msg += f"以下SteamID已经是本群的分发路由（push_group），无需重复添加：{', '.join(push_details)}\n"
        unhandled = len(steam_ids) - len(added) - len(pushed) - len(already) - len(already_pushed)
        if unhandled:
            msg += f"本群监控组人数已达上限（{limit}人），部分ID未添加。\n"
        started = False
        if added and self._plugin.monitor_control.ensure_running(group_id, notify_session=notify_session):
            started = True
            msg += "监控已自动启动。\n"
        return AddPlayersResult(
            added=added,
            started=started,
            message=msg.strip() if msg else "未添加任何SteamID。",
        )

    def primary_group_of(self, steam_id: str) -> Optional[str]:
        return next(
            (group_id for group_id, steam_ids in self.groups.items() if steam_id in steam_ids),
            None,
        )

    def bind_player(self, steam_id: str, qq: Optional[str] = None, nickname: Optional[str] = None) -> bool:
        if not qq and not nickname:
            return False
        bindings = self.bindings
        if qq:
            bindings[qq] = {"sid": str(steam_id), "nickname": nickname or "*"}
        else:
            matched = False
            for key, info in list(bindings.items()):
                if str(info.get("sid")) == str(steam_id):
                    bindings[key]["nickname"] = nickname
                    matched = True
            if not matched:
                bindings[f"__remark:{steam_id}"] = {"sid": str(steam_id), "nickname": nickname}
        self._plugin._save_bind_data()
        return True

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
                # 群已空，停止该群的监控轮询
                running_groups = getattr(self._plugin, 'running_groups', set())
                running_groups.discard(owner)
                monitor_enabled = getattr(self._plugin, 'group_monitor_enabled', {})
                monitor_enabled.pop(owner, None)
                notify_sessions = getattr(self._plugin, 'notify_sessions', {})
                notify_sessions.pop(owner, None)
                self._plugin._save_notify_session()
                self._plugin._save_group_switches()
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
        if not is_valid_group_id(group_id):
            return GroupMutationResult(False, "invalid group_id")
        if group_id in self.groups:
            return GroupMutationResult(False, "already exists")
        self.groups[group_id] = []
        self._plugin._save_group_steam_ids()
        return GroupMutationResult(True)

    def add_push_route(self, group_id: str, steam_id: str) -> GroupMutationResult:
        if not str(steam_id).isdigit() or len(str(steam_id)) != 17:
            return GroupMutationResult(False, "SteamID无效（需为64位数字串，17位）")
        if self.primary_group_of(steam_id) is None:
            return GroupMutationResult(False, "未找到已轮询该SteamID的主群，请先在任一群添加并开启监控。")
        push_groups = getattr(self._plugin, "push_groups", None)
        if push_groups is None:
            push_groups = self._plugin.push_groups = {}
        targets = push_groups.setdefault(steam_id, [])
        if group_id in targets:
            return GroupMutationResult(False, "本群已在该SteamID的推送组中。")
        targets.append(group_id)
        self._plugin._save_push_groups()
        return GroupMutationResult(True, f"本群已加入SteamID {steam_id} 的联动推送组。")

    def remove_push_route(
        self,
        group_id: str,
        steam_id: str,
        *,
        explicit_target: bool = False,
    ) -> GroupMutationResult:
        if not str(steam_id).isdigit() or len(str(steam_id)) != 17:
            return GroupMutationResult(False, "SteamID无效（需为64位数字串，17位）")
        push_groups = getattr(self._plugin, "push_groups", {}) or {}
        targets = push_groups.get(steam_id, [])
        if group_id not in targets:
            return GroupMutationResult(False, f"群 {group_id} 未在 SteamID {steam_id} 的推送组中。")
        targets.remove(group_id)
        if not targets:
            push_groups.pop(steam_id, None)
        self._plugin._save_push_groups()
        if explicit_target:
            return GroupMutationResult(True, f"已从 SteamID {steam_id} 的联动推送组中移除群 {group_id}。")
        return GroupMutationResult(True, f"本群已从 SteamID {steam_id} 的联动推送组移除。")

    def remove_group(self, group_id: str) -> GroupMutationResult:
        plugin = self._plugin
        has_primary = group_id in self.groups
        routed_sids = [
            sid
            for sid, targets in (getattr(plugin, "push_groups", {}) or {}).items()
            if str(group_id) in {str(target) for target in targets}
        ]
        if not has_primary and not routed_sids:
            return GroupMutationResult(False, f"群聊 {group_id} 未绑定任何SteamID，无需清理。")

        push_groups = getattr(plugin, "push_groups", {}) or {}
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
        plugin.session_service.discard_group(group_id)
        self._state.group_recent_games.pop(group_id, None)
        self._state.next_poll_time.pop(group_id, None)
        getattr(plugin, "running_groups", set()).discard(group_id)
        getattr(plugin, "group_monitor_enabled", {}).pop(group_id, None)
        getattr(plugin, "group_achievement_enabled", {}).pop(group_id, None)
        getattr(plugin, "notify_sessions", {}).pop(group_id, None)
        tasks = getattr(plugin, "achievement_poll_tasks", {})
        for key in [item for item in list(tasks.keys()) if item[0] == group_id]:
            task = tasks.pop(key, None)
            if task:
                task.cancel()
        snapshots = getattr(plugin, "achievement_snapshots", {})
        for key in [item for item in list(snapshots.keys()) if item[0] == group_id]:
            snapshots.pop(key, None)
        plugin._save_group_steam_ids()
        plugin._save_push_groups()
        plugin._save_notify_session()
        plugin._save_group_switches()
        plugin._save_persistent_data(force=True)
        if hasattr(getattr(plugin, "config", None), "save_config"):
            plugin.config.save_config()
        return GroupMutationResult(True, f"已删除群聊 {group_id} 的所有SteamID和分发路由，相关状态数据已清空。")

    def clear_all_ids(self) -> GroupMutationResult:
        plugin = self._plugin
        for task in list(getattr(plugin, "achievement_poll_tasks", {}).values()):
            if task:
                task.cancel()
        getattr(plugin, "achievement_poll_tasks", {}).clear()
        getattr(plugin, "achievement_snapshots", {}).clear()
        getattr(plugin, "achievement_fail_count", {}).clear()
        plugin.group_steam_ids.clear()
        getattr(plugin, "push_groups", {}).clear()
        getattr(plugin, "running_groups", set()).clear()
        getattr(plugin, "group_monitor_enabled", {}).clear()
        getattr(plugin, "group_achievement_enabled", {}).clear()
        plugin._save_group_switches()
        plugin.next_poll_time.clear()
        plugin.group_last_states.clear()
        plugin.group_last_quit_times.clear()
        plugin.group_pending_logs.clear()
        plugin.playing_sessions.clear()
        getattr(plugin, "_session_meta", {}).clear()
        plugin.group_recent_games.clear()
        plugin._pending_end_notifications.clear()
        getattr(plugin, "notify_sessions", {}).clear()
        plugin._save_group_steam_ids()
        plugin._save_push_groups()
        plugin._save_notify_session()
        plugin._save_persistent_data(force=True)
        config = getattr(plugin, "config", None)
        if isinstance(config, dict) or hasattr(config, "__setitem__"):
            config["group_steam_ids"] = plugin.group_steam_ids
        if hasattr(config, "save_config"):
            config.save_config()
        return GroupMutationResult(True, "已删除所有群聊的所有SteamID，相关状态数据已清空。")

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
