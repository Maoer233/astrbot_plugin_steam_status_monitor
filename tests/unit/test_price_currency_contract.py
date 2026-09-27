import unittest
from decimal import Decimal

from src.domain.price.models import Money
from src.shared.utils.price import summary_to_currency


class MoneyTests(unittest.TestCase):
    def test_normalizes_amount_and_currency(self):
        money = Money("10.50", "usd")
        self.assertEqual(Decimal("10.50"), money.amount)
        self.assertEqual("USD", money.currency)

    def test_rejects_missing_currency(self):
        with self.assertRaises(ValueError):
            Money(10, "")


class SummaryCurrencyContractTests(unittest.TestCase):
    def test_converts_each_source_currency_once(self):
        summary = {
            "currency": "CNY",
            "current_price": 100,
            "current_regular": 100,
            "steam_low": 10,
            "steam_low_currency": "USD",
            "history_low": 20,
            "history_low_currency": "HKD",
            "cdk_amount": 10,
            "cdk_currency": "USD",
        }
        converted = summary_to_currency(summary, "CNY")
        self.assertEqual(100, converted["current_price"])
        self.assertEqual(67.25, converted["steam_low"])
        self.assertEqual("CNY", converted["steam_low_currency"])
        # history_low 使用独立 HKD 来源：20 * 0.8576 = 17.15
        self.assertEqual(17.15, converted["history_low"])
        self.assertEqual("CNY", converted["history_low_currency"])
        self.assertEqual(67.25, converted["cdk_amount"])
        self.assertEqual("CNY", converted["cdk_currency"])
        self.assertEqual("USD", summary["cdk_currency"])

    def test_unknown_source_currency_keeps_value_and_currency(self):
        summary = {"cdk_amount": 10, "cdk_currency": "XXX"}
        converted = summary_to_currency(summary, "CNY")
        self.assertEqual(10, converted["cdk_amount"])
        self.assertEqual("XXX", converted["cdk_currency"])


if __name__ == "__main__":
    unittest.main()
