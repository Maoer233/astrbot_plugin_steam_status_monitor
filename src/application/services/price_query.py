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
    history_low: dict = field(default_factory=dict)
    current_price: dict = field(default_factory=dict)
    card_data: dict = field(default_factory=dict)
    store_url: str = ""
    store_message: str = ""
    locked: bool = False


class PriceQueryService:
    """价格/详情用例：搜索、ITAD 无价换区、拼卡片 DTO。区回退与 DLC 过滤仍在 client。"""

    TOTAL_BUDGET_SECONDS = 20
    CORE_BUDGET_SECONDS = 12
    REGION_ATTEMPT_SECONDS = 4

    def __init__(self, plugin, translator: Optional[Translator] = None):
        self._plugin = plugin
        self._translator = translator
        self._cache = PriceTTLCache()
        self._inflight = InFlightRequests()

    async def close(self):
        await self._inflight.clear()

    SUPPORTED_CURRENCIES = frozenset(CURRENCY_REGION)
    SUPPORTED_REGIONS = frozenset({
        "CN", "US", "JP", "KR", "RU", "UA", "TR", "HK", "TW", "GB", "DE", "PL", "BR", "IN",
    })

    def _settings(self) -> PriceQuerySettings:
        config = getattr(self._plugin, "config", {}) or {}
        currency = (config.get("price_currency", "CNY") or "CNY").strip().upper() or "CNY"
        if currency not in self.SUPPORTED_CURRENCIES:
            logger.warning("价格主货币 %s 不受支持，回退 CNY", currency)
            currency = "CNY"
        region = (config.get("price_region", "") or "").strip().upper()
        if not region:
            region = CURRENCY_REGION.get(currency, "CN")
        elif region not in self.SUPPORTED_REGIONS:
            fallback = CURRENCY_REGION.get(currency, "CN")
            logger.warning("价格对比区1 %s 不受支持，回退 %s", region, fallback)
            region = fallback
        compare_raw = (config.get("price_compare_regions", "NONE") or "NONE").strip()
        compare_region = compare_raw.split(",")[0].strip().upper() or "NONE"
        if compare_region not in self.SUPPORTED_REGIONS | {"NONE"}:
            logger.warning("价格对比区2 %s 不受支持，回退 NONE", compare_region)
            compare_region = "NONE"
        return PriceQuerySettings(
            currency=currency,
            region=region,
            compare_region=compare_region,
        )

    @staticmethod
    def _search_message(status: str) -> str:
        return {
            "EMPTY": "未找到匹配游戏。",
            "NOT_CONFIGURED": "ITAD 未配置，已无法查询完整价格。",
            "TIMEOUT": "价格搜索超时，请稍后重试。",
            "RATE_LIMITED": "价格搜索请求受限，请稍后重试。",
            "AUTH_ERROR": "ITAD 鉴权失败，请管理员检查配置。",
            "UPSTREAM_ERROR": "价格搜索暂时不可用，请稍后重试。",
            "INVALID_RESPONSE": "价格搜索返回异常，请稍后重试。",
        }.get(status, "未找到匹配游戏。")

    async def resolve_games(self, query: str) -> dict:
        query = str(query or "").strip()
        if not query:
            return {"games": [], "status": "EMPTY", "provider": "", "retryable": False, "used_fallback": False}
        client = self._plugin.ITAD_CLIENT
        url_appid = extract_steam_appid(query)
        if url_appid:
            game = await client.lookup_steam_appid(url_appid)
            if game is not None:
                return {"games": [game], "status": "SUCCESS", "provider": "itad", "retryable": False, "used_fallback": False}
            # lookup 失败不要求 ITAD 价格接口可用，直接用 AppID 出 Steam-only 详情卡。
            return {
                "games": [ITADGame(id=f"steam:{url_appid}", title="", appid=url_appid)],
                "status": "SUCCESS",
                "provider": "steam",
                "retryable": False,
                "used_fallback": True,
            }
        result = await client.search_games(query)
        games = list(result.get("games") or [])
        status = str(result.get("status") or "EMPTY")
        if games or status != "EMPTY" or self._translator is None or not contains_chinese(query):
            return result
        translated = await self._translator(query)
        if not translated or translated == query or contains_chinese(translated):
            return result
        retried = await client.search_games(translated)
        retried = dict(retried)
        retried["used_fallback"] = True
        return retried

    async def build_card(
        self,
        game: ITADGame,
        *,
        include_itad: bool = True,
        include_reviews: Optional[bool] = None,
    ) -> PriceCard:
        started = asyncio.get_running_loop().time()
        core_deadline = started + self.CORE_BUDGET_SECONDS
        core_task = asyncio.create_task(
            self._build_core_card(game, include_itad=include_itad, deadline=core_deadline)
        )
        try:
            card = await asyncio.wait_for(core_task, self.CORE_BUDGET_SECONDS)
        except TimeoutError:
            core_task.cancel()
            logger.warning("价格卡核心数据超时 (game=%s, appid=%s)", game.id, game.appid)
            raise
        if include_reviews is None:
            include_reviews = True
        remaining = self.TOTAL_BUDGET_SECONDS - (asyncio.get_running_loop().time() - started)
        enhancement_deadline = asyncio.get_running_loop().time() + remaining
        if remaining <= 0:
            logger.info("价格卡增强数据预算已用尽，返回核心结果 (game=%s)", game.id)
            return card
        core_regions = dict(card.region_prices)
        try:
            async with asyncio.timeout(remaining):
                await self._attach_enhancements(
                    card,
                    game,
                    include_itad=include_itad,
                    include_reviews=include_reviews,
                    deadline=enhancement_deadline,
                )
        except TimeoutError:
            logger.info("价格卡增强数据超时，返回核心结果 (game=%s, appid=%s)", game.id, game.appid)
            card.reviews = None
            card.region_prices = core_regions
            card.card_data["review_all"] = {}
            card.card_data["review_schinese"] = {}
        return card

    @staticmethod
    def _select_current_price(
        detail: Optional[dict],
        summary: dict,
        *,
        requested_region: str = "",
        region_prices: Optional[dict] = None,
    ) -> dict:
        requested = str(requested_region or "").upper()
        primary = (region_prices or {}).get(requested) or {}
        if summary.get("current_price") is not None:
            actual = str(summary.get("region") or requested).upper()
            return {
                "value": summary.get("current_price"),
                "regular": summary.get("current_regular"),
                "currency": summary.get("currency") or "",
                "cut": summary.get("cut") or 0,
                "source": "itad_steam",
                "requested_region": requested,
                "actual_region": actual,
                "is_fallback": bool(requested and actual and actual != requested),
            }
        if primary.get("current_price") is not None:
            return {
                "value": primary.get("current_price"),
                "regular": primary.get("current_regular"),
                "currency": primary.get("currency") or "",
                "cut": primary.get("cut") or 0,
                "source": "steam_store",
                "requested_region": requested,
                "actual_region": requested,
                "is_fallback": False,
            }
        price = (detail or {}).get("price_overview") or {}
        if price:
            actual = str((detail or {}).get("_store_region") or requested).upper()
            return {
                "value": price.get("final", 0) / 100,
                "regular": price.get("initial", 0) / 100,
                "currency": price.get("currency") or "",
                "cut": price.get("discount_percent") or 0,
                "source": "steam_store",
                "requested_region": requested,
                "actual_region": actual,
                "is_fallback": bool((detail or {}).get("_store_fallback") or (requested and actual != requested)),
            }
        return {
            "value": None,
            "regular": None,
            "currency": "",
            "cut": 0,
            "source": "none",
            "requested_region": requested,
            "actual_region": "",
            "is_fallback": False,
        }

    @staticmethod
    def _select_history_low(summary: dict) -> dict:
        if summary.get("steam_low") is not None:
            return {
                "value": summary.get("steam_low"),
                "currency": summary.get("steam_low_currency") or summary.get("currency") or "",
                "cut": summary.get("steam_low_cut"),
                "source": "itad_steam_store_low",
            }
        value = summary.get("history_low")
        if value is None:
            value = summary.get("lowest")
        return {
            "value": value,
            "currency": summary.get("history_low_currency") or summary.get("lowest_currency") or summary.get("currency") or "",
            "cut": None,
            "source": "itad_history" if value is not None else "none",
        }

    def _attempt_timeout(self, deadline):
        if deadline is None:
            return self.REGION_ATTEMPT_SECONDS
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("价格查询区域回退预算已用尽")
        return min(self.REGION_ATTEMPT_SECONDS, remaining)

    async def _fetch_itad_summary(
        self, game: ITADGame, settings: PriceQuerySettings, deadline=None
    ) -> dict:
        if not game.itad_id:
            return {}
        cache_key = f"{game.itad_id}:{settings.region}"
        cached = self._cache.get("itad_summary", cache_key)
        if cached is not None:
            return dict(cached)
        async def request_summary():
            return await self._plugin.ITAD_CLIENT.get_price_summary(
                game.itad_id, settings.region, timeout=self._attempt_timeout(deadline)
            ) or {}

        summary = await self._inflight.get_or_create(
            f"itad_summary:{game.itad_id}:{settings.region}", request_summary
        )
        summary = dict(summary)
        if summary.get("current_price") is None:
            for fallback_region in store_region_candidates(settings.region)[1:]:
                fallback_key = f"{game.itad_id}:{fallback_region}"
                fallback_summary = self._cache.get("itad_summary", fallback_key)
                if fallback_summary is None:
                    async def request_fallback(region=fallback_region):
                        return await self._plugin.ITAD_CLIENT.get_price_summary(
                            game.itad_id, region, timeout=self._attempt_timeout(deadline)
                        ) or {}
                    fallback_summary = await self._inflight.get_or_create(
                        f"itad_summary:{game.itad_id}:{fallback_region}", request_fallback
                    )
                    if fallback_summary:
                        self._cache.set("itad_summary", fallback_key, fallback_summary)
                if fallback_summary.get("current_price") is not None:
                    logger.info(
                        "ITAD %s 区无价格，改用 %s 区 (game=%s)",
                        settings.region,
                        fallback_region,
                        game.itad_id,
                    )
                    summary = dict(fallback_summary)
                    summary["region"] = fallback_region
                    summary["requested_region"] = settings.region
                    summary["is_fallback"] = True
                    summary["source"] = "itad"
                    break
        if summary:
            summary = dict(summary)
            summary.setdefault("region", settings.region)
            summary.setdefault("requested_region", settings.region)
            summary.setdefault("is_fallback", False)
            summary.setdefault("source", "itad")
            self._cache.set("itad_summary", cache_key, summary)
        return summary_to_currency(summary, settings.currency)

    async def _fetch_region_price_cached(self, appid: str, region: str, deadline=None):
        key = f"{appid}:{region}"
        cached = self._cache.get("region_price", key)
        if cached is not None:
            return dict(cached)
        async def request_price():
            return await self._plugin.fetch_region_price(appid, region, deadline=deadline)

        result = await self._inflight.get_or_create(
            f"region_price:{appid}:{region}", request_price
        )
        result = dict(result) if result else result
        if result:
            self._cache.set("region_price", key, result)
        return result

    async def _fetch_store_details(
        self, game: ITADGame, settings: PriceQuerySettings, deadline=None
    ):
        if not game.appid:
            return None
        key = f"{game.appid}:{settings.region}:schinese"
        cached = self._cache.get("store_detail", key)
        if cached is not None:
            return dict(cached)
        async def request_detail():
            return await self._plugin.fetch_game_details(
                game.appid, country=settings.region, deadline=deadline
            )
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

    async def _build_core_card(
        self, game: ITADGame, *, include_itad: bool, deadline=None
    ) -> PriceCard:
        settings = self._settings()
        summary = (
            await self._fetch_itad_summary(game, settings, deadline=deadline)
            if include_itad else {}
        )
        detail = await self._fetch_store_details(game, settings, deadline=deadline)
        region_prices = {}
        if include_itad and game.appid:
            primary = await self._fetch_region_price_cached(
                game.appid, settings.region, deadline=deadline
            )
            if primary:
                actual = str(primary.get("region") or settings.region).upper()
                priced = summary_to_currency(primary, settings.currency)
                priced["requested_region"] = settings.region
                priced["actual_region"] = actual
                priced["is_fallback"] = bool(primary.get("is_fallback") or actual != settings.region)
                priced["source"] = "steam_store"
                region_prices[actual] = priced
        reviews = None
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
            actual_label = COUNTRY_LABEL.get(actual_store_region, actual_store_region)
            fallback_note = f"，价格来自{actual_label}回退" if actual_store_region and actual_store_region != settings.region else ""
            store_message = f"{store_url}\n当前游戏锁{region_label}{fallback_note}"
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
        current_price = self._select_current_price(
            detail,
            summary,
            requested_region=settings.region,
            region_prices=region_prices,
        )
        history_low = self._select_history_low(summary)
        return PriceCard(
            game=game,
            summary=summary,
            region_prices=region_prices,
            detail=detail,
            reviews=reviews,
            history_low=history_low,
            current_price=current_price,
            card_data=card_data,
            store_url=store_url,
            store_message=store_message,
            locked=locked,
        )

    async def _attach_enhancements(
        self,
        card: PriceCard,
        game: ITADGame,
        *,
        include_itad: bool,
        include_reviews: bool,
        deadline=None,
    ):
        settings = self._settings()
        compare_task = None
        if (
            include_itad
            and game.appid
            and settings.compare_region
            and settings.compare_region not in {"NONE", settings.region}
            and settings.compare_region not in card.region_prices
        ):
            compare_task = asyncio.create_task(
                self._fetch_region_price_cached(
                    game.appid, settings.compare_region, deadline=deadline
                )
            )
        review_task = (
            asyncio.create_task(self._fetch_reviews(game, include_reviews))
            if include_reviews else None
        )
        pending = [task for task in (compare_task, review_task) if task is not None]
        try:
            if pending:
                await asyncio.gather(*pending)
        except BaseException:
            for task in pending:
                task.cancel()
            raise
        if compare_task is not None and compare_task.done() and not compare_task.cancelled():
            compare = compare_task.result()
            if isinstance(compare, BaseException):
                logger.warning("对比区价格获取失败，继续生成价格卡 (appid=%s): %s", game.appid, compare)
            elif compare:
                actual = str(compare.get("region") or settings.compare_region).upper()
                priced = summary_to_currency(compare, settings.currency)
                priced["requested_region"] = settings.compare_region
                priced["actual_region"] = actual
                priced["is_fallback"] = bool(compare.get("is_fallback") or actual != settings.compare_region)
                priced["source"] = "steam_store"
                card.region_prices[actual] = priced
        if review_task is not None and review_task.done() and not review_task.cancelled():
            reviews = review_task.result()
            if isinstance(reviews, BaseException):
                logger.warning("Steam 评价获取失败，继续生成价格卡 (appid=%s): %s", game.appid, reviews)
                reviews = None
            card.reviews = reviews
            card.card_data["review_all"] = (reviews or {}).get("all") or {}
            card.card_data["review_schinese"] = (reviews or {}).get("schinese") or {}

    async def build_store_card(self, appid: str) -> Optional[PriceCard]:
        appid = str(appid or "").strip()
        if not appid.isdigit():
            return None
        game = ITADGame(id="", title="", appid=appid)
        card = await self.build_card(game, include_itad=False, include_reviews=False)
        if not card.detail:
            return None
        return card
