import unittest
from unittest.mock import AsyncMock

from src.infrastructure.clients.itad import ITADClient


class ITADPriceSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_steam_low_uses_store_low_not_recent_history(self):
        client = ITADClient(api_key="test")
        client.get_prices = AsyncMock(return_value={
            "id": "elden",
            "historyLow": {
                "all": {"amount": 21.6, "currency": "CNY"},
            },
            "deals": [
                {
                    "shop": {"id": 61, "name": "Steam"},
                    "price": {"amount": 298, "currency": "CNY"},
                    "regular": {"amount": 298, "currency": "CNY"},
                    "cut": 0,
                    "storeLow": {"amount": 21.6, "currency": "CNY"},
                },
                {
                    "shop": {"id": 16, "name": "Humble Store"},
                    "price": {"amount": 402.43, "currency": "CNY"},
                    "regular": {"amount": 402.43, "currency": "CNY"},
                    "cut": 0,
                },
            ],
        })
        summary = await client.get_price_summary("elden", "CN")

        self.assertEqual(298, summary["current_price"])
        self.assertEqual(21.6, summary["steam_low"])
        self.assertEqual(93, summary["steam_low_cut"])
        self.assertEqual(21.6, summary["history_low"])
        self.assertEqual("Humble Store", summary["cdk_shop"])
        self.assertEqual(402.43, summary["cdk_amount"])

    async def test_steam_shop_matched_by_id_when_name_missing(self):
        client = ITADClient(api_key="test")
        client.get_prices = AsyncMock(return_value={
            "id": "elden",
            "deals": [
                {
                    "shop": {"id": 61},
                    "price": {"amount": 298, "currency": "CNY"},
                    "regular": {"amount": 298, "currency": "CNY"},
                    "cut": 0,
                    "storeLow": {"amount": 89.4, "currency": "CNY"},
                },
            ],
        })

        summary = await client.get_price_summary("elden", "CN")

        self.assertEqual(89.4, summary["steam_low"])
        self.assertEqual(70, summary["steam_low_cut"])
