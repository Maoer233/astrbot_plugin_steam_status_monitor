import unittest

from src.domain.ranking.push_scopes import build_rank_push_scopes


class RankPushScopeTests(unittest.TestCase):
    def test_default_mode_uses_each_target_group_as_its_data_scope(self):
        self.assertEqual(
            build_rank_push_scopes(["group-a", "group-b"]),
            [("group-a", "group-a"), ("group-b", "group-b")],
        )

    def test_global_mode_is_explicit_and_keeps_delivery_targets(self):
        self.assertEqual(
            build_rank_push_scopes(["group-a", "group-b"], use_global_rank=True),
            [("group-a", None), ("group-b", None)],
        )

    def test_group_ids_are_normalized_and_deduplicated(self):
        self.assertEqual(
            build_rank_push_scopes([123, "123", "", None, " 456 "]),
            [("123", "123"), ("456", "456")],
        )


if __name__ == "__main__":
    unittest.main()
