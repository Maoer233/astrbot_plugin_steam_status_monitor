import unittest

from src.domain.monitoring import MonitorStateStore, StateBackedMonitorMixin


class _Monitor(StateBackedMonitorMixin):
    def __init__(self):
        self.monitor_state = MonitorStateStore()


class MonitorStateStoreTests(unittest.TestCase):
    def test_legacy_fields_share_store_identity(self):
        monitor = _Monitor()

        monitor.group_steam_ids["100"] = ["76561198000000000"]

        self.assertIs(monitor.group_steam_ids, monitor.monitor_state.group_steam_ids)
        self.assertEqual(
            monitor.monitor_state.group_steam_ids["100"],
            ["76561198000000000"],
        )

    def test_legacy_assignment_updates_store(self):
        monitor = _Monitor()

        replacement = {"100": {"76561198000000000": {"gameid": "730"}}}
        monitor.group_last_states = replacement

        self.assertIs(monitor.monitor_state.group_last_states, replacement)

    def test_private_runtime_fields_are_backed_by_store(self):
        monitor = _Monitor()

        monitor._startup_stale_groups["100"] = True
        monitor._pending_end_notifications["100"] = [{"gameid": "730"}]

        self.assertTrue(monitor.monitor_state.startup_stale_groups["100"])
        self.assertEqual(
            monitor.monitor_state.pending_end_notifications["100"],
            [{"gameid": "730"}],
        )


if __name__ == "__main__":
    unittest.main()
