import unittest
from unittest.mock import AsyncMock

import httpx

from src.infrastructure.clients.itad import ITADClient, ProviderError


class ITADProviderErrorTests(unittest.TestCase):
    def test_classifies_timeout_as_retryable(self):
        error = ITADClient._provider_error(httpx.ReadTimeout("timeout"), "/games/prices/v3")
        self.assertIsInstance(error, ProviderError)
        self.assertEqual("TIMEOUT", error.code)
        self.assertTrue(error.retryable)

    def test_classifies_auth_and_rate_limit(self):
        request = httpx.Request("GET", "https://example.test")
        auth_response = httpx.Response(401, request=request)
        rate_response = httpx.Response(429, request=request)
        auth_error = ITADClient._provider_error(httpx.HTTPStatusError("auth", request=request, response=auth_response), "/games/search/v1")
        rate_error = ITADClient._provider_error(httpx.HTTPStatusError("rate", request=request, response=rate_response), "/games/search/v1")
        self.assertEqual("AUTH_ERROR", auth_error.code)
        self.assertEqual("RATE_LIMITED", rate_error.code)
        self.assertTrue(rate_error.retryable)


class ITADProviderFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_client_returns_empty_compatible_result(self):
        client = ITADClient()
        self.assertEqual({}, await client.get_prices("game", "CN"))


if __name__ == "__main__":
    unittest.main()
