from src.application.services.monitor_admin import MonitorAdminService


class PluginStub:
    def __init__(self, groups, push_groups=None):
        self.group_steam_ids = groups
        self.push_groups = push_groups or {}
        self.group_steam_ids_saves = 0
        self.push_groups_saves = 0

    def _save_group_steam_ids(self):
        self.group_steam_ids_saves += 1

    def _save_push_groups(self):
        self.push_groups_saves += 1


def test_add_player_creates_primary_monitor_when_unassigned():
    plugin = PluginStub({"group-a": []})
    service = MonitorAdminService(plugin, max_group_size=5)

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
    service = MonitorAdminService(plugin, max_group_size=5)

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
    service = MonitorAdminService(plugin, max_group_size=5)

    result = service.add_player("secondary", sid)

    assert result.changed is False
    assert result.message == "already push group"
    assert plugin.push_groups[sid] == ["secondary"]
    assert plugin.group_steam_ids_saves == 0
    assert plugin.push_groups_saves == 0


def test_existing_primary_monitor_takes_precedence_over_group_limit():
    sid = "76561198000000001"
    plugin = PluginStub({"primary": [sid], "secondary": ["other"]})
    service = MonitorAdminService(plugin, max_group_size=1)

    result = service.add_player("secondary", sid)

    assert result.changed is True
    assert result.message == "added as push group"
    assert plugin.group_steam_ids["secondary"] == ["other"]
    assert plugin.push_groups[sid] == ["secondary"]
