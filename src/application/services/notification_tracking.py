import asyncio
from datetime import datetime
import tempfile
import time

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image

from ...shared.fonts import resolve_font_path
from ...shared.logging import logger
from ...shared.utils.notify_session import is_sendable_group_session
from ...presentation.renderers.game_start import render_game_start
from ...presentation.renderers.game_end import render_game_end


class NotificationTrackingMixin:
    """游戏状态通知的路由、渲染、缓冲和延迟结算编排。"""

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
            if noti["type"] == "start":
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

    def _notification_event_key(self, notification, session):
        event_time = notification.get("start_time")
        if notification.get("type") == "end":
            event_time = notification.get("quit_time")
        return (
            str(session),
            str(notification.get("sid")),
            str(notification.get("gameid")),
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
        if self.config.get("notify_send_image", True):
            for matched in session_notifications.values():
                for notification in matched:
                    key = id(notification)
                    if key not in images:
                        images[key] = await self._render_notification_image(notification)

        async def send_one(session, matched_notifications):
            msg_chain = []
            for notification in matched_notifications:
                if notification["type"] == "start":
                    line = f"🟢【{notification['name']}】开始游玩 {notification['game']}\n"
                else:
                    line = f"👋 {notification['name']} 不玩 {notification['game']}，游玩时间 {notification['duration_str']}\n"
                if self.config.get("notify_send_text", True):
                    msg_chain.append(Plain(line))
                img_path = images.get(id(notification))
                if img_path:
                    msg_chain.append(Image.fromFileSystem(img_path))
            if not msg_chain:
                if self.config.get("notify_send_image", True) and not self.config.get("notify_send_text", True):
                    for notification in matched_notifications:
                        self._finish_notification(notification, session, success=False)
                    return matched_notifications
                for notification in matched_notifications:
                    self._finish_notification(notification, session, success=False)
                return []
            try:
                await self.context.send_message(session, MessageChain(msg_chain))
            except Exception:
                for notification in matched_notifications:
                    self._finish_notification(notification, session, success=False)
                logger.exception("推送合并通知失败 (group_id=%s, session=%s)", group_id, session)
                return matched_notifications
            for notification in matched_notifications:
                self._finish_notification(notification, session, success=True)
            return []

        sessions = list(session_notifications.items())
        results = await asyncio.gather(
            *(send_one(session, matched) for session, matched in sessions),
            return_exceptions=True,
        )
        failed = []
        for (session, matched), result in zip(sessions, results):
            if isinstance(result, BaseException):
                logger.error("推送通知异常 (group_id=%s, session=%s): %s", group_id, session, result)
                result = matched
            for notification in result:
                retry = dict(notification, _target_session=session)
                failed.append(retry)
        return failed

    async def _flush_pending_end_notifications(self):
        lock = getattr(self, "_notification_flush_lock", None)
        if lock is None:
            lock = self._notification_flush_lock = asyncio.Lock()
        async with lock:
            if not self._pending_end_notifications:
                return
            pending = self._pending_end_notifications
            self._pending_end_notifications = {}
            for group_id, notifications in pending.items():
                if not (getattr(self, "group_monitor_enabled", {}) or {}).get(str(group_id), True):
                    continue
                try:
                    failed = await self._send_merged_notification(group_id, notifications)
                except BaseException:
                    self._pending_end_notifications.setdefault(group_id, []).extend(notifications)
                    raise
                if failed:
                    self._pending_end_notifications.setdefault(group_id, []).extend(failed)
