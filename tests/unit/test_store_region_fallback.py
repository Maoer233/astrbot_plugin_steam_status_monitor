import unittest
from unittest.mock import patch

from src.infrastructure.clients.steam import STEAM_STORE_COOKIES, SteamClientMixin
from src.shared.utils.price import (
    is_store_region_locked,
    store_region_candidates,
    summary_to_cny,
)


class FakeSteam(SteamClientMixin):
    def __init__(self):
        self.proxy = None
        self.STEAM_STORE_BASE = "https://store.steampowered.com"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _RegionClient:
    payloads = {}
    language_payloads = {}
    calls = []
    errors = {}
    cookies = None

    def __init__(self, *args, **kwargs):
        _RegionClient.cookies = kwargs.get("cookies")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        params = params or {}
        if not params.get("appids"):
            raise RuntimeError("400 Bad Request: missing appids")
        cc = str(params.get("cc") or "").upper()
        lang = str(params.get("l") or "")
        _RegionClient.calls.append((params.get("appids"), cc, lang))
        if cc in _RegionClient.errors:
            raise _RegionClient.errors[cc]
        keyed = _RegionClient.language_payloads.get((cc, lang))
        payload = keyed or _RegionClient.payloads.get(cc) or {"1034140": {"success": False}}
        return _FakeResponse(payload)


def _payload(success, **data):
    return {"1034140": {"success": success, "data": data if success else {}}}


class StoreRegionCandidateTests(unittest.TestCase):
    def test_cn_then_hk_tw_jp_us(self):
        self.assertEqual(
            ["CN", "HK", "TW", "JP", "US"],
            store_region_candidates("CN"),
        )

    def test_preferred_hk_is_not_duplicated(self):
        self.assertEqual(
            ["HK", "TW", "JP", "US"],
            store_region_candidates("HK"),
        )


class StoreRegionFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _RegionClient.calls = []
        _RegionClient.errors = {}
        _RegionClient.cookies = None
        _RegionClient.language_payloads = {}
        _RegionClient.payloads = {
            "CN": _payload(False),
            "HK": _payload(
                True,
                name="Subverse",
                price_overview={
                    "currency": "HKD",
                    "initial": 24900,
                    "final": 24900,
                    "discount_percent": 0,
                    "final_formatted": "HK$ 249.00",
                },
            ),
        }

    async def test_details_fall_back_from_cn_to_hk(self):
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _RegionClient):
            detail = await client.fetch_game_details("1034140", country="CN")

        self.assertEqual("Subverse", detail["name"])
        self.assertEqual("HK", detail["_store_region"])
        self.assertEqual(
            [("1034140", "CN", "schinese"), ("1034140", "HK", "schinese")],
            _RegionClient.calls,
        )
        self.assertEqual("1", _RegionClient.cookies["wants_mature_content"])

    async def test_region_price_uses_actual_unlocked_region(self):
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _RegionClient):
            price = await client.fetch_region_price("1034140", "CN")

        self.assertEqual("HK", price["region"])
        self.assertEqual("HKD", price["currency"])
        self.assertEqual(249.0, price["current_price"])
        converted = summary_to_cny(price)
        self.assertEqual("CNY", converted["currency"])
        self.assertNotIn("CN", {price["region"]})

    async def test_preferred_region_success_does_not_fallback(self):
        _RegionClient.payloads["US"] = _payload(
            True,
            name="Subverse",
            price_overview={
                "currency": "USD",
                "initial": 3999,
                "final": 3999,
                "discount_percent": 0,
            },
        )
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _RegionClient):
            detail = await client.fetch_game_details("1034140", country="US")
            price = await client.fetch_region_price("1034140", "US")

        self.assertEqual("US", detail["_store_region"])
        self.assertEqual("US", price["region"])
        self.assertEqual(("1034140", "US", "schinese"), _RegionClient.calls[0])
        self.assertNotIn("HK", [cc for _appid, cc, _lang in _RegionClient.calls])

    async def test_cn_timeout_still_tries_hk(self):
        _RegionClient.errors["CN"] = TimeoutError("cn timeout")
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _RegionClient):
            detail = await client.fetch_game_details("1034140", country="CN")

        self.assertEqual("HK", detail["_store_region"])
        self.assertEqual(
            [("1034140", "CN", "schinese"), ("1034140", "HK", "schinese")],
            _RegionClient.calls,
        )

    async def test_schinese_failure_falls_back_to_english(self):
        _RegionClient.payloads = {}
        _RegionClient.language_payloads = {
            ("CN", "schinese"): _payload(False),
            ("HK", "schinese"): _payload(False),
            ("TW", "schinese"): _payload(False),
            ("JP", "schinese"): _payload(False),
            ("US", "schinese"): _payload(False),
            ("CN", "english"): _payload(
                True,
                name="Subverse",
                short_description="Adult sci-fi shooter.",
                genres=[{"description": "Action"}],
            ),
        }
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _RegionClient):
            detail = await client.fetch_game_details("1034140", country="CN")

        self.assertEqual("Subverse", detail["name"])
        self.assertEqual("CN", detail["_store_region"])
        self.assertEqual("english", detail["_store_language"])
        self.assertIn(("1034140", "US", "schinese"), _RegionClient.calls)
        self.assertIn(("1034140", "CN", "english"), _RegionClient.calls)
        self.assertEqual(STEAM_STORE_COOKIES, _RegionClient.cookies)


class StoreRegionLockedHintTests(unittest.TestCase):
    def test_locked_when_preferred_missing_and_fallback_used(self):
        self.assertTrue(is_store_region_locked("CN", "HK", {"HK": {"current_price": 249}}))

    def test_not_locked_when_preferred_has_price(self):
        self.assertFalse(is_store_region_locked("CN", "CN", {"CN": {"current_price": 128}}))

    def test_not_locked_without_any_store_hit(self):
        self.assertFalse(is_store_region_locked("CN", None, {}))
