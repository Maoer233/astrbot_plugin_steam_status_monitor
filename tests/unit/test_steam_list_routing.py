import asyncio

import pytest

from src.application.services.steam_list import handle_steam_list


class _SessionService:
    def started_at(self, group_id, sid, gameid):
        if group_id == "primary" and sid == "sid-primary" and str(gameid) == "730":
            return 100
        return None


class ListMonitor:
    group_steam_ids = {"primary": ["sid-primary"], "push": []}
    push_groups = {"sid-primary": ["push"]}
    session_service = _SessionService()

    async def fetch_player_statuses_batch(self, steam_ids):
        return {
            sid: {
                "name": "Player",
                "gameid": "730",
                "gameextrainfo": "Counter-Strike 2",
                "avatarfull": "",
                "personastate": 1,
            }
            for sid in steam_ids
        }

    async def get_chinese_game_name(self, gameid, game):
        return game

    def _resolve_bind_name(self, sid, name):
        return name


def test_push_group_uses_primary_group_play_time_cache(monkeypatch):
    async def fake_render(*args, **kwargs):
        return "rendered"

    monkeypatch.setattr(
        "src.application.services.steam_list.render_steam_list_image",
        fake_render,
    )

    async def run():
        event = type("Event", (), {"get_group_id": lambda self: "push"})()
        result = [item async for item in handle_steam_list(ListMonitor(), event)]
        assert result == ["rendered"]

    asyncio.run(run())


class _StatusMonitor:
    def __init__(self, personastate, gameid=None):
        self.config = {}
        self.data_dir = "tmp"
        self.group_steam_ids = {"primary": ["sid1"]}
        self.push_groups = {}
        self.session_service = _SessionService()
        self._personastate = personastate
        self._gameid = gameid

    async def fetch_player_statuses_batch(self, steam_ids):
        return {
            sid: {
                "name": "Player",
                "gameid": self._gameid,
                "gameextrainfo": "SomeGame",
                "avatarfull": "",
                "personastate": self._personastate,
            }
            for sid in steam_ids
        }

    async def get_chinese_game_name(self, gameid, game):
        return game

    def _resolve_bind_name(self, sid, name):
        return name


@pytest.mark.parametrize("personastate,expected", [
    (2, "busy"),
    (3, "away"),
    (4, "snooze"),
])
def test_list_maps_persona_state(monkeypatch, personastate, expected):
    captured = {}

    async def fake_render(data_dir, user_list, **kwargs):
        captured["user_list"] = user_list
        return b"PNG"

    async def fake_frame(*args, **kwargs):
        return None

    monkeypatch.setattr("src.application.services.steam_list.render_steam_list_image", fake_render)
    monkeypatch.setattr("src.application.services.steam_list.get_avatar_frame_path", fake_frame)
    monkeypatch.setattr("src.application.services.steam_list.get_avatar_frame_url", fake_frame)

    class Event:
        def get_group_id(self):
            return "primary"

        def image_result(self, path):
            return ("image", path)

        def plain_result(self, text):
            return ("plain", text)

    async def run():
        list([x async for x in handle_steam_list(_StatusMonitor(personastate), Event())])

    asyncio.run(run())
    assert [u["status"] for u in captured["user_list"]] == [expected]
