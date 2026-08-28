import unittest

from src.infrastructure.persistence.plugin_data import PersistenceMixin


class _Persistence(PersistenceMixin):
    def __init__(self):
        self.session_records = {}
        self._session_dirty = False

    def _get_day_key(self, offset_days=0):
        return "2026-08-28"


class SessionRecordTests(unittest.TestCase):
    def test_same_session_is_not_duplicated_across_groups(self):
        persistence = _Persistence()

        persistence._record_session("sid", "730", "Game", 1000, 1123, 2.05, "group-a")
        persistence._record_session("sid", "730", "Game", 1000, 1123, 2.05, "group-b")

        sessions = persistence.session_records["sid"]
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["duration_min"], 2.05)
        self.assertEqual(sessions[0]["group_id"], "group-a")


if __name__ == "__main__":
    unittest.main()
