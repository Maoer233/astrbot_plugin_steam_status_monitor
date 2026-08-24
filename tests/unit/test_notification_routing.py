import ast
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_PATH = PROJECT_ROOT / "src/plugin/steam_status_monitor.py"


def load_get_notify_sessions():
    tree = ast.parse(IMPLEMENTATION_PATH.read_text(encoding="utf-8"))
    plugin_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SteamStatusMonitorV3"
    )
    method = next(
        node
        for node in plugin_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_get_notify_sessions"
    )
    module = ast.Module(body=[method], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), str(IMPLEMENTATION_PATH), "exec"), namespace)
    return namespace["_get_notify_sessions"]


class NotificationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.get_notify_sessions = load_get_notify_sessions()

    def test_cross_linked_primary_groups_are_not_sent_twice(self):
        sid = "test-steam-id"
        plugin = type("Plugin", (), {})()
        plugin.notify_sessions = {"group-a": "session-a", "group-b": "session-b"}
        plugin.group_steam_ids = {"group-a": [sid], "group-b": [sid]}
        plugin.push_groups = {sid: ["group-a", "group-b"]}

        self.assertEqual(
            ["session-a"], self.get_notify_sessions(plugin, "group-a", sid)
        )
        self.assertEqual(
            ["session-b"], self.get_notify_sessions(plugin, "group-b", sid)
        )

    def test_linked_group_without_direct_monitoring_still_receives_notification(self):
        sid = "test-steam-id"
        plugin = type("Plugin", (), {})()
        plugin.notify_sessions = {"group-a": "session-a", "group-b": "session-b"}
        plugin.group_steam_ids = {"group-a": [sid], "group-b": []}
        plugin.push_groups = {sid: ["group-b"]}

        self.assertEqual(
            ["session-a", "session-b"],
            self.get_notify_sessions(plugin, "group-a", sid),
        )


if __name__ == "__main__":
    unittest.main()
