import unittest
from unittest.mock import AsyncMock, patch

from src.infrastructure.clients.errors import ProviderError
from src.infrastructure.clients.itad import ITADClient, ITADGame
from src.shared.utils.price import extract_price_query


class PriceQueryExtractionTests(unittest.TestCase):
    def test_strips_full_steam_price_command(self):
        self.assertEqual(
            "being a dik",
            extract_price_query("/steam price being a dik", "price"),
        )
        self.assertEqual(
            "being a dik",
            extract_price_query("steam price being a dik", "price"),
        )
        self.assertEqual(
            "being a dik",
            extract_price_query("/steam px being a dik", "px"),
        )

    def test_keeps_already_stripped_query(self):
        self.assertEqual(
            "being a dik",
            extract_price_query("being a dik", "price"),
        )
        self.assertEqual(
            "being a dik",
            extract_price_query("price being a dik", "price"),
        )


class TitleMatchTests(unittest.TestCase):
    def test_being_a_dik_matches_series_not_steamy_dlc(self):
        query = "being a dik"
        self.assertTrue(ITADClient._title_matches_query("Being a DIK - Season 1", query))
        self.assertTrue(ITADClient._title_matches_query("Being a DIK - Season 3", query))
        self.assertFalse(
            ITADClient._title_matches_query(
                "The New Han Prince 3: Brother-in-Law's Steamy Indulgence",
                query,
            )
        )
        self.assertFalse(
            ITADClient._title_matches_query(
                "The New Han Prince 3: Brother-in-Law's Steamy Indulgence",
                "steam price being a dik",
            )
        )

    def test_single_token_still_matches(self):
        self.assertTrue(ITADClient._title_matches_query("Being a DIK - Season 1", "dik"))
        self.assertFalse(ITADClient._title_matches_query("Steam Machine", "dik"))
        self.assertFalse(
            ITADClient._title_matches_query(
                "The New Han Prince 3: Brother-in-Law's Steamy Indulgence",
                "steam",
            )
        )

    def test_chinese_base_game_does_not_match_english_query(self):
        self.assertFalse(ITADClient._title_matches_query("艾尔登法环", "ELDEN RING"))
        self.assertTrue(
            ITADClient._title_matches_query("ELDEN RING Tarnished Pack", "ELDEN RING")
        )


class SearchGamesTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_steam_hits_when_itad_is_empty(self):
        client = ITADClient(api_key="test")
        steam_items = [
            {"id": "1126320", "name": "Being a DIK - Season 1", "tiny_image": "s1.jpg"},
            {"id": "1807120", "name": "Being a DIK - Season 3", "tiny_image": "s3.jpg"},
            {
                "id": "5071090",
                "name": "The New Han Prince 3: Brother-in-Law's Steamy Indulgence",
                "tiny_image": "wrong.jpg",
            },
        ]
        with (
            patch.object(client, "_steam_storesearch", AsyncMock(side_effect=[(steam_items, "SUCCESS"), ([], "EMPTY")])),
            patch.object(client, "_steam_search_html", AsyncMock(return_value=[])),
            patch.object(client, "_steam_english_title", AsyncMock(side_effect=lambda appid: {
                "1126320": "Being a DIK - Season 1",
                "1807120": "Being a DIK - Season 3",
                "5071090": "The New Han Prince 3: Brother-in-Law's Steamy Indulgence",
            }.get(appid, ""))),
            patch.object(client, "_get", AsyncMock(return_value=[])),
        ):
            result = await client.search_games("being a dik")

        games = result["games"]
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual(["1126320", "1807120"], [game.appid for game in games])
        self.assertTrue(all(game.id.startswith("steam:") for game in games))

    async def test_html_unrelated_hits_are_ignored(self):
        client = ITADClient(api_key="test")
        html_items = [
            {"id": "4287260", "name": "Rebirth: If Only I Had Grown Up Right"},
            {"id": "2509780", "name": "汉武大帝传-国士无双礼包"},
        ]
        with (
            patch.object(client, "_steam_storesearch", AsyncMock(return_value=([], "EMPTY"))),
            patch.object(client, "_steam_search_html", AsyncMock(return_value=html_items)),
            patch.object(client, "_get", AsyncMock(return_value=[
                {"id": "itad-wrong", "title": "The New Han Prince 3: Brother-in-Law's Steamy Indulgence"},
            ])),
        ):
            result = await client.search_games("being a dik")

        self.assertEqual([], result["games"])
        self.assertEqual("EMPTY", result["status"])

    async def test_itad_exact_title_is_kept_fuzzy_mismatch_is_not(self):
        client = ITADClient(api_key="test")
        steam_items = [
            {"id": "1126320", "name": "Being a DIK - Season 1", "tiny_image": "s1.jpg"},
        ]
        with (
            patch.object(client, "_steam_storesearch", AsyncMock(side_effect=[(steam_items, "SUCCESS"), ([], "EMPTY")])),
            patch.object(client, "_steam_search_html", AsyncMock(return_value=[])),
            patch.object(client, "_steam_english_title", AsyncMock(return_value="Being a DIK - Season 1")),
            patch.object(client, "_get", AsyncMock(return_value=[
                {"id": "itad-s1", "title": "Being a DIK - Season 1"},
                {"id": "itad-wrong", "title": "The New Han Prince 3: Brother-in-Law's Steamy Indulgence"},
            ])),
        ):
            result = await client.search_games("being a dik")

        games = result["games"]
        self.assertEqual(["1126320"], [game.appid for game in games])
        self.assertEqual(["itad-s1"], [game.id for game in games])


