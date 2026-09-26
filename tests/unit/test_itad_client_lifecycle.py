import unittest
from unittest.mock import AsyncMock, patch

from src.infrastructure.clients.itad import ITADClient


class ITADClientLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_reuses_client_in_same_loop(self):
        client = ITADClient(api_key="test")
        with patch("src.infrastructure.clients.itad.httpx.AsyncClient") as factory:
            first = AsyncMock()
            second = AsyncMock()
            factory.side_effect = [first, second]
            await client.initialize_http_client()
            await client.initialize_http_client()
            self.assertIs(client._http_client, first)
            factory.assert_called_once()
            await client.close_http_client()
            first.aclose.assert_awaited_once()

    async def test_request_falls_back_when_client_not_initialized(self):
        client = ITADClient(api_key="test")
        fake = AsyncMock()
        response = unittest.mock.Mock()
        response.json.return_value = {"ok": True}
        response.raise_for_status.return_value = None
        fake.get.return_value = response
        with patch("src.infrastructure.clients.itad.httpx.AsyncClient", return_value=fake):
            self.assertEqual({"ok": True}, await client._get("/test", {}))
        fake.aclose.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
