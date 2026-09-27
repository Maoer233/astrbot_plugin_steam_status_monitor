import asyncio
import json
import os
import time

import httpx

from ...infrastructure.clients.errors import ProviderError
from ...shared.logging import format_exception, logger
from ...shared.network import httpx_client_kwargs
from ...shared.utils.price import store_region_candidates

# 成人内容/年龄墙：无 Cookie 时 appdetails 与 appreviews 常返回 success=false。
STEAM_STORE_COOKIES = {
    "birthtime": "0",
    "lastagecheckage": "1-0-1970",
    "mature_content": "1",
    "wants_mature_content": "1",
}
STEAM_STORE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def steam_store_client_kwargs(proxy=None):
    """商店接口共用代理、年龄墙 Cookie 与浏览器头。"""
    return {
        "cookies": STEAM_STORE_COOKIES,
        "headers": STEAM_STORE_HEADERS,
        **httpx_client_kwargs(proxy),
    }


async def _get_steam_store_client(owner):
    """返回当前实例的 Store client；未初始化或跨 loop 时使用临时 client。"""
    client = getattr(owner, "_steam_store_http_client", None)
    if client is not None:
        try:
            if getattr(owner, "_steam_store_http_client_loop", None) is asyncio.get_running_loop():
                return client, False
        except RuntimeError:
            pass
    return httpx.AsyncClient(
        timeout=15,
        **steam_store_client_kwargs(getattr(owner, "proxy", None)),
    ), True


async def initialize_steam_store_client(owner):
    """在当前事件循环创建 Steam Store 连接池。"""
    loop = asyncio.get_running_loop()
    current = getattr(owner, "_steam_store_http_client", None)
    if current is not None and getattr(owner, "_steam_store_http_client_loop", None) is loop:
        return current
    await close_steam_store_client(owner)
    owner._steam_store_http_client = httpx.AsyncClient(
        timeout=15,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        **steam_store_client_kwargs(owner.proxy),
    )
    owner._steam_store_http_client_loop = loop
    return owner._steam_store_http_client


async def close_steam_store_client(owner):
    """关闭当前实例的 Steam Store 连接池。"""
    client = getattr(owner, "_steam_store_http_client", None)
    owner._steam_store_http_client = None
    owner._steam_store_http_client_loop = None
    if client is not None:
        await client.aclose()


class SteamClientError(RuntimeError):
    """Steam 客户端调用失败。"""


def continue_store_region_fallback(error: ProviderError) -> bool:
    """锁区、无商品和可重试的网络失败继续换区；协议或鉴权错误不再消耗预算。"""
    return error.code in {"REGION_LOCKED", "NOT_FOUND", "TIMEOUT", "UPSTREAM_ERROR"}


def _region_attempt_timeout(deadline):
    """单次回退不能用满客户端默认超时，否则五个区会吃光查询预算。"""
    if deadline is None:
        return None
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError("Steam 商店区域回退预算已用尽")
    return remaining


def classify_steam_store_error(exc) -> ProviderError:
    """把 Steam 商店请求异常分成可诊断状态，不包含锁区或无商品。"""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError("TIMEOUT", "Steam 商店请求超时", retryable=True)
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return ProviderError("RATE_LIMITED", "Steam 商店请求受限", status_code=status, retryable=True)
        if status in (401, 403):
            return ProviderError("AUTH_ERROR", "Steam 商店拒绝访问", status_code=status)
        if status == 404:
            return ProviderError("NOT_FOUND", "Steam 商店未找到商品", status_code=status)
        return ProviderError(
            "UPSTREAM_ERROR",
            "Steam 商店上游失败",
            status_code=status,
            retryable=status >= 500,
        )
    if isinstance(exc, (json.JSONDecodeError, ValueError)):
        return ProviderError("INVALID_RESPONSE", "Steam 商店响应无法解析")
    if isinstance(exc, httpx.RequestError):
        return ProviderError("UPSTREAM_ERROR", "Steam 商店网络失败", retryable=True)
    return ProviderError("UPSTREAM_ERROR", "Steam 商店请求失败", retryable=True)


