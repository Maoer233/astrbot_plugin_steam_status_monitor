"""IsThereAnyDeal 客户端：游戏搜索、当前价格与历史最低价。"""
from dataclasses import dataclass
from html import unescape
from typing import Any
import re

import httpx

try:
    from ...shared.logging import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
try:
    from ...shared.network import httpx_client_kwargs
except ImportError:
    def httpx_client_kwargs(proxy=None):
        return {'proxy': proxy} if proxy else {}


@dataclass
class ITADGame:
    id: str
    title: str
    url: str = ""
    slug: str = ""
    image: str = ""
    appid: str = ""


class ITADClient:
    BASE_URL = "https://api.isthereanydeal.com"

    def __init__(self, api_key: str = "", proxy=None, base_url: str = ""):
        self.api_key = (api_key or "").strip()
        self.proxy = proxy
        self.base_url = (base_url or self.BASE_URL).rstrip("/")

    async def _get(self, path: str, params: dict[str, Any]):
        if not self.api_key:
            return None
        params = {**params, "key": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, **httpx_client_kwargs(self.proxy)) as client:
                response = await client.get(f"{self.base_url}{path}", params=params)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            logger.warning("ITAD 请求失败 %s: %s", path, exc)
            return None

    async def _post(self, path: str, body, params: dict[str, Any]):
        if not self.api_key:
            return None
        params = {**params, "key": self.api_key}
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, **httpx_client_kwargs(self.proxy)) as client:
                response = await client.post(f"{self.base_url}{path}", json=body, params=params)
                response.raise_for_status()
                return response.json()
        except Exception as exc:
            logger.warning("ITAD 请求失败 %s: %s", path, exc)
            return None

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
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, **httpx_client_kwargs(self.proxy)) as client:
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
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, **httpx_client_kwargs(self.proxy)) as client:
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
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, **httpx_client_kwargs(self.proxy)) as client:
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

    def _filter_steam_items(self, items, query: str, limit: int) -> list[dict]:
        result = []
        seen: set[str] = set()
        for item in items or []:
            if not isinstance(item, dict):
                continue
            appid = str(item.get("id") or "")
            title = str(item.get("name") or "").strip()
            if not appid or appid in seen or not title:
                continue
            if not self._title_matches_query(title, query):
                continue
            seen.add(appid)
            result.append(item)
            if len(result) >= limit:
                break
        return result

    async def _lookup_steam_items(self, query: str, limit: int) -> list[dict]:
        """优先 storesearch；HTML 页只在过滤后仍有相关标题时才采用。"""
        for language in ("schinese", "english"):
            items = self._filter_steam_items(
                await self._steam_storesearch(query, language, limit), query, limit
            )
            if items:
                return items
        for language in ("schinese", "english"):
            items = self._filter_steam_items(
                await self._steam_search_html(query, language, limit), query, limit
            )
            if items:
                return items
        return []

    async def search_games(self, query: str, limit: int = 6) -> list[ITADGame]:
        """先通过 Steam 商店解析本地化名称，再用英文标题查询 ITAD。"""
        steam_items = await self._lookup_steam_items(query, limit)

        # Steam 中文索引可能暂时没有结果；保留 ITAD 直搜作为兜底，避免中文查询完全失败。
        if not steam_items:
            fallback = await self._parse_search_payload(
                await self._get("/games/search/v1", {"title": query, "results": limit}), limit
            )
            matched = [game for game in fallback if self._title_matches_query(game.title, query)]
            for game in matched:
                candidates = self._filter_steam_items(
                    await self._steam_search(game.title, "english", 3), game.title, 3
                )
                if candidates:
                    game.appid = str(candidates[0].get("id") or "")
                    game.image = game.image or candidates[0].get("tiny_image", "")
            return matched[:limit]

        result: list[ITADGame] = []
        seen_keys: set[str] = set()
        for item in steam_items:
            appid = str(item.get("id") or "")
            english_title = await self._steam_english_title(appid)
            local_title = str(item.get("name") or "").strip()
            search_title = english_title or local_title
            if not search_title:
                continue
            if english_title and not (
                self._title_matches_query(english_title, query)
                or self._title_matches_query(local_title, query)
            ):
                continue
            itad_games = await self._parse_search_payload(
                await self._get("/games/search/v1", {"title": search_title, "results": 3}), 3
            )
            normalized_title = search_title.casefold()
            game = next(
                (candidate for candidate in itad_games
                 if candidate.title.casefold().strip() == normalized_title),
                None,
            )
            if game is None:
                game = ITADGame(f"steam:{appid}", search_title)
            dedupe_key = game.id or f"steam:{appid}"
            if dedupe_key in seen_keys:
                continue
            game.appid = appid
            game.title = english_title or game.title
            game.image = game.image or item.get("tiny_image", "")
            seen_keys.add(dedupe_key)
            result.append(game)
            if len(result) >= limit:
                break
        return result

    async def get_prices(self, game_id: str, country: str = "CN") -> dict[str, Any]:
        payload = await self._post("/games/prices/v3", [game_id], {"country": country})
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict) and item.get("id") == game_id:
                    return item
        return {}

    async def get_history(self, game_id: str, country: str = "CN") -> list[dict[str, Any]]:
        payload = await self._get("/games/history/v2", {"id": game_id, "country": country})
        if isinstance(payload, list):
            return payload
        return []

    async def get_price_summary(self, game_id: str, country: str = "CN") -> dict[str, Any]:
        import asyncio

        current, history = await asyncio.gather(
            self.get_prices(game_id, country), self.get_history(game_id, country)
        )
        current_price = None
        current_regular = None
        currency = None
        cut = None
        for deal in current.get("deals", []) if isinstance(current, dict) else []:
            if not isinstance(deal, dict):
                continue
            price = deal.get("price") or {}
            regular = deal.get("regular") or {}
            amount = price.get("amount")
            if amount is None:
                continue
            current_price = float(amount)
            current_regular = float(regular.get("amount") or current_price)
            currency = price.get("currency")
            cut = deal.get("cut")
            break
        history_low = None
        low_obj = current.get("historyLow") if isinstance(current, dict) else None
        if isinstance(low_obj, dict):
            low_all = low_obj.get("all") or {}
            try:
                history_low = float(low_all.get("amount"))
            except (TypeError, ValueError):
                history_low = None
            if not currency:
                currency = low_all.get("currency")
        lowest = None
        for item in history:
            if not isinstance(item, dict):
                continue
            deal = item.get("deal") or {}
            price = deal.get("price") or {}
            amount = price.get("amount")
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                continue
            lowest = amount if lowest is None else min(lowest, amount)
        return {
            "current": current,
            "current_price": current_price,
            "current_regular": current_regular,
            "currency": currency,
            "cut": cut,
            "history_low": history_low,
            "lowest": lowest,
            "history": history,
        }
