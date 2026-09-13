import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from ...infrastructure.clients.itad import ITADGame
from ...shared.logging import logger
from ...shared.utils.price import (
    CURRENCY_REGION,
    extract_steam_appid,
    is_store_region_locked,
    store_region_candidates,
    summary_to_currency,
)
from ...presentation.renderers.game_detail import COUNTRY_LABEL


Translator = Callable[[str], Awaitable[str]]


def contains_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


@dataclass
class PriceCard:
    game: Optional[ITADGame]
    summary: dict = field(default_factory=dict)
    region_prices: dict = field(default_factory=dict)
    detail: Optional[dict] = None
    reviews: Optional[dict] = None
    card_data: dict = field(default_factory=dict)
    store_url: str = ""
    store_message: str = ""
    locked: bool = False


class PriceQueryService:
    """价格/详情用例：搜索、ITAD 无价换区、拼卡片 DTO。区回退与 DLC 过滤仍在 client。"""

    def __init__(self, plugin, translator: Optional[Translator] = None):
        self._plugin = plugin
        self._translator = translator

    def _settings(self):
        config = getattr(self._plugin, "config", {}) or {}
        currency = (config.get("price_currency", "CNY") or "CNY").strip().upper() or "CNY"
        region = (config.get("price_region", "") or "").strip().upper()
        if not region:
            region = CURRENCY_REGION.get(currency, "CN")
        compare_raw = (config.get("price_compare_regions", "UA") or "NONE").strip()
        compare_region = compare_raw.split(",")[0].strip().upper()
        return currency, region, compare_region

    async def resolve_games(self, query: str) -> List[ITADGame]:
        query = str(query or "").strip()
        if not query:
            return []
        client = self._plugin.ITAD_CLIENT
        url_appid = extract_steam_appid(query)
        if url_appid:
            game = await client.lookup_steam_appid(url_appid)
            return [game] if game is not None else []
        games = await client.search_games(query)
        if games:
            return games
        if self._translator is not None and contains_chinese(query):
            translated = await self._translator(query)
            if translated and translated != query:
                games = await client.search_games(translated)
        return games or []

    async def build_card(
        self,
        game: ITADGame,
        *,
        include_itad: bool = True,
        include_reviews: Optional[bool] = None,
    ) -> PriceCard:
        if include_reviews is None:
            include_reviews = True
        currency, region, compare_region = self._settings()
        summary = {}
        if include_itad and game.id:
            summary = await self._plugin.ITAD_CLIENT.get_price_summary(game.id, region) or {}
            if summary.get("current_price") is None:
                for fallback_region in store_region_candidates(region)[1:]:
                    fallback_summary = await self._plugin.ITAD_CLIENT.get_price_summary(game.id, fallback_region) or {}
                    if fallback_summary.get("current_price") is not None:
                        logger.info(
                            "ITAD %s 区无价格，改用 %s 区 (game=%s)",
                            region,
                            fallback_region,
                            game.id,
                        )
                        summary = fallback_summary
                        break
            summary = summary_to_currency(summary, currency)
        region_codes = [region]
        if include_itad and compare_region and compare_region != "NONE" and compare_region != region:
            region_codes.append(compare_region)
        region_prices = {}
        if game.appid and include_itad:
            region_summaries = await asyncio.gather(
                *[self._plugin.fetch_region_price(game.appid, code) for code in region_codes]
            )
            for code, region_summary in zip(region_codes, region_summaries):
                if not region_summary:
                    continue
                actual = str(region_summary.get("region") or code).upper()
                region_prices[actual] = summary_to_currency(region_summary, currency)
        detail = await self._plugin.fetch_game_details(game.appid, country=region) if game.appid else None
        reviews = None
        if include_reviews and game.appid:
            reviews = await self._plugin.fetch_game_reviews_both(game.appid)
        if detail:
            detail["review_all"] = (reviews or {}).get("all") or {}
            detail["review_schinese"] = (reviews or {}).get("schinese") or {}
        store_appid = (detail or {}).get("store_appid") or game.appid
        store_url = f"https://store.steampowered.com/app/{store_appid}/" if store_appid else ""
        actual_store_region = str((detail or {}).get("_store_region") or "").upper()
        locked = bool(store_url and is_store_region_locked(region, actual_store_region, region_prices))
        store_message = store_url
        if locked and store_url:
            region_label = COUNTRY_LABEL.get(region, region)
            store_message = f"{store_url}\n当前游戏锁{region_label}"
        card_data = detail or {
            "name": game.title,
            "header_image": game.image,
            "short_description": "由 ITAD 提供当前价格与历史最低价信息。" if include_itad else "",
            "genres": [],
            "developers": [],
            "release_date": {"date": "未知"},
            "price_overview": {},
            "review_all": (reviews or {}).get("all") or {},
            "review_schinese": (reviews or {}).get("schinese") or {},
        }
        return PriceCard(
            game=game,
            summary=summary,
            region_prices=region_prices,
            detail=detail,
            reviews=reviews,
            card_data=card_data,
            store_url=store_url,
            store_message=store_message,
            locked=locked,
        )

    async def build_store_card(self, appid: str) -> Optional[PriceCard]:
        appid = str(appid or "").strip()
        if not appid.isdigit():
            return None
        game = ITADGame(id="", title="", appid=appid)
        card = await self.build_card(game, include_itad=False, include_reviews=False)
        if not card.detail:
            return None
        return card
