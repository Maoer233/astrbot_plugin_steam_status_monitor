"""测试主群监控删除时的状态晋升机制"""
from src.application.services.monitor_admin import MonitorAdminService
from src.domain.monitoring import MonitorStateStore


class PluginStub:
    max_group_size = 5

    def __init__(self, groups, push_groups=None):
        self.monitor_state = MonitorStateStore(group_steam_ids=groups)
        self.push_groups = push_groups or {}
        self._bind_data = {}
        self.group_steam_ids_saves = 0
        self.push_groups_saves = 0
        self.persistent_saves = 0
        self.bind_data_saves = 0
        self.notify_saves = 0
        self.switch_saves = 0
        self.running_groups = set()
        self.group_monitor_enabled = {}
        self.group_achievement_enabled = {}
        self.notify_sessions = {}
        self.achievement_poll_tasks = {}
        self.achievement_snapshots = {}
        self.achievement_fail_count = {}
        self._session_meta = {}
        self.config = {}

    @property
    def group_steam_ids(self):
        return self.monitor_state.group_steam_ids

    @property
    def group_last_states(self):
        return self.monitor_state.group_last_states

    @property
    def group_last_quit_times(self):
        return self.monitor_state.group_last_quit_times

    @property
    def group_pending_logs(self):
        return self.monitor_state.group_pending_logs

    @property
    def group_recent_games(self):
        return self.monitor_state.group_recent_games

    @property
    def playing_sessions(self):
        return self.monitor_state.playing_sessions

    @property
    def next_poll_time(self):
        return self.monitor_state.next_poll_time

    @property
    def _pending_end_notifications(self):
        return self.monitor_state.pending_end_notifications

    def _save_group_steam_ids(self):
        self.group_steam_ids_saves += 1

    def _save_push_groups(self):
        self.push_groups_saves += 1

    def _save_persistent_data(self, force=False):
        self.persistent_saves += 1

    def _save_bind_data(self):
        self.bind_data_saves += 1

    def _save_notify_session(self):
        self.notify_saves += 1

    def _save_group_switches(self):
        self.switch_saves += 1

    @property
    def session_service(self):
        class _Stub:
            def discard_player(self, steam_id):
                return None

            def discard_group(self, group_id):
                return None

        return _Stub()


def test_remove_primary_with_single_push_route_promotes_it():
    """删除主群时，若只有一个分发路由群，应晋升为新主群"""
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]}, {sid: ["222"]})
    plugin.monitor_state.group_last_states = {"111": {sid: {"gameid": "730", "name": "Player"}}}
    plugin.monitor_state.next_poll_time = {"111": {sid: 100.0}}
    service = MonitorAdminService(plugin)

    result = service.remove_player("111", sid)

    assert result.changed is True
    assert "promoted 222 as new primary" in result.message
    # 新主群应接管监控
    assert plugin.group_steam_ids == {"222": [sid]}
    # 分发路由表应清空该 sid
    assert plugin.push_groups == {}
    # 状态数据应迁移到新主群
    assert plugin.monitor_state.group_last_states == {"222": {sid: {"gameid": "730", "name": "Player"}}}
    assert plugin.monitor_state.next_poll_time == {"222": {sid: 100.0}}


def test_remove_primary_with_multiple_push_routes_promotes_first():
    """删除主群时，若有多个分发路由群，晋升第一个，其余保留为分发路由"""
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]}, {sid: ["222", "333", "444"]})
    plugin.monitor_state.group_last_states = {"111": {sid: {"gameid": "570"}}}
    service = MonitorAdminService(plugin)

    result = service.remove_player("111", sid)

    assert result.changed is True
    assert "promoted 222 as new primary" in result.message
    # 222 晋升为主群
    assert plugin.group_steam_ids == {"222": [sid]}
    # 333, 444 仍为分发路由
    assert plugin.push_groups == {sid: ["333", "444"]}
    # 状态迁移到 222
    assert plugin.monitor_state.group_last_states == {"222": {sid: {"gameid": "570"}}}


