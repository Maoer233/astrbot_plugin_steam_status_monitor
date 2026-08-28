import unittest

from src.application.services.openbox import query_openbox


class _SteamClient:
    async def fetch_player_summary(self, steamid):
        return {
            "steamid": steamid,
            "personaname": "Tester",
            "personastate": 1,
            "avatarfull": "https://example.com/avatar.jpg",
        }


class OpenBoxServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_returns_framework_neutral_result(self):
        result = await query_openbox(_SteamClient(), "76561198000000000")

        self.assertIn("昵称: Tester", result.text)
        self.assertIn("在线状态: 在线", result.text)
        self.assertEqual(result.avatar_url, "https://example.com/avatar.jpg")


if __name__ == "__main__":
    unittest.main()