class SteamItemRankingTests(unittest.TestCase):
    def test_filter_keeps_localized_base_game_ahead_of_dlc(self):
        client = ITADClient(api_key="test")
        items = [
            {"id": "3655690", "name": "ELDEN RING Tarnished Pack", "type": "dlc"},
            {"id": "1245620", "name": "艾尔登法环", "type": "game"},
            {"id": "2778580", "name": "ELDEN RING NIGHTREIGN", "type": "game"},
        ]

        filtered = client._filter_steam_items(items, "ELDEN RING", 6, keep_localized=True)

        self.assertEqual(["1245620", "2778580", "3655690"], [item["id"] for item in filtered])

    def test_exact_english_title_outranks_dlc_even_without_type(self):
        client = ITADClient(api_key="test")
        items = [
            {"id": "3655690", "name": "ELDEN RING Tarnished Pack"},
            {"id": "1245620", "name": "ELDEN RING"},
        ]

        filtered = client._filter_steam_items(items, "ELDEN RING", 6)

        self.assertEqual(["1245620", "3655690"], [item["id"] for item in filtered])


class SearchAssociationTests(unittest.IsolatedAsyncioTestCase):
    async def test_deduplicates_same_itad_id_and_binds_appid(self):
        client = ITADClient(api_key="test")
        steam_items = [
            {"id": "1245620", "name": "ELDEN RING", "tiny_image": "base.jpg"},
            {"id": "1245621", "name": "ELDEN RING", "tiny_image": "alt.jpg"},
        ]
        with (
            patch.object(client, "_steam_storesearch", AsyncMock(side_effect=[(steam_items, "SUCCESS"), ([], "EMPTY")])),
            patch.object(client, "_steam_search_html", AsyncMock(return_value=[])),
            patch.object(client, "_steam_english_title", AsyncMock(return_value="ELDEN RING™")),
            patch.object(client, "_get", AsyncMock(return_value=[
                {"id": "itad-elden", "title": "ELDEN RING\x99"},
            ])),
        ):
            result = await client.search_games("ELDEN RING")

        games = result["games"]
        self.assertEqual(1, len(games))
        self.assertEqual("itad-elden", games[0].id)
        self.assertEqual("1245620", games[0].appid)
        self.assertEqual("base.jpg", games[0].image)
        self.assertFalse(hasattr(client, "resolve_query"))
        self.assertFalse(hasattr(client, "match_itad_game"))
        self.assertFalse(hasattr(client, "deduplicate_games"))

    async def test_itad_fallback_binds_exact_steam_appid(self):
        client = ITADClient(api_key="test")
        with (
            patch.object(client, "_lookup_steam_items", AsyncMock(return_value=[])),
            patch.object(client, "_get", AsyncMock(return_value=[
                {"id": "itad-elden", "title": "ELDEN RING"},
            ])),
            patch.object(client, "_steam_search", AsyncMock(return_value=[
                {"id": "1245620", "name": "ELDEN RING", "tiny_image": "base.jpg"},
                {"id": "3655690", "name": "ELDEN RING Tarnished Pack"},
            ])),
        ):
            result = await client.search_games("ELDEN RING")

        games = result["games"]
        self.assertEqual("SUCCESS", result["status"])
        self.assertEqual("itad", result["provider"])
        self.assertTrue(result["used_fallback"])
        self.assertEqual("itad-elden", games[0].id)
        self.assertEqual("1245620", games[0].appid)

    def test_temporary_identity_is_not_matched_as_itad_id(self):
        matched = ITADClient._match_itad_game("ELDEN RING™", [
            ITADGame("steam:1245620", "ELDEN RING™"),
            ITADGame("itad-elden", "ELDEN RING\x99"),
        ])

        self.assertEqual("itad-elden", matched.id)


class SteamEnglishTitleTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_title_without_identity_or_ranking(self):
        client = ITADClient(api_key="test")

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"1245620": {"success": True, "data": {"name": "ELDEN RING"}}}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def get(self, url, params=None):
                self.params = params
                return _Response()

        http = _Client()
        with patch("src.infrastructure.clients.itad.httpx.AsyncClient", return_value=http):
            title = await client._steam_english_title("1245620")

        self.assertEqual("ELDEN RING", title)
        self.assertIsInstance(title, str)
        self.assertEqual("english", http.params["l"])


class SearchGamesRankingTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_elden_ring_over_tarnished_pack(self):
        client = ITADClient(api_key="test")
        steam_items = [
            {"id": "3655690", "name": "ELDEN RING Tarnished Pack", "type": "dlc", "tiny_image": "dlc.jpg"},
            {"id": "1245620", "name": "艾尔登法环", "type": "game", "tiny_image": "base.jpg"},
        ]
        english_titles = {
            "3655690": "ELDEN RING Tarnished Pack",
            "1245620": "ELDEN RING",
        }
        with (
            patch.object(client, "_steam_storesearch", AsyncMock(side_effect=[(steam_items, "SUCCESS"), ([], "EMPTY")])),
            patch.object(client, "_steam_search_html", AsyncMock(return_value=[])),
            patch.object(
                client,
                "_steam_english_title",
                AsyncMock(side_effect=lambda appid: english_titles.get(appid, "")),
            ),
            patch.object(client, "_get", AsyncMock(return_value=[])),
        ):
            result = await client.search_games("ELDEN RING")

        games = result["games"]
        self.assertEqual(["1245620", "3655690"], [game.appid for game in games])
        self.assertEqual("ELDEN RING", games[0].title)

    async def test_keeps_localized_base_game_when_english_title_is_missing(self):
        client = ITADClient(api_key="test")
        steam_items = [
            {"id": "3655690", "name": "ELDEN RING Tarnished Pack", "type": "dlc"},
            {"id": "1245620", "name": "艾尔登法环", "type": "app"},
        ]
        with (
            patch.object(client, "_steam_storesearch", AsyncMock(side_effect=[(steam_items, "SUCCESS"), ([], "EMPTY")])),
            patch.object(client, "_steam_search_html", AsyncMock(return_value=[])),
            patch.object(client, "_steam_english_title", AsyncMock(return_value="")),
            patch.object(client, "_get", AsyncMock(return_value=[])),
        ):
            result = await client.search_games("ELDEN RING")

        games = result["games"]
        self.assertEqual("1245620", games[0].appid)

    async def test_itad_direct_search_failure_is_not_empty(self):
        client = ITADClient(api_key="test")
        with (
            patch.object(client, "_lookup_steam_items", AsyncMock(return_value=[])),
            patch.object(client, "_get", AsyncMock(side_effect=ProviderError("TIMEOUT", retryable=True))),
        ):
            result = await client.search_games("ELDEN RING")

        self.assertEqual([], result["games"])
        self.assertEqual("TIMEOUT", result["status"])
        self.assertEqual("itad", result["provider"])
        self.assertTrue(result["retryable"])
        self.assertTrue(result["used_fallback"])
        self.assertFalse(hasattr(client, "SearchResult"))
