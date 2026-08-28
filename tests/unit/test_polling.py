import unittest

from src.domain.monitoring.polling import calculate_poll_schedule


class PollingScheduleTests(unittest.TestCase):
    def test_game_uses_fastest_interval(self):
        self.assertEqual(
            calculate_poll_schedule(
                now=1000,
                gameid="730",
                personastate=1,
                lastlogoff=None,
                fixed_interval=0,
                intervals=[1, 3, 5, 10, 20, 30],
            ),
            (60, "1分钟轮询"),
        )

    def test_recently_offline_uses_online_interval(self):
        self.assertEqual(
            calculate_poll_schedule(
                now=2000,
                gameid=None,
                personastate=0,
                lastlogoff=1400,
                fixed_interval=0,
                intervals=[1, 3, 5, 10, 20, 30],
            ),
            (180, "3分钟轮询"),
        )

    def test_fixed_interval_wins(self):
        self.assertEqual(
            calculate_poll_schedule(
                now=0,
                gameid="730",
                personastate=1,
                lastlogoff=None,
                fixed_interval=90,
            ),
            (90, "固定1分钟轮询"),
        )


if __name__ == "__main__":
    unittest.main()
