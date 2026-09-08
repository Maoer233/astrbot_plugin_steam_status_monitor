import unittest
from unittest.mock import AsyncMock, patch

import httpx

from src.infrastructure.clients.steam import SteamClientMixin


class FakeSteam(SteamClientMixin):
    def __init__(self):
        self.API_KEY = "k"
        self.RETRY_TIMES = 2
        self.proxy = None
        self.STEAM_API_BASE = "https://api.steampowered.com"
        self.single_calls = []

    async def fetch_player_status(self, steam_id, retry=None):
        self.single_calls.append((steam_id, retry))
        return {"name": steam_id}


class _FailingClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        raise httpx.ConnectError("boom")


class SteamBatchFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_connect_error_skips_serial_single_lookup(self):
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _FailingClient):
            with patch("src.infrastructure.clients.steam.asyncio.sleep", new=AsyncMock()):
                result = await client.fetch_player_statuses_batch(["s1", "s2"], retry=2)

        self.assertEqual(result, {})
        self.assertEqual(client.single_calls, [])
