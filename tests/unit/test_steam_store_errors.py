import json
import unittest
from unittest.mock import patch

import httpx

from src.infrastructure.clients.errors import ProviderError
from src.infrastructure.clients.steam import SteamClientMixin, classify_steam_store_error


class FakeSteam(SteamClientMixin):
    def __init__(self):
        self.proxy = None
        self.STEAM_STORE_BASE = "https://store.steampowered.com"
        self._steam_store_http_client = None
        self._steam_store_http_client_loop = None


class _FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.request = httpx.Request("GET", "https://store.steampowered.com/api/appdetails")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _ScriptedClient:
    responses = []
    calls = []
    scripted_regions = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aclose(self):
        return None

    async def get(self, url, params=None):
        params = params or {}
        region = str(params.get("cc") or "").upper()
        _ScriptedClient.calls.append((url, params.get("cc"), params.get("l"), params.get("language")))
        if _ScriptedClient.scripted_regions and region not in _ScriptedClient.scripted_regions:
            raise httpx.ConnectError("unscripted region")
        if not _ScriptedClient.responses:
            raise AssertionError("unexpected Steam request")
        item = _ScriptedClient.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _payload(success, **data):
    body = {"data": data} if success else {}
    return _FakeResponse({"1034140": {"success": success, **body}})


class SteamStoreErrorClassificationTests(unittest.TestCase):
    def test_classifies_timeout_rate_limit_and_invalid_json(self):
        timeout = classify_steam_store_error(httpx.ReadTimeout("timeout"))
        request = httpx.Request("GET", "https://store.steampowered.com")
        rate = classify_steam_store_error(
            httpx.HTTPStatusError(
                "rate",
                request=request,
                response=httpx.Response(429, request=request),
            )
        )
        invalid = classify_steam_store_error(json.JSONDecodeError("bad", "x", 0))

        self.assertEqual("TIMEOUT", timeout.code)
        self.assertTrue(timeout.retryable)
        self.assertEqual("RATE_LIMITED", rate.code)
        self.assertEqual("INVALID_RESPONSE", invalid.code)
        self.assertIsInstance(timeout, ProviderError)


class SteamStoreStructuredErrorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _ScriptedClient.responses = []
        _ScriptedClient.calls = []
        _ScriptedClient.scripted_regions = None

    async def test_region_lock_continues_fallback_and_records_reason(self):
        _ScriptedClient.responses = [
            _payload(False),
            _payload(True, name="Subverse", price_overview={"currency": "HKD", "final": 24900, "initial": 24900}),
        ]
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _ScriptedClient):
            detail = await client.fetch_game_details("1034140", country="CN")

        self.assertEqual("HK", detail["_store_region"])
        self.assertEqual("REGION_LOCKED", detail["_store_status"])
        self.assertTrue(detail["_store_fallback"])
        self.assertEqual("CN", detail["_requested_region"])

    async def test_missing_product_does_not_look_like_network_failure(self):
        _ScriptedClient.responses = [_FakeResponse({}) for _ in range(10)]
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _ScriptedClient):
            detail = await client.fetch_game_details("1034140", country="CN")

        self.assertIsNone(detail)
        self.assertEqual("NOT_FOUND", client.last_store_error.code)

    async def test_network_failure_is_classified_and_still_tries_next_region(self):
        _ScriptedClient.responses = [
            httpx.ConnectError("down"),
            _payload(True, name="Subverse"),
        ]
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _ScriptedClient):
            detail = await client.fetch_game_details("1034140", country="CN")

        self.assertEqual("Subverse", detail["name"])
        self.assertEqual("HK", detail["_store_region"])
        self.assertEqual("UPSTREAM_ERROR", detail["_store_errors"][0]["code"])

    async def test_invalid_json_is_not_swallowed_as_empty_product(self):
        _ScriptedClient.scripted_regions = {"US"}
        _ScriptedClient.responses = [ _FakeResponse(json.JSONDecodeError("bad", "{", 0)) ]
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _ScriptedClient):
            price = await client.fetch_region_price("1034140", "US")

        self.assertIsNone(price)
        self.assertEqual("INVALID_RESPONSE", client.last_store_error.code)
        self.assertEqual(1, len(_ScriptedClient.calls))

    async def test_no_price_is_distinct_from_region_lock(self):
        _ScriptedClient.scripted_regions = {"US"}
        _ScriptedClient.responses = [
            _payload(True, name="Free Sample"),
        ]
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _ScriptedClient):
            price = await client.fetch_region_price("1034140", "US")

        self.assertIsNone(price)
        self.assertEqual("NO_PRICE", client.last_store_error.code)

    async def test_review_failure_does_not_block_the_other_language(self):
        _ScriptedClient.responses = [
            httpx.ReadTimeout("reviews"),
            _FakeResponse({
                "query_summary": {"total_reviews": 10, "total_positive": 9},
            }),
        ]
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _ScriptedClient):
            reviews = await client.fetch_game_reviews_both("1034140")

        self.assertIsNone(reviews["all"])
        self.assertEqual("特别好评", reviews["schinese"]["text"])
        self.assertEqual("TIMEOUT", client.last_store_error.code)
