"""AstrBot 插件兼容入口。

具体实现位于 ``src.plugin.steam_status_monitor``，根入口保留用于 AstrBot 插件发现。
"""

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.star.star_handler import star_handlers_registry

from .src.plugin.steam_status_monitor import SteamStatusMonitorV3


class Main(SteamStatusMonitorV3):
    """AstrBot plugin entry point."""

    async def steam_owned_games(
        self,
        event: AstrMessageEvent,
        /,
        query: str = "",
        offset: int = 0,
        limit: int = 20,
        **_ignored,
    ) -> str:
        # LLM 工具注册桩必须留在入口模块：AstrBot 按 handler.__module__ 匹配
        # 插件模块路径并绑定插件实例；注册在 src/ 子模块里不会绑定，调用时
        # 会把 event 当成 self。官方机器人链路还可能额外传 event 关键字参数，
        # 这里用 **_ignored 吞掉，只保留位置参数里的真实事件对象。
        return await SteamStatusMonitorV3.steam_owned_games(
            self,
            event,
            query=query,
            offset=offset,
            limit=limit,
        )

    async def terminate(self):
        # AstrBot 只调用入口类自身 __dict__ 里的 terminate，继承来的方法不会被调用。
        await super().terminate()


# AstrBot 通过模块路径把处理器绑定到插件实例。处理器定义在实现模块，
# 入口类定义在当前模块，因此需要将处理器归属调整到运行时入口模块。
_IMPLEMENTATION_MODULE = SteamStatusMonitorV3.__module__
for _handler in star_handlers_registry:
    if _handler.handler_module_path == _IMPLEMENTATION_MODULE:
        _handler.handler_module_path = __name__


# LLM 工具的 schema 来自函数 docstring；复用实现方法的 docstring，避免两处维护。
Main.steam_owned_games.__doc__ = SteamStatusMonitorV3.steam_owned_games.__doc__
Main.steam_owned_games = filter.llm_tool(name="steam_owned_games")(
    Main.steam_owned_games
)


__all__ = ["Main", "SteamStatusMonitorV3"]
