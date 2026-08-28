"""QQ 官方机器人指令面板的应用服务。"""

from __future__ import annotations

import asyncio
from typing import Any

from ...infrastructure.clients.qqofficial_panel import (
    QQOfficialPanelClient,
    QQOfficialPanelError,
)
from ...shared.logging import logger


DEFAULT_MENU_COMMANDS = [
    {"command": "/steam help", "description": "查看 Steam 插件指令帮助"},
    {"command": "/steam list", "description": "查看本群玩家当前状态"},
    {"command": "/steam rank", "description": "查看本群今日时长排行"},
    {"command": "/steam rank 7", "description": "查看本群最近七天排行"},
    {"command": "/steam rank 30", "description": "查看本群最近三十天排行"},
]


class QQMenuManagementMixin:
    """编排 QQ 面板用例，宿主插件只负责命令和事件适配。"""

    def _qq_menu_panel(self) -> dict[str, Any]:
        commands = self.config.get("qq_menu_commands", DEFAULT_MENU_COMMANDS)
        items = []
        for command_item in commands:
            command = str(command_item.get("command", "")).strip()
            description = str(command_item.get("description", "")).strip()
            if command and description:
                items.append(
                    {
                        "name": command,
                        "desc": description,
                        "type": "command",
                        "only_admin": False,
                    }
                )
        return {"remark": "Steam 状态监控", "items": items}

    def _qq_menu_client(self, event) -> tuple[QQOfficialPanelClient, str]:
        platform_id = event.get_platform_id()
        platform = self.context.get_platform_inst(platform_id)
        platform_type = str(platform.meta().name)
        if platform_type not in {"qq_official", "qq_official_webhook"}:
            raise QQOfficialPanelError("请从 QQ 官方机器人平台会话执行该指令")
        use_web_config = bool(self.config.get("qq_official_enabled", False))
        appid = str(
            self.config.get("qq_official_appid", "")
            if use_web_config
            else getattr(platform, "appid", "")
        ).strip()
        secret = str(
            self.config.get("qq_official_secret", "")
            if use_web_config
            else getattr(platform, "secret", "")
        ).strip()
        if not appid or not secret:
            source = "后台 QQ 官方机器人配置" if use_web_config else "QQ 官方机器人平台"
            raise QQOfficialPanelError(f"{source}缺少 appid 或 secret")
        return QQOfficialPanelClient(appid, secret, proxy=self.proxy), platform_type

    async def _save_qq_menu_panel_id(self, panel_id: str) -> None:
        self.config["qq_menu_panel_id"] = panel_id
        save_async = getattr(self.config, "save_config_async", None)
        if callable(save_async):
            await save_async()
            return
        save = getattr(self.config, "save_config", None)
        if callable(save):
            await asyncio.to_thread(save)

    def _qq_menu_group_openids(self, event, scope: str) -> tuple[list[str] | None, str | None]:
        raw_openids = self.config.get("qq_menu_group_openids", [])
        if isinstance(raw_openids, str):
            group_openids = [item.strip() for item in raw_openids.split(",") if item.strip()]
        else:
            group_openids = [str(item).strip() for item in raw_openids if str(item).strip()]
        if scope == "group" and not group_openids:
            platform = self.context.get_platform_inst(event.get_platform_id())
            platform_type = str(platform.meta().name)
            current_group = str(event.get_group_id() or "").strip()
            if platform_type in {"qq_official", "qq_official_webhook"} and current_group:
                group_openids = [current_group]
            else:
                return None, (
                    "群聊面板需要目标群 OpenID；当前会话不是 QQ 官方机器人会话，"
                    "请在后台填写 qq_menu_group_openids。"
                )
        return group_openids, None

    def _qq_menu_lock_for_use(self):
        lock = getattr(self, "_qq_menu_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._qq_menu_lock = lock
        return lock

    async def qq_menu_sync(self, event) -> str:
        if not self.config.get("qq_menu_enabled", False):
            return "QQ 指令面板未启用，请先在插件配置中开启 qq_menu_enabled。"
        scope = str(self.config.get("qq_menu_scope", "group")).strip().lower()
        if scope not in {"group", "c2c"}:
            return "qq_menu_scope 仅支持 group 或 c2c。"
        group_openids, error = self._qq_menu_group_openids(event, scope)
        if error:
            return error
        try:
            client, _ = self._qq_menu_client(event)
            async with self._qq_menu_lock_for_use():
                panel_id = str(self.config.get("qq_menu_panel_id", "")).strip()
                action = "更新"
                if panel_id:
                    try:
                        await client.update_panel(panel_id, self._qq_menu_panel())
                        if scope == "group":
                            await client.update_targets(panel_id, group_openids or [])
                    except QQOfficialPanelError as exc:
                        if not exc.panel_not_found:
                            raise
                        panel_id = ""
                if not panel_id:
                    panel_id = await client.create_panel(
                        scope=scope,
                        panel=self._qq_menu_panel(),
                        group_openids=group_openids or [],
                    )
                    await self._save_qq_menu_panel_id(panel_id)
                    action = "创建"
            target = f"{len(group_openids or [])} 个群" if scope == "group" else "单聊"
            return f"QQ 指令面板已{action}：{target}，panel_id={panel_id}"
        except QQOfficialPanelError as exc:
            logger.warning("[SteamStatusMonitor] QQ 指令面板同步失败: %s", exc)
            return f"QQ 指令面板同步失败：{exc}"
        except Exception as exc:
            logger.exception("[SteamStatusMonitor] QQ 指令面板同步异常")
            return f"QQ 指令面板同步异常：{type(exc).__name__}"

    async def qq_menu_status(self, event) -> str:
        panel_id = str(self.config.get("qq_menu_panel_id", "")).strip()
        if not panel_id:
            return "尚未记录 QQ 指令面板，请先执行 /steam qq菜单同步。"
        try:
            client, platform_type = self._qq_menu_client(event)
            data = await client.get_panel(panel_id)
            panel = data.get("panel") if isinstance(data.get("panel"), dict) else {}
            items = panel.get("items") if isinstance(panel.get("items"), list) else []
            return (
                "QQ 指令面板状态：\n"
                f"平台：{platform_type}\n"
                f"场景：{data.get('scope') or self.config.get('qq_menu_scope', 'group')}\n"
                f"面板 ID：{panel_id}\n"
                f"版本：{panel.get('version', '-')}\n"
                f"指令数：{len(items)}"
            )
        except QQOfficialPanelError as exc:
            if exc.panel_not_found:
                await self._save_qq_menu_panel_id("")
                return "QQ 指令面板已不存在，本地记录已清除，可重新同步。"
            return f"查询 QQ 指令面板失败：{exc}"

    async def qq_menu_delete(self, event) -> str:
        panel_id = str(self.config.get("qq_menu_panel_id", "")).strip()
        if not panel_id:
            return "尚未记录 QQ 指令面板，无需删除。"
        try:
            client, _ = self._qq_menu_client(event)
            await client.delete_panel(panel_id)
        except QQOfficialPanelError as exc:
            if not exc.panel_not_found:
                return f"删除 QQ 指令面板失败：{exc}"
        await self._save_qq_menu_panel_id("")
        return "QQ 指令面板已删除，本地记录已清除。"
