"""IsThereAnyDeal 客户端：游戏搜索、当前价格与历史最低价。"""
from dataclasses import dataclass
from html import unescape
from typing import Any, Optional
import asyncio
import re
import unicodedata

import httpx

try:
    from ...shared.logging import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
try:
    from ...infrastructure.clients.errors import ProviderError
except ImportError:
    class ProviderError(RuntimeError):
        def __init__(self, code, message="", *, status_code=None, retryable=False):
            self.code = code
            self.status_code = status_code
            self.retryable = retryable
            super().__init__(message or code)
try:
    from ...shared.network import httpx_client_kwargs
except ImportError:
    def httpx_client_kwargs(proxy=None):
        return {'proxy': proxy} if proxy else {}
try:
    from .steam import steam_store_client_kwargs
except ImportError:
    def steam_store_client_kwargs(proxy=None):
        return httpx_client_kwargs(proxy)


@dataclass
class ITADGame:
    id: str
    title: str
    url: str = ""
    slug: str = ""
    image: str = ""
    appid: str = ""

    @property
    def itad_id(self) -> str:
        """真实 ITAD ID。steam:<appid> 只表示 Steam-only，不能进入价格接口。"""
        game_id = str(self.id or "")
        if not game_id or game_id.startswith("steam:"):
            return ""
        return game_id


