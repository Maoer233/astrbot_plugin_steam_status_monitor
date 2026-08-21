"""AstrBot 插件兼容入口。

具体实现位于 ``src.plugin.steam_status_monitor``，根入口保留用于 AstrBot 插件发现。
"""

from .src.plugin.steam_status_monitor import SteamStatusMonitorV3


class Main(SteamStatusMonitorV3):
    """AstrBot plugin entry point."""


__all__ = ["Main", "SteamStatusMonitorV3"]
