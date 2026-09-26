import asyncio
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from ...infrastructure.cache.inflight import InFlightRequests
from ...infrastructure.cache.price_cache import PriceTTLCache
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


@dataclass(frozen=True)
class PriceQuerySettings:
    currency: str
    region: str
    compare_region: str


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
        self._cache = PriceTTLCache()
        self._inflight = InFlightRequests()

    async def close(self):
        await self._inflight.clear()

    def _settings(self) -> PriceQuerySettings:
        config = getattr(self._plugin, "config", {}) or {}
        currency = (config.get("price_currency", "CNY") or "CNY").strip().upper() or "CNY"
        region = (config.get("price_region", "") or "").strip().upper()
        if not region:
            region = CURRENCY_REGION.get(currency, "CN")
        compare_raw = (config.get("price_compare_regions", "UA") or "NONE").strip()
        compare_region = compare_raw.split(",")[0].strip().upper()
        return PriceQuerySettings(
            currency=currency,
            region=region,
            compare_region=compare_region,
        )

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
        try:
            async with asyncio.timeout(20):
                return await self._build_card_with_budget(
                    game,
                    include_itad=include_itad,
                    include_reviews=include_reviews,
                )
        except TimeoutError:
            logger.warning("价格卡查询超时 (game=%s, appid=%s)", game.id, game.appid)
            raise

    async def _fetch_itad_summary(self, game: ITADGame, settings: PriceQuerySettings) -> dict:
        if not game.id:
            return {}
        cache_key = f"{game.id}:{settings.region}"
        cached = self._cache.get("itad_summary", cache_key)
        if cached is not None:
            return dict(cached)
        async def request_summary():
            return await self._plugin.ITAD_CLIENT.get_price_summary(game.id, settings.region) or {}

        summary = await self._inflight.get_or_create(
            f"itad_summary:{game.id}:{settings.region}", request_summary
        )
        summary = dict(summary)
        if summary.get("current_price") is None:
            for fallback_region in store_region_candidates(settings.region)[1:]:
                fallback_key = f"{game.id}:{fallback_region}"
                fallback_summary = self._cache.get("itad_summary", fallback_key)
                if fallback_summary is None:
                    async def request_fallback(region=fallback_region):
                        return await self._plugin.ITAD_CLIENT.get_price_summary(
                            game.id, region
                        ) or {}
                    fallback_summary = await self._inflight.get_or_create(
                        f"itad_summary:{game.id}:{fallback_region}", request_fallback
                    )
                    if fallback_summary:
                        self._cache.set("itad_summary", fallback_key, fallback_summary)
                if fallback_summary.get("current_price") is not None:
                    logger.info(
                        "ITAD %s 区无价格，改用 %s 区 (game=%s)",
                        settings.region,
                        fallback_region,
                        game.id,
                    )
                    summary = dict(fallback_summary)
                    break
        if summary:
            self._cache.set("itad_summary", cache_key, summary)
        return summary_to_currency(summary, settings.currency)

    async def _fetch_region_prices(self, game: ITADGame, settings: PriceQuerySettings) -> dict:
        if not game.appid:
            return {}
        region_codes = [settings.region]
        if (
            settings.compare_region
            and settings.compare_region != "NONE"
            and settings.compare_region != settings.region
        ):
            region_codes.append(settings.compare_region)
        region_summaries = await asyncio.gather(
            *[self._fetch_region_price_cached(game.appid, code) for code in region_codes],
            return_exceptions=True,
        )
        region_prices = {}
        for code, region_summary in zip(region_codes, region_summaries):
            if isinstance(region_summary, BaseException) or not region_summary:
                continue
            actual = str(region_summary.get("region") or code).upper()
            region_prices[actual] = summary_to_currency(region_summary, settings.currency)
        return region_prices

    async def _fetch_region_price_cached(self, appid: str, region: str):
        key = f"{appid}:{region}"
        cached = self._cache.get("region_price", key)
        if cached is not None:
            return dict(cached)
        async def request_price():
            return await self._plugin.fetch_region_price(appid, region)

        result = await self._inflight.get_or_create(
            f"region_price:{appid}:{region}", request_price
        )
        result = dict(result) if result else result
        if result:
            self._cache.set("region_price", key, result)
        return result

    async def _fetch_store_details(self, game: ITADGame, settings: PriceQuerySettings):
        if not game.appid:
            return None
        key = f"{game.appid}:{settings.region}:schinese"
        cached = self._cache.get("store_detail", key)
        if cached is not None:
            return dict(cached)
        async def request_detail():
            return await self._plugin.fetch_game_details(game.appid, country=settings.region)
        detail = await self._inflight.get_or_create(
            f"store_detail:{game.appid}:{settings.region}:schinese", request_detail
        )
        if detail:
            self._cache.set("store_detail", key, detail)
        return dict(detail) if detail else detail

    async def _fetch_reviews(self, game: ITADGame, include_reviews: bool):
        if not include_reviews or not game.appid:
            return None
        try:
            return await self._plugin.fetch_game_reviews_both(game.appid)
        except Exception as exc:
            logger.warning("Steam 评价获取失败，继续生成价格卡 (appid=%s): %s", game.appid, exc)
            return None

    async def _build_card_with_budget(
        self,
        game: ITADGame,
        *,
        include_itad: bool,
        include_reviews: Optional[bool],
    ) -> PriceCard:
        if include_reviews is None:
            include_reviews = True
        settings = self._settings()
        summary = await self._fetch_itad_summary(game, settings) if include_itad else {}
        region_prices = await self._fetch_region_prices(game, settings) if include_itad else {}
        detail = await self._fetch_store_details(game, settings)
        reviews = await self._fetch_reviews(game, include_reviews)
        if detail:
            detail["review_all"] = (reviews or {}).get("all") or {}
            detail["review_schinese"] = (reviews or {}).get("schinese") or {}
        store_appid = (detail or {}).get("store_appid") or game.appid
        store_url = f"https://store.steampowered.com/app/{store_appid}/" if store_appid else ""
        actual_store_region = str((detail or {}).get("_store_region") or "").upper()
        locked = bool(store_url and is_store_region_locked(settings.region, actual_store_region, region_prices))
        store_message = store_url
        if locked and store_url:
            region_label = COUNTRY_LABEL.get(settings.region, settings.region)
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
