from datetime import datetime
import asyncio
import tempfile
import time

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Plain, Image

from ...shared.logging import logger
from ...presentation.renderers.game_start import render_game_start
from ...presentation.renderers.game_end import render_game_end
from ...presentation.formatters.status import format_play_duration


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
        primary_session = notify_sessions.get(normalized_group_id)
        if primary_session:
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
            if push_session and push_session not in sessions:
                sessions.append(push_session)
        return sessions

    async def _render_notification_image(self, noti):
        try:
            if noti["type"] == "start":
                status = noti.get("status", {})
                avatar_url = status.get("avatarfull") or status.get("avatar")
                superpower = self.get_today_superpower(noti["sid"])
                font_path = self.get_font_path("NotoSansHans-Regular.otf")
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
                    font_path=self.get_font_path("NotoSansHans-Regular.otf"),
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
        key = self._notification_event_key(notification, session)
        if key in sent_events and now - sent_events[key] < 600:
            return False
        sent_events[key] = now
        for old_key, sent_at in list(sent_events.items()):
            if now - sent_at >= 600:
                del sent_events[old_key]
        return True

    async def _send_merged_notification(self, group_id, notifications):
        if not notifications:
            return
        session_notifications = {}
        for notification in notifications:
            for session in self._get_notify_sessions(group_id, notification["sid"]):
                if not self._should_send_notification(notification, session):
                    logger.info(
                        "Skipping duplicate Steam status notification "
                        "(session=%s, sid=%s, gameid=%s, type=%s)",
                        session, notification.get("sid"),
                        notification.get("gameid"), notification.get("type"),
                    )
                    continue
                session_notifications.setdefault(session, []).append(notification)
        for session, matched_notifications in session_notifications.items():
            msg_chain = []
            for notification in matched_notifications:
                if notification["type"] == "start":
                    line = f"🟢【{notification['name']}】开始游玩 {notification['game']}\n"
                else:
                    line = f"👋 {notification['name']} 不玩 {notification['game']}，游玩时间 {notification['duration_str']}\n"
                if self.config.get("notify_send_text", True):
                    msg_chain.append(Plain(line))
                if self.config.get("notify_send_image", True):
                    img_path = await self._render_notification_image(notification)
                    if img_path:
                        msg_chain.append(Image.fromFileSystem(img_path))
            if msg_chain:
                try:
                    await self.context.send_message(session, MessageChain(msg_chain))
                except Exception:
                    logger.exception(f"推送合并通知失败 (group_id={group_id}, session={session})")

    async def _flush_pending_end_notifications(self):
        if not self._pending_end_notifications:
            return
        for group_id, notifications in list(self._pending_end_notifications.items()):
            await self._send_merged_notification(group_id, notifications)
        self._pending_end_notifications.clear()

    async def _delayed_quit_check(self, group_id, sid, gameid):
        await asyncio.sleep(180)
        info = self.group_pending_quit.get(group_id, {}).get(sid, {}).get(gameid)
        if not info or info.get("notified"):
            return
        duration_min = info["duration_min"]
        if duration_min == 0:
            for _ in range(2):
                start_time = info["start_time"]
                if start_time and info["quit_time"]:
                    duration_min = (info["quit_time"] - start_time) / 60
                    if duration_min > 0:
                        info["duration_min"] = duration_min
                        break
                await asyncio.sleep(1)
        info["notified"] = True
        self._record_playtime(sid, gameid, info.get("game_name", "未知游戏"), info.get("duration_min", 0))
        self._record_session(
            sid=sid, gameid=gameid, game_name=info.get("game_name", "未知游戏"),
            start_time=info.get("start_time"), end_time=info.get("quit_time"),
            duration_min=info.get("duration_min", 0), group_id=group_id,
        )
        key = (group_id, sid, gameid)
        if not self.config.get("enable_game_end_notify", True):
            poll_task = self.achievement_poll_tasks.pop(key, None)
            if poll_task:
                poll_task.cancel()
            self.achievement_snapshots.pop(key, None)
            self.achievement_monitor.clear_game_achievements(group_id, sid, gameid)
            self.group_pending_quit.get(group_id, {}).get(sid, {}).pop(gameid, None)
            return
        duration_min = info["duration_min"]
        time_str = format_play_duration(duration_min)
        if duration_min < 5:
            tip_text = "风扇都没转热，主人就结束了？"
        elif duration_min < 10:
            tip_text = "杂鱼杂鱼~主人你就这水平？"
        elif duration_min < 30:
            tip_text = "热身一下就结束了？"
        elif duration_min < 60:
            tip_text = "歇会儿再来，别太累了喵！"
        elif duration_min < 120:
            tip_text = "沉浸在游戏世界，时间过得飞快喵！"
        elif duration_min < 300:
            tip_text = "肝到手软了喵！主人不如陪陪咱~"
        elif duration_min < 600:
            tip_text = "你吃饭了吗？还是说你已经忘了吃饭这件事？"
        elif duration_min < 1200:
            tip_text = "家里电费都要被你玩光了喵！"
        elif duration_min < 1800:
            tip_text = "咱都要给你颁发'不眠猫'勋章了！"
        elif duration_min < 2400:
            tip_text = "主人你还活着喵？你是不是忘了关电脑呀~"
        else:
            tip_text = "你已经和椅子合为一体，成为传说中的'椅子精'了喵！"
        last_state = self.group_last_states.get(group_id, {}).get(sid)
        self._pending_end_notifications.setdefault(group_id, []).append({
            "type": "end", "name": info["name"], "game": info["game_name"],
            "duration_str": time_str, "sid": sid, "gameid": gameid,
            "quit_time": info["quit_time"], "duration_min": duration_min,
            "avatar_url": (last_state or {}).get("avatarfull") or (last_state or {}).get("avatar"),
            "tip_text": tip_text,
        })
        poll_task = self.achievement_poll_tasks.pop(key, None)
        if poll_task:
            poll_task.cancel()
        self.achievement_snapshots.pop(key, None)
        self.achievement_monitor.clear_game_achievements(group_id, sid, gameid)
        self.group_pending_quit.get(group_id, {}).get(sid, {}).pop(gameid, None)


def format_play_duration(duration_min):
    if duration_min < 60:
        return f"{duration_min:.1f}分钟"
    return f"{duration_min / 60:.1f}小时"
