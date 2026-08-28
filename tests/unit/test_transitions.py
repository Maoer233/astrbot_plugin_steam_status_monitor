import unittest

from src.domain.monitoring.transitions import classify_game_transition


class GameTransitionTests(unittest.TestCase):
    def test_initial_offline_state(self):
        transition = classify_game_transition(None, {"gameid": None})
        self.assertEqual("initial", transition.kind)

    def test_game_start(self):
        transition = classify_game_transition(
            {"gameid": None}, {"gameid": "730"}
        )
        self.assertEqual("start", transition.kind)
        self.assertTrue(transition.has_start)

    def test_game_exit(self):
        transition = classify_game_transition(
            {"gameid": "730"}, {"gameid": None}
        )
        self.assertEqual("exit", transition.kind)
        self.assertTrue(transition.has_exit)
        self.assertFalse(transition.has_start)

    def test_game_switch_contains_exit_and_start(self):
        transition = classify_game_transition(
            {"gameid": "730"}, {"gameid": "570"}
        )
        self.assertEqual("switch", transition.kind)
        self.assertTrue(transition.has_exit)
        self.assertTrue(transition.has_start)

    def test_recent_quit_is_network_fluctuation(self):
        transition = classify_game_transition(
            None,
            {"gameid": "730"},
            pending_quit={"730": {"quit_time": 900, "notified": False}},
            now=1000,
        )
        self.assertEqual("start", transition.kind)
        self.assertTrue(transition.is_network_fluctuation)

    def test_notified_or_expired_quit_is_start(self):
        for quit_info, now in (
            ({"quit_time": 900, "notified": True}, 1000),
            ({"quit_time": 700, "notified": False}, 1000),
        ):
            with self.subTest(quit_info=quit_info):
                transition = classify_game_transition(
                    None,
                    {"gameid": "730"},
                    pending_quit={"730": quit_info},
                    now=now,
                )
                self.assertEqual("start", transition.kind)


if __name__ == "__main__":
    unittest.main()
