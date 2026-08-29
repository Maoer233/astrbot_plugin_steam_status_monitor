import ast
import unittest
import time
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[2]
IMPLEMENTATION_PATH = (
    PROJECT_ROOT / "src/application/services/notification_tracking.py"
)


def load_methods():
    tree = ast.parse(IMPLEMENTATION_PATH.read_text(encoding="utf-8"))
    mixin = next(
        node for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name == "NotificationTrackingMixin"
    )
    names = {"_notification_event_key", "_should_send_notification"}
    methods = [
        node for node in mixin.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    module = ast.Module(body=methods, type_ignores=[])
    namespace = {"time": time}
    exec(
        compile(ast.fix_missing_locations(module), str(IMPLEMENTATION_PATH), "exec"),
        namespace,
    )
    return namespace


class NotificationDeduplicationTests(unittest.TestCase):
    def setUp(self):
        methods = load_methods()
        plugin_type = type(
            "Plugin",
            (),
            {
                "_notification_event_key": methods["_notification_event_key"],
                "_should_send_notification": methods["_should_send_notification"],
            },
        )
        self.plugin = plugin_type()
        self.notification = {
            "type": "start",
            "sid": "steam-id",
            "gameid": "730",
            "start_time": 1000,
        }

    @patch("time.time", return_value=2000)
    def test_same_event_to_same_session_is_sent_once(self, _):
        self.assertTrue(
            self.plugin._should_send_notification(
                self.notification, "session-target"
            )
        )
        self.assertFalse(
            self.plugin._should_send_notification(
                self.notification, "session-target"
            )
        )

    @patch("time.time", return_value=2000)
    def test_same_event_to_different_sessions_is_not_suppressed(self, _):
        self.assertTrue(
            self.plugin._should_send_notification(
                self.notification, "session-a"
            )
        )
        self.assertTrue(
            self.plugin._should_send_notification(
                self.notification, "session-b"
            )
        )

    @patch("time.time", return_value=2000)
    def test_different_start_times_are_distinct_events(self, _):
        self.assertTrue(
            self.plugin._should_send_notification(
                self.notification, "session-target"
            )
        )
        next_event = dict(self.notification, start_time=1001)
        self.assertTrue(
            self.plugin._should_send_notification(
                next_event, "session-target"
            )
        )


if __name__ == "__main__":
    unittest.main()

