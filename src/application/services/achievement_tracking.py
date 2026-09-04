import asyncio
import tempfile
import time

from astrbot.api.event import MessageChain
from astrbot.api.message_components import Image

from ...shared.logging import logger
from ...shared.utils.notify_session import is_sendable_group_session


class AchievementTrackingMixin:
    """成就轮询、结束补偿和成就通知的应用层编排。"""

    async def achievement_periodic_check(self, group_id, sid, gameid, player_name, game_name):
        key = (group_id, sid, gameid)
        try:
            while True:
                await asyncio.sleep(1200)
                if gameid in self.achievement_blacklist:
                    logger.info(f"[成就定时对比] 游戏 {gameid} 已在黑名单，跳过轮询")
                    break
                achievements_a = self.achievement_snapshots.get(key)
                achievements_b = await self.achievement_monitor.get_player_achievements(
                    self.API_KEY, group_id, sid, gameid
                )
                today = time.strftime('%Y-%m-%d')
                fail_key = (gameid, today)
                if achievements_b is None:
                    cnt = self.achievement_fail_count.get(fail_key, 0) + 1
                    self.achievement_fail_count[fail_key] = cnt
                    if cnt >= 10:
                        self.achievement_blacklist.add(gameid)
                        logger.info(f"[成就黑名单] 游戏 {gameid} 当天累计获取失败10次，已加入黑名单")
                        break
                    continue
                if achievements_a is not None:
                    new_achievements = set(achievements_b) - set(achievements_a)
                    if new_achievements:
                        logger.info(f"[成就定时对比] {player_name} 在 {game_name} 解锁新成就：{', '.join(new_achievements)}")
                        await self.notify_new_achievements(group_id, sid, player_name, gameid, game_name, new_achievements)
                        self.achievement_snapshots[key] = list(achievements_b)
                    else:
                        logger.info(f"[成就定时对比] {player_name} 在 {game_name} 未发现新成就")
        except asyncio.CancelledError:
            logger.info(f"[成就定时对比] 任务已取消 group_id={group_id} sid={sid} gameid={gameid}")
        except Exception as e:
            logger.error(f"[成就定时对比] group_id={group_id} sid={sid} gameid={gameid} 异常: {e}")

    async def achievement_delayed_final_check(self, group_id, sid, gameid, player_name, game_name, achievements_a=None):
        key = (group_id, sid, gameid)
        await asyncio.sleep(300)
        if gameid in self.achievement_blacklist:
            logger.info(f"[成就结束冗余对比] 游戏 {gameid} 已在黑名单，跳过轮询")
            return
        # 优先使用结束瞬间捕获的基准；未传（兼容旧调用）则读全局快照
        if achievements_a is None:
            achievements_a = self.achievement_snapshots.get(key)
        achievements_b = await self.achievement_monitor.get_player_achievements(
            self.API_KEY, group_id, sid, gameid
        )
        fail_key = (gameid, time.strftime('%Y-%m-%d'))
        if achievements_b is None:
            cnt = self.achievement_fail_count.get(fail_key, 0) + 1
            self.achievement_fail_count[fail_key] = cnt
            if cnt >= 10:
                self.achievement_blacklist.add(gameid)
                logger.info(f"[成就黑名单] 游戏 {gameid} 当天累计获取失败10次，已加入黑名单")
                return
        if achievements_a is not None and achievements_b is not None:
            new_achievements = set(achievements_b) - set(achievements_a)
            if new_achievements:
                logger.info(f"[成就结束冗余对比] {player_name} 在 {game_name} 解锁新成就：{', '.join(new_achievements)}")
                await self.notify_new_achievements(group_id, sid, player_name, gameid, game_name, new_achievements)
            else:
                logger.info(f"[成就结束冗余对比] {player_name} 在 {game_name} 未发现新成就")
        # 若该 key 已被新的成就轮询/会话占用（如重开同游戏或 A→B→A 切回），则跳过清理，避免误清新局数据
        if key in getattr(self, "achievement_poll_tasks", {}):
            return
        self.achievement_snapshots.pop(key, None)
        self.achievement_poll_tasks.pop(key, None)
        self.achievement_monitor.clear_game_achievements(group_id, sid, gameid)

    async def notify_new_achievements(self, group_id, steamid, player_name, gameid, game_name, new_achievements):
        if not self.group_achievement_enabled.get(group_id, True):
            return
        if not new_achievements or not self.notify_sessions:
            return
        achievements_to_notify = list(new_achievements)[:self.max_achievement_notifications]
        details = self.achievement_monitor.details_cache.get((group_id, gameid))
        if not details:
            try:
                details = await self.achievement_monitor.get_achievement_details(
                    group_id, gameid, lang="schinese", api_key=self.API_KEY, steamid=steamid
                )
            except Exception as e:
                details = None
                logger.warning(f"获取成就详情失败: {e}")
        if details and game_name:
            for detail in details.values():
                detail["game_name"] = game_name
        font_path = self.get_font_path('NotoSansHans-Regular.otf')
        notify_sessions = []
        notify_session = getattr(self, 'notify_sessions', {}).get(group_id)
        if notify_session:
            notify_sessions.append(notify_session)
        for push_gid in self.push_groups.get(steamid, []):
            push_session = getattr(self, 'notify_sessions', {}).get(push_gid)
            if push_session and push_session not in notify_sessions:
                notify_sessions.append(push_session)
        notify_sessions = [
            session for session in notify_sessions
            if is_sendable_group_session(session)
        ]
        if not notify_sessions:
            logger.warning(
                "成就通知无有效会话，已跳过 (group_id=%s, steamid=%s)",
                group_id,
                steamid,
            )
            return
        tmp_path = None
        if self.config.get('notify_send_image', True) and details:
            unlocked_set = await self.achievement_monitor.get_player_achievements(
                self.API_KEY, group_id, steamid, gameid
            )
            if not unlocked_set:
                unlocked_set = set(self.achievement_snapshots.get((group_id, steamid, gameid), []))
            try:
                img_bytes = await self.achievement_monitor.render_achievement_image(
                    details, set(achievements_to_notify), player_name=player_name,
                    steamid=steamid, appid=gameid, unlocked_set=unlocked_set or set(),
                    font_path=font_path,
                )
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                    tmp.write(img_bytes)
                    tmp_path = tmp.name
            except Exception as e:
                logger.error(f"成就图片渲染失败: {e}")
        if not tmp_path:
            return
        for session in notify_sessions:
            try:
                await self.context.send_message(session, MessageChain([Image.fromFileSystem(tmp_path)]))
            except Exception as e:
                logger.error(f"发送成就通知失败: {e}")
