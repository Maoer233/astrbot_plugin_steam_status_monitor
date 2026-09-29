import unittest

from src.infrastructure.cache.price_cache import PriceTTLCache


class PriceTTLCacheTests(unittest.TestCase):
    def test_hit_and_expire(self):
        now = [100.0]
        cache = PriceTTLCache({"price": 5}, clock=lambda: now[0])
        cache.set("price", "appid:CN", {"current_price": 10})
        self.assertEqual({"current_price": 10}, cache.get("price", "appid:CN"))
        now[0] = 105.0
        self.assertIsNone(cache.get("price", "appid:CN"))

    def test_clear_removes_entries(self):
        cache = PriceTTLCache({"price": 5})
        cache.set("price", "key", "value")
        cache.clear()
        self.assertIsNone(cache.get("price", "key"))


if __name__ == "__main__":
    unittest.main()
