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