class ITADClient:
    BASE_URL = "https://api.isthereanydeal.com"

    def __init__(self, api_key: str = "", proxy=None, base_url: str = ""):
        self.api_key = (api_key or "").strip()
        self.proxy = proxy
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self._http_client = None
        self._http_client_loop = None

    async def initialize_http_client(self):
        """在当前事件循环创建可复用的 ITAD 连接池。"""
        loop = asyncio.get_running_loop()
        if self._http_client is not None and self._http_client_loop is loop:
            return self._http_client
        await self.close_http_client()
        self._http_client = httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            **httpx_client_kwargs(self.proxy),
        )
        self._http_client_loop = loop
        return self._http_client

    async def close_http_client(self):
        client = self._http_client
        self._http_client = None
        self._http_client_loop = None
        if client is not None:
            await client.aclose()

    async def _request_client(self):
        if self._http_client is not None:
            try:
                if self._http_client_loop is asyncio.get_running_loop():
                    return self._http_client, False
            except RuntimeError:
                pass
        return httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            **httpx_client_kwargs(self.proxy),
        ), True

    @staticmethod
    def _provider_error(exc, path):
        if isinstance(exc, httpx.TimeoutException):
            return ProviderError("TIMEOUT", f"ITAD 请求超时: {path}", retryable=True)
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status == 401 or status == 403:
                return ProviderError("AUTH_ERROR", f"ITAD 鉴权失败: {path}", status_code=status)
            if status == 429:
                return ProviderError("RATE_LIMITED", f"ITAD 请求受限: {path}", status_code=status, retryable=True)
            return ProviderError("UPSTREAM_ERROR", f"ITAD 上游失败: {path}", status_code=status, retryable=status >= 500)
        if isinstance(exc, ValueError):
            return ProviderError("INVALID_RESPONSE", f"ITAD 响应解析失败: {path}")
        return ProviderError("UPSTREAM_ERROR", f"ITAD 请求失败: {path}", retryable=True)

    async def _get(self, path: str, params: dict[str, Any], timeout=None):
        if not self.api_key:
            logger.warning("ITAD 未配置 API Key，跳过请求 %s", path)
            return None
        params = {**params, "key": self.api_key}
        client, owned = await self._request_client()
        try:
            response = await client.get(f"{self.base_url}{path}", params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            error = self._provider_error(exc, path)
            logger.warning("ITAD 请求失败 %s [%s]: %s", path, error.code, exc)
            return None
        finally:
            if owned:
                await client.aclose()

    async def _post(self, path: str, body, params: dict[str, Any], timeout=None):
        if not self.api_key:
            logger.warning("ITAD 未配置 API Key，跳过请求 %s", path)
            return None
        params = {**params, "key": self.api_key}
        client, owned = await self._request_client()
        try:
            response = await client.post(
                f"{self.base_url}{path}", json=body, params=params, timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            error = self._provider_error(exc, path)
            logger.warning("ITAD 请求失败 %s [%s]: %s", path, error.code, exc)
            return None
        finally:
            if owned:
                await client.aclose()

    async def _parse_search_payload(self, payload, limit: int = 6) -> list[ITADGame]:
        if not isinstance(payload, list):
            return []
        result = []
        for item in payload[:limit]:
            if not isinstance(item, dict):
                continue
            game_id = str(item.get("id") or item.get("gameId") or "")
            title = item.get("title") or item.get("name") or ""
            assets = item.get("assets") if isinstance(item.get("assets"), dict) else {}
            image = assets.get("boxart") or assets.get("banner600") or assets.get("banner") or ""
            if game_id and title:
                result.append(ITADGame(game_id, title, item.get("url", ""), item.get("slug", ""), image))
        return result

    async def _steam_storesearch(self, query: str, language: str = "english", limit: int = 6):
        """只走 storesearch API，避免空结果时被 HTML 页的无关条目顶掉。"""
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, **steam_store_client_kwargs(self.proxy)) as client:
                response = await client.get(
                    "https://store.steampowered.com/api/storesearch/",
                    params={"term": query, "l": language, "cc": "cn"},
                )
                response.raise_for_status()
                payload = response.json()
                items = payload.get("items", []) if isinstance(payload, dict) else []
                if isinstance(items, list) and items:
                    return items[:limit]
                return []
        except Exception as exc:
            logger.warning("Steam storesearch 失败: %s", exc)
            return []

    async def _steam_search_html(self, query: str, language: str = "english", limit: int = 6):
        """商店搜索页兜底；国区成人内容经常被过滤，调用方需再做标题相关度校验。"""
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, **steam_store_client_kwargs(self.proxy)) as client:
                page = await client.get(
                    "https://store.steampowered.com/search/results/",
                    params={"term": query, "l": language, "cc": "cn", "count": limit, "json": 1},
                    headers={"Accept": "application/json"},
                )
                page.raise_for_status()
                page_payload = page.json()
                html = page_payload.get("results_html", "") if isinstance(page_payload, dict) else ""
                results = self._parse_steam_search_html(html, limit)
                if results:
                    return results

                page = await client.get(
                    "https://store.steampowered.com/search/",
                    params={"term": query, "l": language, "cc": "cn"},
                    headers={"Accept": "text/html,application/xhtml+xml"},
                )
                page.raise_for_status()
                return self._parse_steam_search_html(page.text, limit)
        except Exception as exc:
            logger.warning("Steam 搜索页失败: %s", exc)
            return []

    async def _steam_search(self, query: str, language: str = "english", limit: int = 6):
        items = await self._steam_storesearch(query, language, limit)
        if items:
            return items
        return await self._steam_search_html(query, language, limit)

    @staticmethod
    def _parse_steam_search_html(html: str, limit: int) -> list[dict[str, str]]:
        """解析 Steam 搜索结果卡片，兼容属性顺序和 class 扩展。"""
        if not isinstance(html, str) or limit <= 0:
            return []

        results = []
        seen_appids: set[str] = set()
        # 搜索结果通常是指向 /app/<appid>/... 的卡片链接；不要依赖
        # data-ds-appid 位于固定位置，也不要跨卡片寻找标题。
        card_pattern = re.compile(
            r'<a\b(?=[^>]*\bhref=["\'][^"\']*/app/(\d+)(?:/|["\']))[^>]*>([\s\S]*?)</a>',
            re.IGNORECASE,
        )
        element_pattern = re.compile(
            r'<(?P<tag>[a-z][a-z0-9]*)\b(?P<attrs>[^>]*)>(?P<body>[\s\S]*?)</(?P=tag)>',
            re.IGNORECASE,
        )
        image_pattern = re.compile(r'<img\b[^>]*\bsrc=["\']([^"\']+)["\']', re.IGNORECASE)
        for card in card_pattern.finditer(html):
            appid = card.group(1)
            if appid in seen_appids:
                continue
            title_match = None
            for element in element_pattern.finditer(card.group(2)):
                class_match = re.search(r'\bclass=["\']([^"\']*)["\']', element.group("attrs"), re.IGNORECASE)
                if class_match and re.search(r"(?:^|\s)title(?:\s|$)", class_match.group(1), re.IGNORECASE):
                    title_match = element
                    break
            if not title_match:
                continue
            title = re.sub(r"<[^>]+>", " ", title_match.group("body"))
            title = re.sub(r"\s+", " ", unescape(title)).strip()
            if not title:
                continue
            item = {"id": appid, "name": title}
            image_match = image_pattern.search(card.group(2))
            if image_match:
                item["tiny_image"] = unescape(image_match.group(1))
            results.append(item)
            seen_appids.add(appid)
            if len(results) >= limit:
                break
        return results

    async def _steam_english_title(self, appid: str) -> str:
        """只返回英文展示标题。ITAD 关联、去重和排序由 search_games() 负责。"""
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, **steam_store_client_kwargs(self.proxy)) as client:
                response = await client.get(
                    "https://store.steampowered.com/api/appdetails/",
                    params={"appids": appid, "l": "english", "cc": "cn"},
                )
                response.raise_for_status()
                payload = response.json().get(str(appid), {})
                data = payload.get("data", {}) if payload.get("success") else {}
                return str(data.get("name") or "").strip()
        except Exception as exc:
            logger.warning("Steam 英文标题获取失败 appid=%s: %s", appid, exc)
            return ""

    @staticmethod
    def _query_tokens(query: str) -> list[str]:
        tokens = re.findall(r"[0-9a-zA-Z]+|[\u4e00-\u9fff]+", str(query or "").casefold())
        return [token for token in tokens if len(token) >= 2 or token.isdigit()]

    @staticmethod
    def _token_in_title(token: str, haystack: str) -> bool:
        if re.fullmatch(r"[0-9a-z]+", token):
            return re.search(rf"(?<![0-9a-z]){re.escape(token)}(?![0-9a-z])", haystack) is not None
        return token in haystack

    @staticmethod
    def _normalize_title(s: str) -> str:
        """归一化游戏名，消除 Steam(Unicode U+2122/U+00AE) 与 ITAD(CP1252 字节) 对 ®/™ 等字符的编码差异。"""
        if not s:
            return ""
        repl = {
            "\x99": "™", "\x9c": "œ", "\x91": "‘", "\x92": "’",
            "\x93": "“", "\x94": "”", "\x85": "…", "\x96": "–", "\x97": "—",
        }
        s = "".join(repl.get(ch, ch) for ch in str(s))
        try:
            return unicodedata.normalize("NFKC", s).casefold().strip()
        except Exception:
            return str(s or "").casefold().strip()

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return any("\u4e00" <= char <= "\u9fff" for char in str(text or ""))

    @classmethod
    def _title_matches_query(cls, title: str, query: str) -> bool:
        """标题需覆盖查询词中足够多的有效 token，避免 Steamy 糊到 steam。"""
        tokens = cls._query_tokens(query)
        if not tokens:
            return bool(str(title or "").strip())
        haystack = str(title or "").casefold()
        if not haystack:
            return False
        if haystack == str(query or "").casefold().strip():
            return True
        matched = sum(1 for token in tokens if cls._token_in_title(token, haystack))
        if len(tokens) == 1:
            return matched == 1
        return matched >= max(2, (len(tokens) + 1) // 2)

    @classmethod
    def _keep_localized_steam_item(cls, item: dict, title: str, query: str) -> bool:
        """中文商店名对不上英文查询词时，仍保留非 DLC 条目，留给英文标题再过滤。"""
        item_type = str(item.get("type") or "").lower()
        if item_type in {"dlc", "bundle", "music", "video"}:
            return False
        return cls._contains_cjk(title) and not cls._contains_cjk(query)

    @classmethod
    def _steam_type_rank(cls, item_type: str) -> int:
        return {
            "game": 0,
            "app": 0,
            "software": 1,
            "dlc": 2,
            "bundle": 3,
            "music": 4,
            "video": 5,
        }.get(str(item_type or "").lower(), 1)

    @classmethod
    def _steam_item_sort_key(cls, item: dict, query: str, index: int):
        title = str(item.get("name") or "").casefold().strip()
        query_folded = str(query or "").casefold().strip()
        type_rank = cls._steam_type_rank(item.get("type"))
        exact = 0 if title == query_folded else 1
        return (exact, type_rank, index)

    @classmethod
    def _game_sort_key(cls, game: ITADGame, query: str, index: int):
        title = str(game.title or "").casefold().strip()
        query_folded = str(query or "").casefold().strip()
        exact = 0 if title == query_folded else 1
        extra = 0 if exact == 0 else max(
            0, len(cls._query_tokens(title)) - len(cls._query_tokens(query))
        )
        return (exact, extra, len(title), index)

    def _filter_steam_items(
        self, items, query: str, limit: int, keep_localized: bool = False
    ) -> list[dict]:
        result = []
        seen: set[str] = set()
        for index, item in enumerate(items or []):
            if not isinstance(item, dict):
                continue
            appid = str(item.get("id") or "")
            title = str(item.get("name") or "").strip()
            if not appid or appid in seen or not title:
                continue
            if not (
                self._title_matches_query(title, query)
                or (keep_localized and self._keep_localized_steam_item(item, title, query))
            ):
                continue
            seen.add(appid)
            result.append((index, item))
        result.sort(key=lambda pair: self._steam_item_sort_key(pair[1], query, pair[0]))
        return [item for _, item in result[:limit]]

    async def _lookup_steam_items(self, query: str, limit: int) -> list[dict]:
        """优先 storesearch；HTML 页只在过滤后仍有相关标题时才采用。"""
        fetch_limit = max(limit * 2, 10)
        merged: list[dict] = []
        seen: set[str] = set()
        for language in ("schinese", "english"):
            for item in self._filter_steam_items(
                await self._steam_storesearch(query, language, fetch_limit),
                query,
                fetch_limit,
                keep_localized=True,
            ):
                appid = str(item.get("id") or "")
                if not appid or appid in seen:
                    continue
                seen.add(appid)
                merged.append(item)
        if merged:
            ranked = list(enumerate(merged))
            ranked.sort(key=lambda pair: self._steam_item_sort_key(pair[1], query, pair[0]))
            return [item for _, item in ranked[:limit]]
        for language in ("schinese", "english"):
            items = self._filter_steam_items(
                await self._steam_search_html(query, language, fetch_limit), query, limit
            )
            if items:
                return items
        return []

    @staticmethod
    def _steam_only_id(appid: str) -> str:
        """临时身份只表示 Steam-only，不能当作真实 ITAD ID。"""
        return f"steam:{appid}"

    @classmethod
    def _match_itad_game(cls, steam_title: str, candidates: list[ITADGame]) -> Optional[ITADGame]:
        """只接受归一化后完全相同的标题，避免模糊命中绑错 ITAD ID。"""
        normalized_title = cls._normalize_title(steam_title)
        if not normalized_title:
            return None
        return next(
            (candidate for candidate in candidates
             if candidate.id and not str(candidate.id).startswith("steam:")
             and cls._normalize_title(candidate.title) == normalized_title),
            None,
        )

    def _bind_steam_appid(self, game: ITADGame, appid: str, image: str = "") -> ITADGame:
        game.appid = str(appid or "")
        if image and not game.image:
            game.image = image
        return game

    def _deduplicate_games(self, games: list[ITADGame]) -> list[ITADGame]:
        result = []
        seen: set[str] = set()
        for game in games:
            key = game.id or self._steam_only_id(game.appid)
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(game)
        return result

    async def _associate_steam_item(self, item: dict, query: str) -> Optional[ITADGame]:
        """关联 Steam 候选和 ITAD 搜索结果；无精确映射时保留 steam:<appid>。"""
        appid = str(item.get("id") or "")
        if not appid:
            return None
        english_title = await self._steam_english_title(appid)
        local_title = str(item.get("name") or "").strip()
        search_title = english_title or local_title
        if not search_title:
            return None
        if not (
            self._title_matches_query(english_title or search_title, query)
            or self._title_matches_query(local_title, query)
            or (
                not english_title
                and self._keep_localized_steam_item(item, local_title, query)
            )
        ):
            return None
        itad_games = await self._parse_search_payload(
            await self._get("/games/search/v1", {"title": search_title, "results": 3}), 3
        )
        game = self._match_itad_game(search_title, itad_games)
        if game is None:
            game = ITADGame(self._steam_only_id(appid), search_title)
        game.title = english_title or game.title
        return self._bind_steam_appid(game, appid, item.get("tiny_image", ""))

    async def _bind_itad_fallback_appids(self, games: list[ITADGame]) -> list[ITADGame]:
        for game in games:
            if game.appid:
                continue
            candidates = self._filter_steam_items(
                await self._steam_search(game.title, "english", 3), game.title, 3
            )
            if candidates:
                self._bind_steam_appid(
                    game,
                    str(candidates[0].get("id") or ""),
                    candidates[0].get("tiny_image", ""),
                )
        return games

    def _rank_games(self, games: list[ITADGame], query: str, limit: int) -> list[ITADGame]:
        ranked = list(enumerate(self._deduplicate_games(games)))
        ranked.sort(key=lambda pair: self._game_sort_key(pair[1], query, pair[0]))
        return [game for _, game in ranked[:limit]]

    async def search_games(self, query: str, limit: int = 6) -> list[ITADGame]:
        """先通过 Steam 商店解析本地化名称，再用英文标题查询 ITAD。"""
        steam_items = await self._lookup_steam_items(query, max(limit * 2, 10))

        # Steam 中文索引可能暂时没有结果；保留 ITAD 直搜作为兜底，避免中文查询完全失败。
        if not steam_items:
            fallback = await self._parse_search_payload(
                await self._get("/games/search/v1", {"title": query, "results": limit}), limit
            )
            matched = [game for game in fallback if self._title_matches_query(game.title, query)]
            await self._bind_itad_fallback_appids(matched)
            return self._rank_games(matched, query, limit)

        associated = []
        for item in steam_items:
            game = await self._associate_steam_item(item, query)
            if game is not None:
                associated.append(game)
        return self._rank_games(associated, query, limit)

    async def lookup_steam_appid(self, appid: str) -> Optional[ITADGame]:
        """按 Steam appid 直接查 ITAD 游戏（用于商店链接查询）。"""
        payload = await self._get("/games/lookup/v1", {"appid": str(appid)})
        game = (payload or {}).get("game") if isinstance(payload, dict) else None
        if isinstance(game, dict) and game.get("id"):
            itad = ITADGame(str(game.get("id")), str(game.get("title") or ""))
            itad.appid = str(appid)
            itad.image = ""
            return itad
        return None

    async def get_prices(self, game_id: str, country: str = "CN", timeout=None) -> dict[str, Any]:
        game_id = str(game_id or "")
        if not game_id or game_id.startswith("steam:"):
            logger.warning("拒绝把 Steam-only 身份当作 ITAD ID 查询价格: %s", game_id)
            return {}
        payload = await self._post("/games/prices/v3", [game_id], {"country": country}, timeout=timeout)
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("id") == game_id:
                    return item
        return {}

    @staticmethod
    def _price_amount(price_obj) -> Optional[float]:
        if not isinstance(price_obj, dict):
            return None
        try:
            return float(price_obj.get("amount"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_steam_shop(shop) -> bool:
        if not isinstance(shop, dict):
            return False
        if shop.get("id") == 61:
            return True
        return str(shop.get("name") or "").strip().lower() == "steam"

    async def get_price_summary(self, game_id: str, country: str = "CN", timeout=None) -> dict[str, Any]:
        current = await self.get_prices(game_id, country, timeout=timeout)
        current_price = None
        current_regular = None
        currency = None
        cut = None
        cdk_shop = None
        cdk_amount = None
        cdk_currency = None
        cdk_cut = None
        fallback_deal = None
        steam_low = None
        steam_low_cut = None
        for deal in current.get("deals", []) if isinstance(current, dict) else []:
            if not isinstance(deal, dict):
                continue
            price = deal.get("price") or {}
            regular = deal.get("regular") or {}
            amount = self._price_amount(price)
            if amount is None:
                continue
            shop = deal.get("shop") or {}
            if fallback_deal is None:
                fallback_deal = (
                    amount,
                    float(regular.get("amount") or amount),
                    price.get("currency"),
                    deal.get("cut"),
                )
            if self._is_steam_shop(shop):
                # 当前价/折扣只取 Steam 店铺当前在售价，避免把第三方折扣当成 Steam 折扣
                current_price = amount
                current_regular = float(regular.get("amount") or current_price)
                currency = price.get("currency")
                cut = deal.get("cut")
                # Steam 史低用 prices/v3 的 storeLow（全时段店铺最低）。
                # history/v2 默认只覆盖约 3 个月，近期无折扣时会把现价当成史低。
                store_low = self._price_amount(deal.get("storeLow"))
                if store_low is not None:
                    steam_low = store_low
                    regular_amount = self._price_amount(regular)
                    if regular_amount and regular_amount > 0:
                        steam_low_cut = int(round((1 - store_low / regular_amount) * 100))
                    else:
                        steam_low_cut = deal.get("cut") if store_low < amount else 0
            elif cdk_amount is None or amount < cdk_amount:
                cdk_amount = amount
                cdk_shop = shop.get("name")
                cdk_currency = price.get("currency")
                cdk_cut = deal.get("cut")
        if current_price is None and fallback_deal is not None:
            current_price, current_regular, currency, cut = fallback_deal
        history_low = None
        history_low_currency = None
        low_obj = current.get("historyLow") if isinstance(current, dict) else None
        if isinstance(low_obj, dict):
            low_all = low_obj.get("all") or {}
            history_low = self._price_amount(low_all)
            history_low_currency = str(low_all.get("currency") or "").upper() or None
            if not currency:
                currency = history_low_currency
        return {
            "current": current,
            "current_price": current_price,
            "current_regular": current_regular,
            "currency": currency,
            "cut": cut,
            "cdk_shop": cdk_shop,
            "cdk_amount": cdk_amount,
            "cdk_currency": cdk_currency,
            "cdk_cut": cdk_cut,
            "history_low": history_low,
            "history_low_currency": history_low_currency,
            "lowest": history_low,
            "lowest_currency": history_low_currency,
            "lowest_cut": None,
            "steam_low": steam_low,
            "steam_low_currency": currency if steam_low is not None else None,
            "steam_low_cut": steam_low_cut,
            "history": [],
        }
