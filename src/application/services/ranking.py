from datetime import datetime, timedelta
from typing import Callable, Dict, Iterable, List, Optional, Tuple
import time

from ...shared.logging import logger


class RankingService:
    """游玩分钟账本：日聚合、时间片、跨群去重。不负责出图或推送范围。"""

    def __init__(
        self,
        plugin=None,
        *,
        clock: Optional[Callable[[], float]] = None,
        now: Optional[Callable[[], datetime]] = None,
    ):
        self._plugin = plugin
        self._clock = clock or time.time
        self._now = now or datetime.now
        self._play_records: Dict[str, dict] = {}
        self._session_records: Dict[str, list] = {}
        self._session_dirty = False
        self._recorded_quit_cache: Dict[Tuple[str, str], float] = {}

    @property
    def play_records(self) -> dict:
        if self._plugin is not None:
            records = getattr(self._plugin, "play_records", None)
            if records is None:
                records = {}
                self._plugin.play_records = records
            return records
        return self._play_records

    @play_records.setter
    def play_records(self, value):
        records = value or {}
        if self._plugin is not None:
            self._plugin.play_records = records
        else:
            self._play_records = records

    @property
    def session_records(self) -> dict:
        if self._plugin is not None:
            records = getattr(self._plugin, "session_records", None)
            if records is None:
                records = {}
                self._plugin.session_records = records
            return records
        return self._session_records

    @session_records.setter
    def session_records(self, value):
        records = value or {}
        if self._plugin is not None:
            self._plugin.session_records = records
        else:
            self._session_records = records

    @property
    def session_dirty(self) -> bool:
        if self._plugin is not None:
            return bool(getattr(self._plugin, "_session_dirty", False))
        return self._session_dirty

    @session_dirty.setter
    def session_dirty(self, value: bool):
        if self._plugin is not None:
            self._plugin._session_dirty = bool(value)
        else:
            self._session_dirty = bool(value)

    @property
    def recorded_quit_cache(self) -> dict:
        if self._plugin is not None:
            cache = getattr(self._plugin, "_recorded_quit_cache", None)
            if cache is None:
                cache = {}
                self._plugin._recorded_quit_cache = cache
            return cache
        return self._recorded_quit_cache

    def day_key(self, offset_days: int = 0, now: Optional[datetime] = None) -> str:
        current = now or self._now()
        if current.hour < 4:
            current = current - timedelta(days=1)
        current = current + timedelta(days=offset_days)
        return current.strftime("%Y-%m-%d")

    def record_closed(
        self,
        sid,
        gameid,
        game_name,
        started_at,
        ended_at,
        duration_min,
        group_id,
    ) -> None:
        self.record_session(
            sid=sid,
            gameid=gameid,
            game_name=game_name,
            start_time=started_at,
            end_time=ended_at,
            duration_min=duration_min,
            group_id=group_id,
        )
        self.record_playtime(sid, gameid, game_name, duration_min)

    def record_session(self, sid, gameid, game_name, start_time, end_time, duration_min, group_id) -> None:
        if duration_min <= 0 or not gameid:
            return
        date_str = self.day_key(0)
        start_timestamp = int(start_time) if start_time else 0
        end_timestamp = int(end_time) if end_time else 0
        session_id = f"{date_str}_{start_timestamp}_{gameid}"
        records = self.session_records
        if self._plugin is not None:
            self._plugin.session_records = records
        sessions = records.setdefault(str(sid), [])
        if any(item.get("session_id") == session_id for item in sessions):
            return
        sessions.append({
            "session_id": session_id,
            "gameid": str(gameid),
            "game_name": str(game_name),
            "start_time": start_timestamp,
            "end_time": end_timestamp,
            "duration_min": round(float(duration_min), 2),
            "date": date_str,
            "group_id": str(group_id),
        })
        self.session_dirty = True

    def record_playtime(self, sid, gameid, game_name, duration_min) -> None:
        try:
            if duration_min <= 0 or not gameid:
                return
            if isinstance(game_name, (tuple, list)):
                game_name = game_name[0] if game_name else "未知游戏"
            game_name = str(game_name) if game_name else "未知游戏"
            cache_key = (str(sid), str(gameid))
            now = self._clock()
            last_ts = self.recorded_quit_cache.get(cache_key, 0)
            if now - last_ts < 300:
                logger.debug(f"[排行榜] 去重跳过: {sid} {game_name} (上次记录{int(now-last_ts)}秒前)")
                return
            self.recorded_quit_cache[cache_key] = now
            today_key = self.day_key(0)
            records = self.play_records
            if self._plugin is not None:
                self._plugin.play_records = records
            if today_key not in records:
                records[today_key] = {}
            if str(sid) not in records[today_key]:
                records[today_key][str(sid)] = {}
            gid = str(gameid)
            if gid not in records[today_key][str(sid)]:
                records[today_key][str(sid)][gid] = {"name": game_name, "minutes": 0}
            records[today_key][str(sid)][gid]["minutes"] += int(duration_min)
            records[today_key][str(sid)][gid]["name"] = game_name
            if self._plugin is not None:
                self._plugin._data_dirty = True
            logger.info(f"[排行榜] 记录游玩时长: {sid} {game_name} +{int(duration_min)}分钟")
            expired = [key for key, ts in list(self.recorded_quit_cache.items()) if now - ts > 600]
            for key in expired:
                self.recorded_quit_cache.pop(key, None)
        except Exception as exc:
            logger.error(f"[排行榜] 记录游玩时长异常: {exc}")

    def aggregate(self, days: int = 1, sids: Optional[Iterable[str]] = None, base_day_offset: int = 0) -> List[dict]:
        try:
            today_str = self.day_key(base_day_offset)
            base_date = datetime.strptime(today_str, "%Y-%m-%d")
            date_keys = [
                (base_date - timedelta(days=index)).strftime("%Y-%m-%d")
                for index in range(days)
            ]
            target_sids = {str(sid) for sid in (sids or [])}
            if not target_sids:
                return []
            merged = {}
            for date_key in date_keys:
                day_data = self.play_records.get(date_key, {})
                for sid, games in day_data.items():
                    if sid not in target_sids:
                        continue
                    if sid not in merged:
                        merged[sid] = {}
                    for gid, info in games.items():
                        raw_name = info.get("name", "未知游戏")
                        if isinstance(raw_name, (tuple, list)):
                            raw_name = raw_name[0] if raw_name else "未知游戏"
                        raw_name = str(raw_name) if raw_name else "未知游戏"
                        if gid not in merged[sid]:
                            merged[sid][gid] = {"name": raw_name, "minutes": 0}
                        merged[sid][gid]["minutes"] += info.get("minutes", 0)
                        merged[sid][gid]["name"] = info.get("name", merged[sid][gid]["name"])
            rank_list = []
            for sid, games in merged.items():
                total = sum(game["minutes"] for game in games.values())
                if total <= 0:
                    continue
                game_list = sorted(
                    [{"name": game["name"], "minutes": game["minutes"], "gameid": gid} for gid, game in games.items()],
                    key=lambda item: item["minutes"],
                    reverse=True,
                )
                rank_list.append({
                    "sid": sid,
                    "name": game_list[0]["name"] if game_list else sid,
                    "total_minutes": total,
                    "games": game_list,
                })
            rank_list.sort(key=lambda item: item["total_minutes"], reverse=True)
            return rank_list
        except Exception as exc:
            logger.error(f"[排行榜] 聚合数据异常: {exc}")
            return []

    def target_sids(self, group_id: Optional[str] = None) -> set:
        plugin = self._plugin
        groups = getattr(plugin, "group_steam_ids", {}) or {}
        if group_id:
            direct = groups.get(group_id, [])
            push_ids = [
                sid
                for sid, targets in (getattr(plugin, "push_groups", {}) or {}).items()
                if group_id in {str(target) for target in targets}
            ]
            return set(direct) | set(push_ids)
        sids = set()
        for group_ids in groups.values():
            sids.update(group_ids)
        return sids
