import asyncio

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
