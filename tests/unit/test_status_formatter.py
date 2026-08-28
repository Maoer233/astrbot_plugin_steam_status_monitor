import unittest

from src.presentation.formatters.status import format_play_duration, format_player_status


class StatusFormatterTests(unittest.TestCase):
    def test_game_status(self):
        self.assertEqual(
            format_player_status(
                name="Alice",
                game_name="Portal 2",
                gameid="620",
                personastate=1,
                lastlogoff=None,
                now=0,
                poll_level="1分钟轮询",
            ),
            "🟢【Alice】正在玩 Portal 2（1分钟轮询）",
        )

    def test_offline_status(self):
        self.assertEqual(
            format_player_status(
                name="Alice",
                game_name=None,
                gameid=None,
                personastate=0,
                lastlogoff=0,
                now=0,
                poll_level="30分钟轮询",
            ),
            "⚪️【Alice】离线（30分钟轮询）",
        )

    def test_duration(self):
        self.assertEqual(format_play_duration(12.5), "12.5分钟")
        self.assertEqual(format_play_duration(90), "1.5小时")


if __name__ == "__main__":
    unittest.main()