def test_remove_primary_prefers_running_group_for_promotion():
    """晋升优先选择已启用监控的分发路由群"""
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]}, {sid: ["222", "333"]})
    plugin.running_groups = {"333"}  # 333 已启动监控
    plugin.monitor_state.group_last_states = {"111": {sid: {"gameid": "440"}}}
    service = MonitorAdminService(plugin)

    result = service.remove_player("111", sid)

    assert result.changed is True
    assert "promoted 333 as new primary" in result.message
    # 333 晋升（因为它已启用监控）
    assert plugin.group_steam_ids == {"333": [sid]}
    # 222 仍为分发路由
    assert plugin.push_groups == {sid: ["222"]}


def test_remove_primary_without_push_routes_fully_deletes():
    """删除主群时，若无分发路由群，执行完全删除"""
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]}, {})
    plugin.monitor_state.group_last_states = {"111": {sid: {"gameid": "730"}}}
    plugin.monitor_state.next_poll_time = {"111": {sid: 50.0}}
    service = MonitorAdminService(plugin)

    result = service.remove_player("111", sid)

    assert result.changed is True
    assert result.message == "removed primary monitor and all push routes"
    # 完全删除
    assert plugin.group_steam_ids == {}
    assert plugin.push_groups == {}
    assert plugin.monitor_state.group_last_states == {}
    assert plugin.monitor_state.next_poll_time == {}


def test_remove_push_route_does_not_trigger_promotion():
    """删除分发路由群不触发晋升逻辑"""
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]}, {sid: ["222", "333"]})
    service = MonitorAdminService(plugin)

    result = service.remove_player("222", sid)

    assert result.changed is True
    assert result.message == "removed push route"
    # 主群不变
    assert plugin.group_steam_ids == {"111": [sid]}
    # 仅移除 222
    assert plugin.push_groups == {sid: ["333"]}


def test_promotion_migrates_all_state_data():
    """晋升时应迁移所有状态数据"""
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]}, {sid: ["222"]})
    plugin.monitor_state.group_last_states = {"111": {sid: {"gameid": "730", "name": "Test"}}}
    plugin.monitor_state.group_last_quit_times = {"111": {sid: {"730": 12345}}}
    plugin.monitor_state.next_poll_time = {"111": {sid: 99.9}}
    service = MonitorAdminService(plugin)

    result = service.remove_player("111", sid)

    assert result.changed is True
    # 所有状态数据应迁移
    assert plugin.monitor_state.group_last_states == {"222": {sid: {"gameid": "730", "name": "Test"}}}
    assert plugin.monitor_state.group_last_quit_times == {"222": {sid: {"730": 12345}}}
    assert plugin.monitor_state.next_poll_time == {"222": {sid: 99.9}}


def test_promotion_cleans_original_primary_when_empty():
    """晋升后若原主群为空，应清理其监控状态"""
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]}, {sid: ["222"]})
    plugin.running_groups = {"111"}
    plugin.group_monitor_enabled = {"111": True}
    plugin.notify_sessions = {"111": "session1"}
    service = MonitorAdminService(plugin)

    result = service.remove_player("111", sid)

    assert result.changed is True
    # 原主群 111 应从所有运行时状态中移除
    assert "111" not in plugin.group_steam_ids
    assert "111" not in plugin.running_groups
    assert "111" not in plugin.group_monitor_enabled
    assert "111" not in plugin.notify_sessions


def test_promotion_preserves_original_primary_if_has_other_players():
    """晋升后若原主群还有其他玩家，应保留该群"""
    sid1 = "76561198000000001"
    sid2 = "76561198000000002"
    plugin = PluginStub({"111": [sid1, sid2]}, {sid1: ["222"]})
    plugin.monitor_state.group_last_states = {"111": {sid1: {"gameid": "730"}, sid2: {"gameid": "570"}}}
    service = MonitorAdminService(plugin)

    result = service.remove_player("111", sid1)

    assert result.changed is True
    # 原主群 111 仍保留 sid2
    assert plugin.group_steam_ids == {"111": [sid2], "222": [sid1]}
    # sid1 的状态迁移，sid2 的状态保留在 111
    assert plugin.monitor_state.group_last_states == {
        "111": {sid2: {"gameid": "570"}},
        "222": {sid1: {"gameid": "730"}}
    }
