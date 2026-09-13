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
    def monitor_control(self):
        class _Control:
            def __init__(self, plugin):
                self._plugin = plugin
                plugin.running_groups = getattr(plugin, "running_groups", set())
                plugin.group_monitor_enabled = getattr(plugin, "group_monitor_enabled", {})
                plugin.notify_sessions = getattr(plugin, "notify_sessions", {})

            def ensure_running(self, group_id, notify_session=None):
                if group_id in self._plugin.running_groups:
                    return False
                self._plugin.running_groups.add(group_id)
                self._plugin.group_monitor_enabled[group_id] = True
                if notify_session:
                    self._plugin.notify_sessions[group_id] = notify_session
                if group_id not in self._plugin.monitor_state.group_last_states:
                    self._plugin.monitor_state.group_last_states[group_id] = {}
                return True

        return _Control(self)

    @property
    def session_service(self):
        class _Stub:
            def discard_player(self, steam_id):
                return None

            def discard_group(self, group_id):
                return None

        return _Stub()


def test_add_player_rejects_empty_group_id():
    plugin = PluginStub({})
    service = MonitorAdminService(plugin)

    result = service.add_player("", "76561198000000001")

    assert result.changed is False
    assert result.message == "invalid group_id"
    assert plugin.group_steam_ids == {}


def test_add_group_rejects_empty_group_id():
    plugin = PluginStub({})
    service = MonitorAdminService(plugin)

    result = service.add_group("")

    assert result.changed is False
    assert result.message == "invalid group_id"
    assert plugin.group_steam_ids == {}


def test_add_player_creates_primary_monitor_when_unassigned():
    plugin = PluginStub({"111": []})
    service = MonitorAdminService(plugin)

    result = service.add_player("111", "76561198000000001")

    assert result.changed is True
    assert result.message == "added as primary monitor"
    assert plugin.group_steam_ids["111"] == ["76561198000000001"]
    assert plugin.push_groups == {}
    assert plugin.group_steam_ids_saves == 1
    assert plugin.push_groups_saves == 0


def test_add_player_uses_push_group_when_primary_exists_elsewhere():
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid], "222": []})
    service = MonitorAdminService(plugin)

    result = service.add_player("222", sid)

    assert result.changed is True
    assert result.message == "added as push group"
    assert plugin.group_steam_ids["222"] == []
    assert plugin.push_groups[sid] == ["222"]
    assert plugin.group_steam_ids_saves == 0
    assert plugin.push_groups_saves == 1


def test_add_player_push_group_is_idempotent():
    sid = "76561198000000001"
    plugin = PluginStub(
        {"111": [sid], "222": []},
        {sid: ["222"]},
    )
    service = MonitorAdminService(plugin)

    result = service.add_player("222", sid)

    assert result.changed is False
    assert result.message == "already push group"
    assert plugin.push_groups[sid] == ["222"]
    assert plugin.group_steam_ids_saves == 0
    assert plugin.push_groups_saves == 0


def test_existing_primary_monitor_takes_precedence_over_group_limit():
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid], "222": ["other"]})
    service = MonitorAdminService(plugin)

    result = service.add_player("222", sid)

    assert result.changed is True
    assert result.message == "added as push group"
    assert plugin.group_steam_ids["222"] == ["other"]
    assert plugin.push_groups[sid] == ["222"]


def test_remove_player_from_push_group_keeps_primary_monitor():
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid], "222": []}, {sid: ["222", "333"]})
    service = MonitorAdminService(plugin)

    result = service.remove_player("222", sid)

    assert result.changed is True
    assert result.message == "removed push route"
    assert plugin.group_steam_ids == {"111": [sid], "222": []}
    assert plugin.push_groups[sid] == ["333"]


def test_add_players_reports_primary_push_and_existing():
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid], "222": []})
    service = MonitorAdminService(plugin)

    result = service.add_players(
        "222",
        [sid, "76561198000000002"],
        bind_qq="10001",
        bind_nickname="猫",
        notify_session="qq:GroupMessage:0_222",
    )

    assert result.added == ["76561198000000002"]
    assert result.started is True
    assert "已为本群添加SteamID: 76561198000000002" in result.message
    assert "已自动设置为分发路由" in result.message
    assert "主监控群：111" in result.message
    assert "监控已自动启动" in result.message
    assert plugin.push_groups[sid] == ["222"]
    assert plugin.group_steam_ids["222"] == ["76561198000000002"]
    assert plugin._bind_data["10001"] == {"sid": "76561198000000002", "nickname": "猫"}
    assert plugin.running_groups == {"222"}
    assert plugin.notify_sessions["222"] == "qq:GroupMessage:0_222"


