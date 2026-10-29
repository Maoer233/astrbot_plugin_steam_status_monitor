import asyncio
from datetime import datetime
import tempfile
import time

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image

from .notification_queue import enqueue_notifications, trim_notifications, TTL_SECONDS
from ...shared.fonts import resolve_font_path
from ...shared.logging import logger
from ...shared.utils.notify_session import is_sendable_group_session
from ...shared.utils.quiet_hours import in_quiet_hours
from ...presentation.formatters.status import format_play_duration
from ...presentation.renderers.game_start import render_game_start
from ...presentation.renderers.game_end import render_game_end
from ...presentation.renderers.owned_game import render_owned_game
from ...presentation.renderers.owned_games_batch import render_owned_games_batch


class NotificationTrackingMixin:
    """游戏状态通知的路由、渲染、缓冲和延迟结算编排。"""

    def _compact_cover_concurrency(self) -> int:
        """简洁模式的封面并发数：复用 owned_games 服务的配置读取，缺服务时用默认值。"""
        service = getattr(self, "owned_games", None)
        getter = getattr(service, "_compact_cover_concurrency", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                pass
        return 4

    def _get_notify_sessions(self, group_id, sid):
        sessions = []
        normalized_group_id = str(group_id)
        normalized_sid = str(sid)
        notify_sessions = {
            str(key): value
            for key, value in (getattr(self, "notify_sessions", {}) or {}).items()
        }
        enabled = getattr(self, "group_monitor_enabled", {}) or {}

        def _group_enabled(gid):
            return enabled.get(str(gid), True)

        primary_session = notify_sessions.get(normalized_group_id)
        if primary_session and _group_enabled(normalized_group_id):
            sessions.append(primary_session)
        monitored_groups = {
            str(gid)
            for gid, steam_ids in getattr(self, "group_steam_ids", {}).items()
            if normalized_sid in {str(value) for value in steam_ids}
        }
        push_targets = (getattr(self, "push_groups", {}) or {}).get(normalized_sid)
        if push_targets is None:
            push_targets = next(
                (
                    targets
                    for raw_sid, targets in (getattr(self, "push_groups", {}) or {}).items()
                    if str(raw_sid) == normalized_sid
                ),
                [],
            )
        for push_gid in push_targets:
            normalized_push_gid = str(push_gid)
            push_session = notify_sessions.get(normalized_push_gid)
            if push_session and push_session not in sessions and _group_enabled(normalized_push_gid):
                sessions.append(push_session)
        return [
            session
            for session in sessions
            if is_sendable_group_session(session)
        ]

    async def _render_notification_image(self, noti):
        try:
            if noti["type"] in ("online", "offline"):
                # 上线/下线为纯文本推送，不渲染图片
                return None
            if noti["type"] == "owned_batch":
                # 简洁模式：本轮新增合并成清单图；不取卡片详情，封面命中缓存即零网络
                img_bytes = await render_owned_games_batch(
                    self.data_dir, noti["sid"], noti["name"], noti.get("avatar_url"),
                    items=noti.get("items") or [],
                    page=noti.get("page") or 1,
                    pages=noti.get("pages") or 1,
                    owned_count=noti.get("owned_count") or 0,
                    added_count=noti.get("added_count") or 0,
                    added_at=noti.get("added_at"),
                    page_size=noti.get("page_size") or 0,
                    api_key=self.API_KEY, sgdb_api_key=self.SGDB_API_KEY,
                    sgdb_api_base=self.SGDB_API_BASE, steam_store_base=self.STEAM_STORE_BASE,
                    steam_api_base=self.STEAM_API_BASE, proxy=self.proxy,
                    version=self._plugin_version,
                    cover_concurrency=self._compact_cover_concurrency(),
                )
            elif noti["type"] == "owned":
                if not noti.get("render", True):
                    return None  # 超出单轮卡片上限的新增：仅文本，不渲染图片
                img_bytes = await render_owned_game(
                    self.data_dir, noti["sid"], noti["name"], noti.get("avatar_url"),
                    noti.get("gameid"), noti.get("game") or "未知游戏",
                    owned_count=noti.get("owned_count") or 0,
                    card_data=noti.get("card_data"),
                    added_at=noti.get("added_at"),
                    api_key=self.API_KEY, sgdb_api_key=self.SGDB_API_KEY,
                    sgdb_api_base=self.SGDB_API_BASE, steam_store_base=self.STEAM_STORE_BASE,
                    steam_api_base=self.STEAM_API_BASE, proxy=self.proxy,
                    version=self._plugin_version,
                )
            elif noti["type"] == "start":
                status = noti.get("status", {})
                avatar_url = status.get("avatarfull") or status.get("avatar")
                superpower = self.superpower.get(noti["sid"])
                font_path = resolve_font_path("NotoSansHans-Regular.otf")
                zh_game_name, en_game_name = await self.get_game_names(noti["gameid"], noti["game"])
                img_bytes = await render_game_start(
                    self.data_dir, noti["sid"], noti["name"], avatar_url,
                    noti["gameid"], zh_game_name, api_key=self.API_KEY,
                    superpower=superpower, sgdb_api_key=self.SGDB_API_KEY,
                    font_path=font_path, sgdb_game_name=en_game_name,
                    online_count=await self.get_game_online_count(noti["gameid"]),
                    appid=noti.get("gameid"), proxy=self.proxy,
                    version=self._plugin_version, sgdb_api_base=self.SGDB_API_BASE,
                )
            else:
                end_time_str = datetime.fromtimestamp(noti["quit_time"]).strftime("%Y-%m-%d %H:%M")
                duration_h = noti["duration_min"] / 60 if noti["duration_min"] > 0 else 0
                zh_game_name, en_game_name = await self.get_game_names(noti["gameid"], noti["game"])
                img_bytes = await render_game_end(
                    self.data_dir, noti["sid"], noti["name"], noti.get("avatar_url"),
                    noti["gameid"], zh_game_name, end_time_str,
                    noti.get("tip_text") or "你已经和椅子合为一体，成为传说中的'椅子精'了喵！",
                    duration_h, sgdb_api_key=self.SGDB_API_KEY,
                    font_path=resolve_font_path("NotoSansHans-Regular.otf"),
                    sgdb_game_name=en_game_name, appid=noti.get("gameid"),
                    proxy=self.proxy, api_key=self.API_KEY,
                    sgdb_api_base=self.SGDB_API_BASE, steam_store_base=self.STEAM_STORE_BASE,
                )
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp.write(img_bytes)
                return tmp.name
        except Exception as e:
            logger.error(f"渲染通知图片失败 ({noti.get('type')}, {noti.get('name')}): {e}")
            return None

    @staticmethod
    def _is_intentionally_unrendered(notification):
        """该通知是否「本就不该出图」（区别于「渲染失败」）。

        目前只有游戏库新入库族带 render 开关：详细模式超出单轮卡片上限的条目
        会被显式标记 render=False，此时没有图片是设计行为，不是故障。
        """
        if notification.get("type") not in ("owned", "owned_batch"):
            return False
        return not notification.get("render", True)

    def _notification_event_key(self, notification, session):
        event_time = notification.get("start_time")
        identifier = notification.get("gameid")
        if notification.get("type") == "end":
            event_time = notification.get("quit_time")
        elif notification.get("type") == "owned":
            event_time = notification.get("added_at")
        elif notification.get("type") == "owned_batch":
            # 合并清单没有单一 gameid：用页码占位，避免同批多页塌缩成同一个去重键。
            # 元组形状保持 5 位不变，既有去重语义与测试断言不受影响。
            event_time = notification.get("added_at")
            identifier = "p{0}/{1}".format(
                int(notification.get("page") or 1),
                int(notification.get("pages") or 1),
            )
        elif event_time is None:
            # 上线/下线通知用检测时刻作键，600 秒窗口内不同次的转换不被误去重
            event_time = notification.get("event_time")
        return (
            str(session),
            str(notification.get("sid")),
            str(identifier),
            str(notification.get("type")),
            int(event_time) if event_time is not None else None,
        )

    def _should_send_notification(self, notification, session):
        now = time.time()
        sent_events = getattr(self, "_sent_notification_events", None)
        if sent_events is None:
            sent_events = self._sent_notification_events = {}
        inflight_events = getattr(self, "_inflight_notification_events", None)
        if inflight_events is None:
            inflight_events = self._inflight_notification_events = set()
        for old_key, sent_at in list(sent_events.items()):
            if now - sent_at >= 600:
                del sent_events[old_key]
        key = self._notification_event_key(notification, session)
        if key in sent_events or key in inflight_events:
            return False
        inflight_events.add(key)
        return True

    def _finish_notification(self, notification, session, *, success):
        key = self._notification_event_key(notification, session)
        inflight_events = getattr(self, "_inflight_notification_events", set())
        inflight_events.discard(key)
        if success:
            self._sent_notification_events[key] = time.time()

    async def _send_merged_notification(self, group_id, notifications):
        if not notifications:
            return []
        if in_quiet_hours(getattr(self, "config", {}) or {}):
            # 静默时段：只跳过播报，不打断轮询/记账；返回空失败列表等于「已消费」，
            # pending 队列（含 owned_games 持久化队列）不会在静默结束后补发。
            logger.info(
                "静默时段跳过播报 (group_id=%s, 共 %s 条)", group_id, len(notifications)
            )
            return []
        session_notifications = {}
        for notification in notifications:
            targets = self._get_notify_sessions(group_id, notification["sid"])
            if "_target_session" in notification:
                targets = [session for session in targets if session == notification["_target_session"]]
            for session in targets:
                if not self._should_send_notification(notification, session):
                    continue
                session_notifications.setdefault(session, []).append(notification)

        if not session_notifications:
            return []
        images = {}
        cooldowns = getattr(self, "_notification_cooldowns", None)
        if cooldowns is None:
            cooldowns = self._notification_cooldowns = {}
        now = time.monotonic()
        for target, (_, deadline) in list(cooldowns.items()):
            if now - deadline > 1800:
                del cooldowns[target]

        async def send_one(session, matched_notifications):
            msg_chain = []
            included = []
            retry = []
            for notification in matched_notifications:
                if self.config.get("notify_send_image", True):
                    key = id(notification)
                    if key not in images:
                        images[key] = await self._render_notification_image(notification)
                before = len(msg_chain)
                if notification["type"] == "start":
                    line = f"🟢【{notification['name']}】开始游玩 {notification['game']}\n"
                elif notification["type"] == "owned":
                    line = (
                        f"📚【{notification['name']}】新入库 {notification['game']}"
                        f"（当前共 {notification.get('owned_count') or 0} 款）\n"
                    )
                elif notification["type"] == "owned_batch":
                    count = notification.get("added_count") or len(notification.get("items") or [])
                    page = notification.get("page") or 1
                    pages = notification.get("pages") or 1
                    line = (
                        f"📚【{notification['name']}】新入库 {count} 款"
                        f"（当前共 {notification.get('owned_count') or 0} 款）\n"
                    )
                    if page == 1:
                        line += f"第 {page}/{pages} 页\n"
                elif notification["type"] == "online":
                    line = f"🔵【{notification['name']}】已上线\n"
                elif notification["type"] == "offline":
                    minutes = notification.get("online_minutes")
                    suffix = f"（本次在线 {format_play_duration(minutes)}）" if minutes is not None else ""
                    line = f"🌑【{notification['name']}】已离线{suffix}\n"
                else:
                    line = f"👋 {notification['name']} 不玩 {notification['game']}，游玩时间 {notification['duration_str']}\n"
                if self.config.get("notify_send_text", True):
                    msg_chain.append(Plain(line))
                img_path = images.get(id(notification))
                if img_path:
                    msg_chain.append(Image.fromFileSystem(img_path))
                if len(msg_chain) > before:
                    included.append(notification)
                elif (
                    self.config.get("notify_send_image", True)
                    and notification["type"] not in ("online", "offline")
                    and not self._is_intentionally_unrendered(notification)
                ):
                    retry.append(notification)
                else:
                    self._finish_notification(notification, session, success=True)
            if not msg_chain:
                return retry
            try:
                await self.context.send_message(session, MessageChain(msg_chain))
            except Exception:
                for notification in matched_notifications:
                    self._finish_notification(notification, session, success=False)
                logger.exception("推送合并通知失败 (group_id=%s, session=%s)", group_id, session)
                return matched_notifications
            for notification in included:
                self._finish_notification(notification, session, success=True)
            return retry

        async def send_batches(session, matched):
            failed = []
            managed = any(n["type"] not in ("owned", "owned_batch") for n in matched)
            try:
                if managed and time.monotonic() < cooldowns.get(session, (0, 0))[1]:
                    return [dict(n, _target_session=session) for n in matched]
                for offset in range(0, len(matched), 5):
                    batch = [n for n in matched[offset:offset + 5]
                             if time.time() - n.get("_enqueued_at", time.time()) < TTL_SECONDS]
                    result = await send_one(session, batch)
                    if result:
                        attempts = min(cooldowns.get(session, (0, 0))[0] + 1, 5)
                        delay = min(30 * 2 ** (attempts - 1), 300)
                        if managed:
                            cooldowns[session] = (attempts, time.monotonic() + delay)
                            logger.error("通知目标退避 (session=%s, delay=%s, pending=%s)",
                                         session, delay, len(matched) - offset)
                        failed.extend(result)
                        failed.extend(matched[offset + 5:])
                        break
                    if batch and managed:
                        cooldowns.pop(session, None)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("推送通知异常 (group_id=%s, session=%s)", group_id, session)
                if managed:
                    attempts = min(cooldowns.get(session, (0, 0))[0] + 1, 5)
                    cooldowns[session] = (attempts, time.monotonic() + min(30 * 2 ** (attempts - 1), 300))
                sent = getattr(self, "_sent_notification_events", {})
                failed = [n for n in matched if self._notification_event_key(n, session) not in sent]
            finally:
                for notification in matched:
                    self._finish_notification(notification, session, success=False)
            return [dict(n, _target_session=session) for n in failed]

        tasks = [asyncio.create_task(send_batches(session, matched))
                 for session, matched in session_notifications.items()]
        try:
            results = await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return [notification for result in results for notification in result]

    async def _flush_pending_end_notifications(self):
        lock = getattr(self, "_notification_flush_lock", None)
        if lock is None:
            lock = self._notification_flush_lock = asyncio.Lock()
        async with lock:
            trim_notifications(self)
            if not self._pending_end_notifications:
                return
            pending = self._pending_end_notifications
            self._pending_end_notifications = {}
            self._active_notification_flush = pending
            groups = list(pending.items())
            try:
                for index, (group_id, notifications) in enumerate(groups):
                    if not (getattr(self, "group_monitor_enabled", {}) or {}).get(str(group_id), True):
                        pending.pop(group_id, None)
                        continue
                    try:
                        failed = await self._send_merged_notification(group_id, notifications)
                    except BaseException:
                        self._active_notification_flush = {}
                        for remaining_group, remaining in groups[index:]:
                            unsent = []
                            for n in remaining:
                                target = n.get("_target_session")
                                key = self._notification_event_key(n, target)
                                if key not in getattr(self, "_sent_notification_events", {}):
                                    unsent.append(n)
                            enqueue_notifications(self, remaining_group, unsent)
                        raise
                    pending.pop(group_id, None)
                    if failed:
                        enqueue_notifications(self, group_id, failed)
            finally:
                self._active_notification_flush = {}
