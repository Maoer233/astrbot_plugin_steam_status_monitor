import asyncio
import os
import time

import httpx

from ...shared.logging import format_exception, logger
from ...shared.network import httpx_client_kwargs


class SteamClientError(RuntimeError):
    """Steam 客户端调用失败。"""


class SteamClientMixin:
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
                        logger.error(f"[批量查询] 本批彻底失败，降级为单查: {batch}")
                        # 降级：批量失败时回退到逐个查询，保证可用性
                        for sid in batch:
                            if sid not in result:
                                try:
                                    single = await self.fetch_player_status(sid, retry=1)
                                    if single:
                                        result[sid] = single
                                except Exception as se:
                                    logger.warning(f'[批量查询] 单查降级也失败 (SteamID={sid}): {format_exception(se)}')
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

    async def fetch_game_reviews(self, appid, language="schinese"):
        """获取 Steam 商店评价摘要。"""
        gid = str(appid).strip()
        if not gid.isdigit():
            return None
        url = f"{self.STEAM_STORE_BASE}/appreviews/{gid}"
        try:
            async with httpx.AsyncClient(timeout=15, **httpx_client_kwargs(self.proxy)) as client:
                response = await client.get(url, params={"json": 1, "language": language, "filter": "summary"})
                response.raise_for_status()
                payload = response.json()
                summary = payload.get("query_summary") or payload.get("querySummary") or {}
                total = int(summary.get("total_reviews") or summary.get("totalReviews") or 0)
                positive = int(summary.get("total_positive") or summary.get("totalPositive") or 0)
                if total <= 0:
                    return {"text": "暂无评价"}
                percent = round(positive * 100 / total)
                if percent >= 95:
                    label = "好评如潮"
                elif percent >= 80:
                    label = "特别好评"
                elif percent >= 70:
                    label = "多半好评"
                elif percent >= 40:
                    label = "褒贬不一"
                else:
                    label = "差评"
                return {"text": label, "percent": percent, "total": total}
        except Exception as exc:
            logger.warning(f"获取 Steam 评价摘要失败: {exc} (appid={gid})")
            return None

    async def fetch_game_details(self, appid, language="schinese"):
        """获取 Steam 商店游戏详情。"""
        gid = str(appid).strip()
        if not gid.isdigit():
            return None
        url = f"{self.STEAM_STORE_BASE}/api/appdetails?appids={gid}&l={language}&cc=cn"
        try:
            async with httpx.AsyncClient(timeout=15, **httpx_client_kwargs(self.proxy)) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json().get(gid, {})
                return payload.get("data") if payload.get("success") else None
        except Exception as exc:
            logger.warning(f"获取 Steam 游戏详情失败: {exc} (appid={gid})")
            return None

    async def fetch_region_price(self, appid, country="CN"):
        """获取指定国家区 Steam 商店价格（含币种、折后价/原价/折扣）。返回 dict 或 None。"""
        gid = str(appid).strip()
        if not gid.isdigit():
            return None
        url = f"{self.STEAM_STORE_BASE}/api/appdetails?appids={gid}&cc={str(country).lower()}"
        try:
            async with httpx.AsyncClient(timeout=15, **httpx_client_kwargs(self.proxy)) as client:
                response = await client.get(url)
                response.raise_for_status()
                payload = response.json().get(gid, {})
                data = payload.get("data") if payload.get("success") else None
                if not data:
                    return None
                price_overview = data.get("price_overview") or {}
                if not price_overview:
                    return None
                return {
                    "currency": price_overview.get("currency"),
                    "current_price": price_overview.get("final", 0) / 100,
                    "current_regular": price_overview.get("initial", 0) / 100,
                    "cut": price_overview.get("discount_percent", 0),
                }
        except Exception as exc:
            logger.warning(f"获取 Steam {country} 区价格失败: {exc} (appid={gid})")
            return None

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
