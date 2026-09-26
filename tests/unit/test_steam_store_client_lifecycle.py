import unittest
from unittest.mock import AsyncMock, patch

from src.infrastructure.clients.steam import (
    SteamClientMixin,
    close_steam_store_client,
    initialize_steam_store_client,
)


class SteamStoreClientOwner(SteamClientMixin):
    proxy = None
    STEAM_STORE_BASE = "https://store.steampowered.com"


class SteamStoreClientLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_reuses_and_close_releases_store_client(self):
        owner = SteamStoreClientOwner()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient") as factory:
            first = AsyncMock()
            factory.return_value = first
            await initialize_steam_store_client(owner)
            await initialize_steam_store_client(owner)
            self.assertIs(owner._steam_store_http_client, first)
            factory.assert_called_once()
            await close_steam_store_client(owner)
            first.aclose.assert_awaited_once()

    async def test_request_falls_back_without_initialized_pool(self):
        owner = SteamStoreClientOwner()
        fake = AsyncMock()
        response = AsyncMock()
        response.json.return_value = {"730": {"success": False}}
        response.raise_for_status.return_value = None
        fake.get.return_value = response
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", return_value=fake):
            self.assertIsNone(await owner.fetch_game_details("730"))
        fake.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