def test_add_players_does_not_restart_running_group():
    plugin = PluginStub({"111": []})
    plugin.running_groups = {"111"}
    service = MonitorAdminService(plugin)

    result = service.add_players("111", ["76561198000000001"])

    assert result.added == ["76561198000000001"]
    assert result.started is False
    assert "监控已自动启动" not in result.message


def test_bind_player_writes_qq_and_remark_records():
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]})
    service = MonitorAdminService(plugin)

    assert service.bind_player(sid, qq="10001", nickname="猫") is True
    assert plugin._bind_data["10001"] == {"sid": sid, "nickname": "猫"}
    assert plugin.bind_data_saves == 1

    assert service.bind_player(sid, nickname="备注名") is True
    assert plugin._bind_data["10001"]["nickname"] == "备注名"
    assert plugin.bind_data_saves == 2

    other = "76561198000000002"
    assert service.bind_player(other, nickname="路人") is True
    assert plugin._bind_data[f"__remark:{other}"] == {"sid": other, "nickname": "路人"}


def test_remove_player_from_primary_removes_all_routes_and_runtime_state():
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid], "222": []}, {sid: ["222", "333"]})
    plugin.monitor_state.group_last_states = {"111": {sid: {"gameid": "1"}}}
    plugin.monitor_state.next_poll_time = {"111": {sid: 20.0}}
    service = MonitorAdminService(plugin)

    result = service.remove_player("111", sid)

    assert result.changed is True
    assert result.message == "removed primary monitor and all push routes"
    assert plugin.group_steam_ids == {}
    assert plugin.push_groups == {}
    assert plugin.monitor_state.group_last_states == {}
    assert plugin.monitor_state.next_poll_time == {}
    assert plugin.persistent_saves == 1


def test_add_push_route_requires_primary_and_is_idempotent():
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]})
    service = MonitorAdminService(plugin)

    missing = service.add_push_route("222", "76561198000000002")
    assert missing.changed is False
    assert "未找到已轮询该SteamID的主群" in missing.message

    added = service.add_push_route("222", sid)
    assert added.changed is True
    assert plugin.push_groups[sid] == ["222"]

    again = service.add_push_route("222", sid)
    assert again.changed is False
    assert again.message == "本群已在该SteamID的推送组中。"


def test_remove_push_route_uses_explicit_target_message():
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]}, {sid: ["222"]})
    service = MonitorAdminService(plugin)

    result = service.remove_push_route("222", sid, explicit_target=True)
    assert result.changed is True
    assert result.message == f"已从 SteamID {sid} 的联动推送组中移除群 222。"
    assert plugin.push_groups == {}


def test_remove_group_clears_primary_routes_and_runtime_state():
    sid = "76561198000000001"
    other = "76561198000000002"
    plugin = PluginStub({"111": [sid], "222": [other]}, {sid: ["222"], other: ["111"]})
    plugin.running_groups = {"111", "222"}
    plugin.group_monitor_enabled = {"111": True, "222": True}
    plugin.notify_sessions = {"111": "s1", "222": "s2"}
    plugin.monitor_state.group_last_states = {"111": {sid: {"gameid": "1"}}}
    plugin.monitor_state.next_poll_time = {"111": {sid: 1.0}}
    service = MonitorAdminService(plugin)

    missing = service.remove_group("333")
    assert missing.changed is False
    assert "无需清理" in missing.message

    result = service.remove_group("111")
    assert result.changed is True
    assert plugin.group_steam_ids == {"222": [other]}
    assert plugin.push_groups == {}
    assert "111" not in plugin.running_groups
    assert "111" not in plugin.group_monitor_enabled
    assert "111" not in plugin.notify_sessions
    assert plugin.monitor_state.group_last_states == {}


def test_clear_all_ids_wipes_watchlists_and_runtime_state():
    sid = "76561198000000001"
    plugin = PluginStub({"111": [sid]}, {sid: ["222"]})
    plugin.running_groups = {"111"}
    plugin.group_monitor_enabled = {"111": True}
    plugin.notify_sessions = {"111": "s1"}
    plugin.monitor_state.group_last_states = {"111": {sid: {"gameid": "1"}}}
    plugin.monitor_state.playing_sessions = {("111", sid): object()}
    service = MonitorAdminService(plugin)

    result = service.clear_all_ids()

    assert result.changed is True
    assert plugin.group_steam_ids == {}
    assert plugin.push_groups == {}
    assert plugin.running_groups == set()
    assert plugin.notify_sessions == {}
    assert plugin.monitor_state.group_last_states == {}
    assert plugin.monitor_state.playing_sessions == {}
    assert plugin.config["group_steam_ids"] == {}
