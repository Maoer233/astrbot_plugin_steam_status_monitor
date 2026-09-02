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

    @property
    def group_steam_ids(self):
        return self.monitor_state.group_steam_ids

    def _save_group_steam_ids(self):
        self.group_steam_ids_saves += 1

    def _save_push_groups(self):
        self.push_groups_saves += 1

    def _save_persistent_data(self, force=False):
        self.persistent_saves += 1

    def _save_bind_data(self):
        self.bind_data_saves += 1

    @property
    def session_service(self):
        class _Stub:
            def discard_player(self, steam_id):
                return None

            def discard_group(self, group_id):
                return None

        return _Stub()


def test_add_player_creates_primary_monitor_when_unassigned():
    plugin = PluginStub({"group-a": []})
    service = MonitorAdminService(plugin)

    result = service.add_player("group-a", "76561198000000001")

    assert result.changed is True
    assert result.message == "added as primary monitor"
    assert plugin.group_steam_ids["group-a"] == ["76561198000000001"]
    assert plugin.push_groups == {}
    assert plugin.group_steam_ids_saves == 1
    assert plugin.push_groups_saves == 0


def test_add_player_uses_push_group_when_primary_exists_elsewhere():
    sid = "76561198000000001"
    plugin = PluginStub({"primary": [sid], "secondary": []})
    service = MonitorAdminService(plugin)

    result = service.add_player("secondary", sid)

    assert result.changed is True
    assert result.message == "added as push group"
    assert plugin.group_steam_ids["secondary"] == []
    assert plugin.push_groups[sid] == ["secondary"]
    assert plugin.group_steam_ids_saves == 0
    assert plugin.push_groups_saves == 1


def test_add_player_push_group_is_idempotent():
    sid = "76561198000000001"
    plugin = PluginStub(
        {"primary": [sid], "secondary": []},
        {sid: ["secondary"]},
    )
    service = MonitorAdminService(plugin)

    result = service.add_player("secondary", sid)

    assert result.changed is False
    assert result.message == "already push group"
    assert plugin.push_groups[sid] == ["secondary"]
    assert plugin.group_steam_ids_saves == 0
    assert plugin.push_groups_saves == 0


def test_existing_primary_monitor_takes_precedence_over_group_limit():
    sid = "76561198000000001"
    plugin = PluginStub({"primary": [sid], "secondary": ["other"]})
    service = MonitorAdminService(plugin)

    result = service.add_player("secondary", sid)

    assert result.changed is True
    assert result.message == "added as push group"
    assert plugin.group_steam_ids["secondary"] == ["other"]
    assert plugin.push_groups[sid] == ["secondary"]


def test_remove_player_from_push_group_keeps_primary_monitor():
    sid = "76561198000000001"
    plugin = PluginStub({"primary": [sid], "secondary": []}, {sid: ["secondary", "third"]})
    service = MonitorAdminService(plugin)

    result = service.remove_player("secondary", sid)

    assert result.changed is True
    assert result.message == "removed push route"
    assert plugin.group_steam_ids == {"primary": [sid], "secondary": []}
    assert plugin.push_groups[sid] == ["third"]


def test_remove_player_from_primary_removes_all_routes_and_runtime_state():
    sid = "76561198000000001"
    plugin = PluginStub({"primary": [sid], "secondary": []}, {sid: ["secondary", "third"]})
    plugin.monitor_state.group_last_states = {"primary": {sid: {"gameid": "1"}}}
    plugin.monitor_state.next_poll_time = {"primary": {sid: 20.0}}
    service = MonitorAdminService(plugin)

    result = service.remove_player("primary", sid)

    assert result.changed is True
    assert result.message == "removed primary monitor and all push routes"
    assert plugin.group_steam_ids == {"secondary": []}
    assert plugin.push_groups == {}
    assert plugin.monitor_state.group_last_states == {}
    assert plugin.monitor_state.next_poll_time == {}
    assert plugin.persistent_saves == 1
