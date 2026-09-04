import unittest
from unittest.mock import AsyncMock, patch

from src.infrastructure.clients.itad import ITADClient
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
            patch.object(client, "_steam_storesearch", AsyncMock(side_effect=[steam_items, []])),
            patch.object(client, "_steam_search_html", AsyncMock(return_value=[])),
            patch.object(client, "_steam_english_title", AsyncMock(side_effect=lambda appid: {
                "1126320": "Being a DIK - Season 1",
                "1807120": "Being a DIK - Season 3",
                "5071090": "The New Han Prince 3: Brother-in-Law's Steamy Indulgence",
            }.get(appid, ""))),
            patch.object(client, "_get", AsyncMock(return_value=[])),
        ):
            games = await client.search_games("being a dik")

        self.assertEqual(["1126320", "1807120"], [game.appid for game in games])
        self.assertTrue(all(game.id.startswith("steam:") for game in games))

    async def test_html_unrelated_hits_are_ignored(self):
        client = ITADClient(api_key="test")
        html_items = [
            {"id": "4287260", "name": "Rebirth: If Only I Had Grown Up Right"},
            {"id": "2509780", "name": "汉武大帝传-国士无双礼包"},
        ]
        with (
            patch.object(client, "_steam_storesearch", AsyncMock(return_value=[])),
            patch.object(client, "_steam_search_html", AsyncMock(return_value=html_items)),
            patch.object(client, "_get", AsyncMock(return_value=[
                {"id": "itad-wrong", "title": "The New Han Prince 3: Brother-in-Law's Steamy Indulgence"},
            ])),
        ):
            games = await client.search_games("being a dik")

        self.assertEqual([], games)

    async def test_itad_exact_title_is_kept_fuzzy_mismatch_is_not(self):
        client = ITADClient(api_key="test")
        steam_items = [
            {"id": "1126320", "name": "Being a DIK - Season 1", "tiny_image": "s1.jpg"},
        ]
        with (
            patch.object(client, "_steam_storesearch", AsyncMock(side_effect=[steam_items, []])),
            patch.object(client, "_steam_search_html", AsyncMock(return_value=[])),
            patch.object(client, "_steam_english_title", AsyncMock(return_value="Being a DIK - Season 1")),
            patch.object(client, "_get", AsyncMock(return_value=[
                {"id": "itad-s1", "title": "Being a DIK - Season 1"},
                {"id": "itad-wrong", "title": "The New Han Prince 3: Brother-in-Law's Steamy Indulgence"},
            ])),
        ):
            games = await client.search_games("being a dik")

        self.assertEqual(["1126320"], [game.appid for game in games])
        self.assertEqual(["itad-s1"], [game.id for game in games])
