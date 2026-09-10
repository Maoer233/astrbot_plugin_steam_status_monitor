import unittest
from unittest.mock import patch

from src.infrastructure.clients.steam import SteamClientMixin
from src.shared.utils.price import store_region_candidates, summary_to_cny


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
    calls = []
    errors = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        cc = str((params or {}).get("cc") or "").upper()
        _RegionClient.calls.append(cc)
        if cc in _RegionClient.errors:
            raise _RegionClient.errors[cc]
        payload = _RegionClient.payloads.get(cc) or {"1034140": {"success": False}}
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
        self.assertEqual(["CN", "HK"], _RegionClient.calls)

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
        self.assertEqual(["US"], _RegionClient.calls[:1])
        self.assertNotIn("HK", _RegionClient.calls)

    async def test_cn_timeout_still_tries_hk(self):
        _RegionClient.errors["CN"] = TimeoutError("cn timeout")
        client = FakeSteam()
        with patch("src.infrastructure.clients.steam.httpx.AsyncClient", _RegionClient):
            detail = await client.fetch_game_details("1034140", country="CN")

        self.assertEqual("HK", detail["_store_region"])
        self.assertEqual(["CN", "HK"], _RegionClient.calls)
