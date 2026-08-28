"""QQ 官方机器人指令面板客户端（兼容转发层）。

优先复用独立插件 ``astrbot_plugin_qq_official_panel`` 提供的实现；
当该插件未安装时，回退到本地内置实现，保证插件可独立运行。
"""

from __future__ import annotations

try:
    from qq_official_panel import (  # type: ignore[import-not-found]
        QQOfficialPanelClient,
        QQOfficialPanelError,
    )
except ImportError:
    from .qqofficial_panel_local import (  # type: ignore[no-redef]
        QQOfficialPanelClient,
        QQOfficialPanelError,
    )

__all__ = ["QQOfficialPanelClient", "QQOfficialPanelError"]
