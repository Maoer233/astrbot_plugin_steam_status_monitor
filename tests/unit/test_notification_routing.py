import ast
import unittest
from pathlib import Path

from src.shared.utils.notify_session import is_sendable_group_session


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_PATH = (
    PROJECT_ROOT / "src/application/services/notification_tracking.py"
)


def load_get_notify_sessions():
    tree = ast.parse(IMPLEMENTATION_PATH.read_text(encoding="utf-8"))
    plugin_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "NotificationTrackingMixin"
    )
    method = next(
        node
        for node in plugin_class.body
        if isinstance(node, ast.FunctionDef) and node.name == "_get_notify_sessions"
    )
    module = ast.Module(body=[method], type_ignores=[])
    namespace = {"is_sendable_group_session": is_sendable_group_session}
    exec(compile(ast.fix_missing_locations(module), str(IMPLEMENTATION_PATH), "exec"), namespace)
    return namespace["_get_notify_sessions"]


class NotificationRoutingTests(unittest.TestCase):
    def setUp(self):
        self.get_notify_sessions = load_get_notify_sessions()

    def test_cross_linked_primary_groups_receive_notification_once_each(self):
        sid = "test-steam-id"
        plugin = type("Plugin", (), {})()
        plugin.notify_sessions = {
            "111": "3640631607:GroupMessage:1_111",
            "222": "3640631607:GroupMessage:1_222",
        }
        plugin.group_steam_ids = {"111": [sid], "222": [sid]}
        plugin.push_groups = {sid: ["111", "222"]}

        self.assertEqual(
            [
                "3640631607:GroupMessage:1_111",
                "3640631607:GroupMessage:1_222",
            ],
            self.get_notify_sessions(plugin, "111", sid),
        )
        self.assertEqual(
            [
                "3640631607:GroupMessage:1_222",
                "3640631607:GroupMessage:1_111",
            ],
            self.get_notify_sessions(plugin, "222", sid),
        )

    def test_linked_group_without_direct_monitoring_still_receives_notification(self):
        sid = "test-steam-id"
        plugin = type("Plugin", (), {})()
        plugin.notify_sessions = {
            "111": "3640631607:GroupMessage:1_111",
            "222": "3640631607:GroupMessage:1_222",
        }
        plugin.group_steam_ids = {"111": [sid], "222": []}
        plugin.push_groups = {sid: ["222"]}

        self.assertEqual(
            [
                "3640631607:GroupMessage:1_111",
                "3640631607:GroupMessage:1_222",
            ],
            self.get_notify_sessions(plugin, "111", sid),
        )

    def test_disabled_group_is_not_notified(self):
        sid = "test-steam-id"
        plugin = type("Plugin", (), {})()
        plugin.notify_sessions = {
            "111": "3640631607:GroupMessage:1_111",
            "222": "3640631607:GroupMessage:1_222",
        }
        plugin.group_steam_ids = {"111": [sid], "222": []}
        plugin.push_groups = {sid: ["222"]}
        plugin.group_monitor_enabled = {"111": False, "222": True}

        self.assertEqual(
            ["3640631607:GroupMessage:1_222"],
            self.get_notify_sessions(plugin, "111", sid),
        )

        plugin.group_monitor_enabled = {"111": False, "222": False}
        self.assertEqual([], self.get_notify_sessions(plugin, "111", sid))

    def test_empty_group_session_is_skipped(self):
        sid = "test-steam-id"
        plugin = type("Plugin", (), {})()
        plugin.notify_sessions = {"": "3640631607:GroupMessage:0_"}
        plugin.group_steam_ids = {"": [sid]}
        plugin.push_groups = {}

        self.assertEqual([], self.get_notify_sessions(plugin, "", sid))


if __name__ == "__main__":
    unittest.main()
