"""AstrBot 插件兼容入口。

具体实现位于 ``src.plugin.steam_status_monitor``，根入口保留用于 AstrBot 插件发现。
"""

from astrbot.core.star.star_handler import star_handlers_registry

from .src.plugin.steam_status_monitor import SteamStatusMonitorV3


class Main(SteamStatusMonitorV3):
    """AstrBot plugin entry point."""


# AstrBot 通过模块路径把处理器绑定到插件实例。处理器定义在实现模块，
# 入口类定义在当前模块，因此需要将处理器归属调整到运行时入口模块。
_IMPLEMENTATION_MODULE = SteamStatusMonitorV3.__module__
for _handler in star_handlers_registry:
    if _handler.handler_module_path == _IMPLEMENTATION_MODULE:
        _handler.handler_module_path = __name__


__all__ = ["Main", "SteamStatusMonitorV3"]