class SteamClientMixin:
    def _remember_store_error(self, error: ProviderError):
        self.last_store_error = error
        return error

    def _clear_store_error(self):
        self.last_store_error = None

    async def fetch_player_summary(self, steam_id):
        """获取 Steam 玩家原始摘要，由应用层负责字段本地化与展示。"""
        if not self.API_KEY:
            raise SteamClientError("未配置 Steam API Key")
        url = (
            f"{self.STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/"
            f"?key={self.API_KEY}&steamids={steam_id}"
        )
        try:
            async with httpx.AsyncClient(
                timeout=15,
                **httpx_client_kwargs(self.proxy),
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                players = response.json().get("response", {}).get("players", [])
                return players[0] if players else None
        except Exception as exc:
            logger.warning(f"获取 Steam 玩家摘要失败: {format_exception(exc)} (SteamID: {steam_id})")
            raise SteamClientError(f"Steam API 请求失败: {format_exception(exc)}") from exc

    async def fetch_player_status(self, steam_id, retry=None):
        '''拉取单个玩家的 Steam 状态，失败自动重试多次并指数退避'''
        url = (
            f"{self.STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/"
            f"?key={self.API_KEY}&steamids={steam_id}"
        )
        delay = 1
        retry = retry if retry is not None else self.RETRY_TIMES
        for attempt in range(retry):
            try:
                async with httpx.AsyncClient(timeout=15, **httpx_client_kwargs(self.proxy)) as client:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        raise Exception(f"HTTP {resp.status_code}")
                    try:
                        data = resp.json()
                    except Exception as je:
                        raise Exception(f"JSON解析失败: {je}")
                    resp_data = data.get('response')
                    if not isinstance(resp_data, dict):
                        raise Exception(f"Steam 返回异常响应（类型={type(resp_data).__name__}，值={resp_data}），疑似 API Key 无效或触发限流")
                    if not resp_data.get('players'):
                        raise Exception("响应中无玩家数据")
                    player = data['response'].get('players')[0]
                    # 返回更多字段，包括头像
                    return {
                        'name': player.get('personaname'),
                        'gameid': player.get('gameid'),
                        'lastlogoff': player.get('lastlogoff'),
                        'gameextrainfo': player.get('gameextrainfo'),
                        'personastate': player.get('personastate', 0),
                        'avatarfull': player.get('avatarfull'),
                        'avatar': player.get('avatar')
                    }
            except Exception as e:
                logger.warning(f"拉取 Steam 状态失败: {format_exception(e)} (SteamID: {steam_id}, 第{attempt+1}次重试)")
                if attempt < retry - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
        logger.error(f"SteamID {steam_id} 状态获取失败，已重试{retry}次")
        return None

    async def fetch_player_statuses_batch(self, steam_ids, retry=None):
        '''批量拉取多个玩家的 Steam 状态（单次请求最多 100 个 ID）。
        返回 {steamid: status_dict}，缺失或失败的 sid 不在返回字典中。
        Steam GetPlayerSummaries/v2 支持逗号分隔的 steamids，一次最多 100 个，
        相比逐个请求可大幅降低 API 调用次数，避免触发 Steam 限流（429 / x-eresult:84）。
        '''
        if not steam_ids or not self.API_KEY:
            return {}
        result = {}
        retry = retry if retry is not None else self.RETRY_TIMES
        # 分片：每 100 个 ID 一批
        BATCH_SIZE = 100
        id_batches = [steam_ids[i:i+BATCH_SIZE] for i in range(0, len(steam_ids), BATCH_SIZE)]
        for batch in id_batches:
            ids_str = ",".join(batch)
            url = (
                f"{self.STEAM_API_BASE}/ISteamUser/GetPlayerSummaries/v2/"
                f"?key={self.API_KEY}&steamids={ids_str}"
            )
            delay = 1
            for attempt in range(retry):
                try:
                    async with httpx.AsyncClient(timeout=15, **httpx_client_kwargs(self.proxy)) as client:
                        resp = await client.get(url)
                        if resp.status_code != 200:
                            raise Exception(f"HTTP {resp.status_code}")
                        data = resp.json()
                        resp_data = data.get('response')
                        if not isinstance(resp_data, dict):
                            logger.warning(f"[批量查询] Steam 返回异常响应（类型={type(resp_data).__name__}，值={resp_data}），疑似 API Key 无效或触发限流，本批降级处理")
                            resp_data = {}
                        players = resp_data.get('players') or []
                        for player in players:
                            sid = player.get('steamid')
                            if sid and sid in batch:
                                result[sid] = {
                                    'name': player.get('personaname'),
                                    'gameid': player.get('gameid'),
                                    'lastlogoff': player.get('lastlogoff'),
                                    'gameextrainfo': player.get('gameextrainfo'),
                                    'personastate': player.get('personastate', 0),
                                    'avatarfull': player.get('avatarfull'),
                                    'avatar': player.get('avatar')
                                }
                        # 成功处理本批，跳出重试
                        missing = [s for s in batch if s not in result]
                        if missing:
                            logger.warning(f"[批量查询] 以下 SteamID 在响应中缺失（可能无效/隐私）: {missing}")
                        break
                except Exception as e:
                    logger.warning(f"[批量查询] 失败: {format_exception(e)} (本批 {len(batch)} 个 ID, 第{attempt+1}次重试)")
                    if attempt < retry - 1:
                        await asyncio.sleep(delay)
                        delay *= 2
                    else:
                        logger.error(
                            f"[批量查询] 本批彻底失败，跳过本轮（不再串行单查，避免把主循环拖死）: "
                            f"{len(batch)} 个 ID"
                        )
        return result

    async def resolve_steam_input(self, raw):
        '''将多种格式的 Steam 输入统一解析为 17 位 SteamID64。
        支持：
        - 17 位纯数字 SteamID64
        - https://steamcommunity.com/profiles/<steamid64>
        - https://steamcommunity.com/id/<vanity>  （自定义 ID，调 ResolveVanityURL）
        - https://s.team/p/<steamid64> 或 s.team/p/<steamid64>
        - 8 位好友码（SteamID32 + 76561197960265728 = SteamID64）
        返回 SteamID64 字符串；解析失败返回 None。
        '''
        if not raw or not isinstance(raw, str):
            return None
        s = raw.strip()
        # 1) 纯 17 位数字
        if s.isdigit() and len(s) == 17:
            return s
        # 2) URL：提取路径段
        lowered = s.lower()
        if 'steamcommunity.com' in lowered or 's.team/p/' in lowered:
            # 去掉 query 和 fragment
            path = s.split('?')[0].split('#')[0].rstrip('/')
            segments = path.split('/')
            # 例: https://steamcommunity.com/profiles/76561198xxx
            #     https://steamcommunity.com/id/customname
            #     https://s.team/p/76561198xxx
            if len(segments) >= 2:
                last = segments[-1]
                last2 = segments[-2] if len(segments) >= 2 else ''
                if last2 == 'profiles' and last.isdigit() and len(last) == 17:
                    return last
                if last2 == 'id' and last:
                    # 自定义 vanity URL，需调用 API 解析
                    return await self._resolve_vanity_url(last)
                # s.team/p/<id>
                if 's.team' in lowered and last.isdigit() and len(last) == 17:
                    return last
        # 3) 8 位好友码（SteamID32）
        if s.isdigit() and len(s) <= 10:
            try:
                steamid64 = str(int(s) + 76561197960265728)
                if len(steamid64) == 17:
                    return steamid64
            except Exception:
                pass
        return None

    async def _resolve_vanity_url(self, vanity):
        '''调用 Steam ResolveVanityURL 接口把自定义 ID 转成 SteamID64'''
        if not self.API_KEY or not vanity:
            return None
        url = (
            f"{self.STEAM_API_BASE}/ISteamUser/ResolveVanityURL/v1/"
            f"?key={self.API_KEY}&vanityurl={vanity}"
        )
        try:
            async with httpx.AsyncClient(timeout=15, **httpx_client_kwargs(self.proxy)) as client:
                resp = await client.get(url)
                if resp.status_code != 200:
                    logger.warning(f"[vanity解析] HTTP {resp.status_code} (vanity={vanity})")
                    return None
                data = resp.json()
                resp_data = data.get('response')
                if not isinstance(resp_data, dict):
                    resp_data = {}
                success = resp_data.get('success', 0)
                steamid = resp_data.get('steamid')
                if success == 1 and steamid:
                    return steamid
                logger.warning(f"[vanity解析] 失败 success={success} (vanity={vanity})")
                return None
        except Exception as e:
            logger.warning(f"[vanity解析] 异常: {e} (vanity={vanity})")
            return None

    async def _review_summary(self, appid, language="all"):
        """获取 Steam 商店评价摘要。language 传 'all' 表示所有语言（缺省），否则为指定语言。
        返回 {"text","percent","total"} 或 None。"""
        gid = str(appid).strip()
        if not gid.isdigit():
            return None
        url = f"{self.STEAM_STORE_BASE}/appreviews/{gid}"
        params = {"json": 1, "filter": "summary"}
        language = language or "all"
        params["language"] = language
        client, owned = await _get_steam_store_client(self)
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            payload = response.json()
            summary = payload.get("query_summary") or payload.get("querySummary") or {}
            total = int(summary.get("total_reviews") or summary.get("totalReviews") or 0)
            positive = int(summary.get("total_positive") or summary.get("totalPositive") or 0)
            if total <= 0:
                return {"text": "暂无评价", "percent": None, "total": 0}
            percent = round(positive * 100 / total)
            if percent >= 95:
                label = "好评如潮"
            elif percent >= 80:
                label = "特别好评"
            elif percent >= 70:
                label = "多半好评"
            elif percent >= 40:
                label = "褒贬不一"
            elif percent >= 20:
                label = "多半差评"
            else:
                label = "差评"
            return {"text": label, "percent": percent, "total": total}
        except Exception as exc:
            error = self._remember_store_error(classify_steam_store_error(exc))
            logger.warning(
                "获取 Steam 评价摘要失败 [%s]: %s (appid=%s, language=%s)",
                error.code,
                exc,
                gid,
                language,
            )
            return None
        finally:
            if owned:
                await client.aclose()

    async def fetch_game_reviews_both(self, appid):
        """同时获取「全部语言」与「简体中文」两份评价摘要，供卡片并列显示。"""
        all_review, zh_review = await asyncio.gather(
            self._review_summary(appid, None),
            self._review_summary(appid, "schinese"),
        )
        return {"all": all_review, "schinese": zh_review}

    async def _request_appdetails(self, client, gid, language=None, country=None, timeout=None):
        """请求 appdetails。

        锁区返回 REGION_LOCKED，响应里没有该商品返回 NOT_FOUND。
        网络、HTTP 和 JSON 错误抛出 ProviderError，供区域回退继续尝试。
        """
        # appids 必须放进 params。httpx 的 params 会整段替换 URL 查询串，
        # 写在 ?appids= 上会被 cc/l 覆盖，商店接口直接 400。
        params = {"appids": gid}
        if language:
            params["l"] = language
        if country:
            params["cc"] = str(country).lower()
        url = f"{self.STEAM_STORE_BASE}/api/appdetails"
        try:
            response = await client.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise classify_steam_store_error(exc) from exc
        item = self._appdetails_item(payload, gid)
        if item is None:
            raise ProviderError("NOT_FOUND", "Steam 商店未找到商品")
        if not item.get("success"):
            raise ProviderError("REGION_LOCKED", "Steam 商店当前区域不可用")
        data = item.get("data")
        if not isinstance(data, dict):
            raise ProviderError("NOT_FOUND", "Steam 商店未返回商品详情")
        return data

    @staticmethod
    def _appdetails_item(payload, gid):
        """取出本次请求对应的商店详情。

        Steam 有时把响应挂在另一个商品 ID 上，同时在 data.steam_appid
        保留请求的 AppID。只按请求键查找会把有价格的详情误判成未找到。
        """
        if not isinstance(payload, dict) or not payload:
            return None
        direct = payload.get(str(gid))
        if isinstance(direct, dict):
            return direct
        requested = str(gid)
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            data = item.get("data")
            if isinstance(data, dict) and str(data.get("steam_appid") or "") == requested:
                return item
        if len(payload) == 1:
            item = next(iter(payload.values()))
            if isinstance(item, dict):
                return item
        return None

    async def fetch_game_details(self, appid, language="schinese", country="CN", deadline=None):
        """获取 Steam 商店游戏详情。主区锁区时按港/台/日/美回退；简体失败再试英文。"""
        gid = str(appid).strip()
        if not gid.isdigit():
            return None
        preferred = str(country or "CN").strip().upper() or "CN"
        languages = []
        if language:
            languages.append(language)
        if language not in ("english", "en"):
            languages.append("english")
        client, owned = await _get_steam_store_client(self)
        errors = []
        self._clear_store_error()
        try:
            for lang in languages:
                for cc in store_region_candidates(preferred):
                    try:
                        attempt_timeout = _region_attempt_timeout(deadline)
                    except TimeoutError:
                        self._remember_store_error(
                            ProviderError("TIMEOUT", "Steam 商店区域回退预算已用尽", retryable=True)
                        )
                        return None
                    try:
                        data = await self._request_appdetails(
                            client,
                            gid,
                            language=lang,
                            country=cc,
                            timeout=attempt_timeout,
                        )
                    except ProviderError as exc:
                        errors.append({"region": cc, "language": lang, "code": exc.code})
                        self._remember_store_error(exc)
                        logger.warning(
                            "获取 Steam %s 区详情失败 [%s]: %s (appid=%s, lang=%s)",
                            cc,
                            exc.code,
                            exc,
                            gid,
                            lang,
                        )
                        if not continue_store_region_fallback(exc):
                            return None
                        continue
                    data["_store_region"] = cc
                    data["_store_language"] = lang
                    data["_requested_region"] = preferred
                    data["_store_fallback"] = cc != preferred
                    data["_store_status"] = "REGION_LOCKED" if cc != preferred else "SUCCESS"
                    data["_store_errors"] = errors
                    if cc != preferred:
                        logger.info(
                            "Steam %s 区锁区或无详情，改用 %s 区 (appid=%s)",
                            preferred,
                            cc,
                            gid,
                        )
                    self._clear_store_error()
                    return data
            if errors:
                logger.warning(
                    "获取 Steam 游戏详情失败 [%s] (appid=%s)",
                    errors[-1]["code"],
                    gid,
                )
            else:
                logger.info("Steam 各区均无商店详情 (appid=%s, preferred=%s)", gid, preferred)
            return None
        finally:
            if owned:
                await client.aclose()

    async def fetch_region_price(self, appid, country="CN", deadline=None):
        """获取指定国家区 Steam 商店价格（含币种、折后价/原价/折扣）。
        主区锁区或无价时回退未锁区，返回 dict 的 region 为实际命中区。"""
        gid = str(appid).strip()
        if not gid.isdigit():
            return None
        preferred = str(country or "CN").strip().upper() or "CN"
        client, owned = await _get_steam_store_client(self)
        errors = []
        saw_product = False
        self._clear_store_error()
        try:
            for cc in store_region_candidates(preferred):
                try:
                    attempt_timeout = _region_attempt_timeout(deadline)
                except TimeoutError:
                    self._remember_store_error(
                        ProviderError("TIMEOUT", "Steam 商店区域回退预算已用尽", retryable=True)
                    )
                    return None
                try:
                    data = await self._request_appdetails(
                        client, gid, country=cc, timeout=attempt_timeout
                    )
                except ProviderError as exc:
                    errors.append({"region": cc, "code": exc.code})
                    self._remember_store_error(exc)
                    logger.warning(
                        "获取 Steam %s 区价格失败 [%s]: %s (appid=%s)",
                        cc,
                        exc.code,
                        exc,
                        gid,
                    )
                    if not continue_store_region_fallback(exc):
                        return None
                    continue
                saw_product = True
                price_overview = data.get("price_overview") or {}
                if not price_overview:
                    no_price = ProviderError("NO_PRICE", "Steam 商店当前区域无价格")
                    errors.append({"region": cc, "code": no_price.code})
                    self._remember_store_error(no_price)
                    continue
                if cc != preferred:
                    logger.info(
                        "Steam %s 区无价格，改用 %s 区 (appid=%s)",
                        preferred,
                        cc,
                        gid,
                    )
                self._clear_store_error()
                return {
                    "currency": price_overview.get("currency"),
                    "current_price": price_overview.get("final", 0) / 100,
                    "current_regular": price_overview.get("initial", 0) / 100,
                    "cut": price_overview.get("discount_percent", 0),
                    "region": cc,
                    "requested_region": preferred,
                    "is_fallback": cc != preferred,
                    "status": "REGION_LOCKED" if cc != preferred else "SUCCESS",
                    "errors": errors,
                }
            if saw_product and any(item["code"] == "NO_PRICE" for item in errors):
                self._remember_store_error(ProviderError("NO_PRICE", "Steam 商店各区均无价格"))
            return None
        finally:
            if owned:
                await client.aclose()

    async def get_chinese_game_name(self, gameid, fallback_name=None):
        '''
        优先通过 Steam 商店API获取游戏中文名（l=schinese），若无则返回英文名（l=en），最后才返回 fallback_name 或“未知游戏”
        '''
        if not gameid:
            return fallback_name or "未知游戏"
        gid = str(gameid)
        if gid in self._game_name_cache:
            cached = self._game_name_cache[gid]
            # get_game_names 会缓存 (name_zh, name_en) 元组，需取中文名
            if isinstance(cached, tuple):
                return cached[0] if cached[0] else (cached[1] if len(cached) > 1 else "未知游戏")
            return cached
        # 优先查中文名（l=schinese），再查英文名（l=en）
        url_zh = f"{self.STEAM_STORE_BASE}/api/appdetails?appids={gid}&l=schinese"
        url_en = f"{self.STEAM_STORE_BASE}/api/appdetails?appids={gid}&l=en"
        try:
            async with httpx.AsyncClient(timeout=10, proxy=self.proxy) as client:
                # 查中文名
                resp_zh = await client.get(url_zh)
                data_zh = resp_zh.json()
                info_zh = data_zh.get(gid, {}).get("data", {})
                name_zh = info_zh.get("name")
                if name_zh:
                    self._game_name_cache[gid] = name_zh
                    return name_zh
                # 查英文名
                resp_en = await client.get(url_en)
                data_en = resp_en.json()
                info_en = data_en.get(gid, {}).get("data", {})
                name_en = info_en.get("name")
                if name_en:
                    self._game_name_cache[gid] = name_en
                    return name_en
        except Exception as e:
            logger.warning(f"获取游戏名失败: {e} (gameid={gid})")
        # 不缓存 fallback，让下次还能重试
        return fallback_name or "未知游戏"

    async def get_game_online_count(self, gameid):
        """通过 Steam Web API 获取当前游戏在线人数。"""
        if not gameid:
            return None
        url = f"{self.STEAM_API_BASE}/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid={gameid}"
        try:
            async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(self.proxy)) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("response", {}).get("player_count")
        except Exception as e:
            logger.warning(f"获取在线人数失败: {e} (gameid={gameid})")
        return None

    async def get_game_names(self, gameid, fallback_name=None):
        '''
        返回 (中文名, 英文名)，如无则 fallback_name 或 "未知游戏"
        '''
        if not gameid:
            return (fallback_name or "未知游戏", fallback_name or "未知游戏")
        gid = str(gameid)
        if gid in self._game_name_cache:
            cached = self._game_name_cache[gid]
            if isinstance(cached, tuple):
                return cached
            else:
                return (cached, cached)
        url_zh = f"{self.STEAM_STORE_BASE}/api/appdetails?appids={gid}&l=schinese"
        url_en = f"{self.STEAM_STORE_BASE}/api/appdetails?appids={gid}&l=en"
        name_zh = name_en = fallback_name or "未知游戏"
        try:
            async with httpx.AsyncClient(timeout=10, proxy=self.proxy) as client:
                resp_zh = await client.get(url_zh)
                data_zh = resp_zh.json()
                info_zh = data_zh.get(gid, {}).get("data", {})
                name_zh = info_zh.get("name") or name_zh
                resp_en = await client.get(url_en)
                data_en = resp_en.json()
                info_en = data_en.get(gid, {}).get("data", {})
                name_en = info_en.get("name") or name_en
        except Exception as e:
            logger.warning(f"获取游戏名失败: {e} (gameid={gid})")
        self._game_name_cache[gid] = (name_zh, name_en)
        return (name_zh, name_en)

    async def get_game_cover_url(self, gameid, force_update=False):
        '''
        获取游戏封面图本地路径（优先小图，失败自动尝试日文/英文区域），自动缓存到本地，定期刷新
        force_update: True 时强制重新下载覆盖本地
        '''
        if not gameid:
            return None
        gid = str(gameid)
        cover_dir = os.path.join(self.data_dir, "covers")
        os.makedirs(cover_dir, exist_ok=True)
        cover_path = os.path.join(cover_dir, f"{gid}.jpg")
        # 定期刷新周期（秒），如30天
        refresh_interval = 30 * 24 * 3600
        need_refresh = force_update
        # 判断本地缓存是否需要刷新
        if os.path.exists(cover_path) and not force_update:
            last_mtime = os.path.getmtime(cover_path)
            if time.time() - last_mtime > refresh_interval:
                need_refresh = True
            else:
                return cover_path
        # 先查缓存
        if not need_refresh and hasattr(self, "_game_cover_cache") and gid in self._game_cover_cache:
            return self._game_cover_cache[gid]
        # 多区域尝试
        lang_list = ["schinese", "japanese", "en"]
        try:
            async with httpx.AsyncClient(timeout=10, proxy=self.proxy) as client:
                for lang in lang_list:
                    url = f"{self.STEAM_STORE_BASE}/api/appdetails?appids={gid}&l={lang}"
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        logger.warning(f"获取游戏封面API失败: HTTP {resp.status_code} (gameid={gid}, lang={lang})")
                        continue
                    data = resp.json()
                    info = data.get(gid, {}).get("data", {})
                    header_img = info.get("header_image")
                    if not header_img:
                        logger.info(f"未找到游戏封面字段 header_image (gameid={gid}, lang={lang})，API返回data: {repr(info)[:200]}")
                        continue
                    small_img = header_img.replace("_header.jpg", "_capsule_184x69.jpg")
                    img_resp = await client.get(small_img)
                    if img_resp.status_code == 200:
                        with open(cover_path, "wb") as f:
                            f.write(img_resp.content)
                        return cover_path
                    else:
                        logger.warning(f"封面图片下载失败: HTTP {img_resp.status_code} url={small_img} (gameid={gid}, lang={lang})")
        except Exception as e:
            logger.warning(f"获取/缓存游戏封面异常: {e} (gameid={gid})")
        # 如果下载失败且本地有旧图，兜底返回旧图
        if os.path.exists(cover_path):
            return cover_path
        return None
