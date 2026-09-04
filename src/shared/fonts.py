"""统一字体路径解析：bundled → 数据目录 → 系统字体 → PIL 默认字体。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import ImageFont

from .logging import logger
from .paths import FONTS_DIR, REQUIRED_FONT_FILES

_logged_missing: set[str] = set()
_path_cache: dict[str, str | None] = {}
_data_fonts_dir = ""
_bundled_dir = str(FONTS_DIR)


def configure_font_resolver(data_dir: str, bundled_dir: str | None = None) -> None:
    """插件启动后注入数据目录；下载完成时再调 invalidate。"""
    global _data_fonts_dir, _bundled_dir
    _data_fonts_dir = os.path.join(data_dir, "fonts") if data_dir else ""
    if bundled_dir:
        _bundled_dir = bundled_dir
    invalidate()


def invalidate() -> None:
    _path_cache.clear()
    _logged_missing.clear()


def bundled_fonts_complete(bundled_dir: str | None = None) -> bool:
    root = Path(bundled_dir or _bundled_dir)
    return all((root / name).is_file() for name in REQUIRED_FONT_FILES)


def _system_font_candidates(name: str, bold: bool = False) -> list[str]:
    lower = name.lower()
    want_bold = bold or "bold" in lower or "medium" in lower
    if sys.platform.startswith("win"):
        windir = os.environ.get("WINDIR", r"C:\Windows")
        fonts = os.path.join(windir, "Fonts")
        return [
            os.path.join(fonts, "msyhbd.ttc" if want_bold else "msyh.ttc"),
            os.path.join(fonts, "msyh.ttc"),
        ]
    if sys.platform == "darwin":
        return [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    return [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if want_bold else "",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]


def _existing_file(path: str | None) -> str | None:
    if path and os.path.isfile(path):
        return path
    return None


def resolve_font_path(name: str, *, bold: bool = False) -> str | None:
    """返回可用字体路径；找不到时返回 None，不抛异常。"""
    if not name:
        name = "NotoSansHans-Regular.otf"
    if bold:
        name = "NotoSansHans-Medium.otf"
    cache_key = f"{name}|{int(bold)}"
    if cache_key in _path_cache:
        return _path_cache[cache_key]

    candidates = [
        os.path.join(_bundled_dir, name),
        os.path.join(_data_fonts_dir, name) if _data_fonts_dir else "",
    ]
    if sys.platform.startswith("win"):
        # Windows 大小写不敏感：数据目录里可能是手动拷贝的不同大小写
        if _data_fonts_dir and os.path.isdir(_data_fonts_dir):
            try:
                for entry in os.listdir(_data_fonts_dir):
                    if entry.lower() == name.lower():
                        candidates.append(os.path.join(_data_fonts_dir, entry))
            except OSError:
                pass

    found = None
    for path in candidates:
        found = _existing_file(path)
        if found:
            break
    if not found:
        _error_missing_once(name)
        for path in _system_font_candidates(name, bold=bold):
            found = _existing_file(path)
            if found:
                break
    _path_cache[cache_key] = found
    return found


def _error_missing_once(name: str) -> None:
    if name in _logged_missing:
        return
    _logged_missing.add(name)
    logger.error("[Font] 找不到字体文件 %s，卡片可能出现方块字", name)


def load_truetype(name: str, size: int, fallbacks: tuple[str, ...] | list[str] = ()):
    """按文件名加载字体；全部失败时回退 PIL 默认字体。"""
    names = [name, *list(fallbacks or ())]
    for font_name in names:
        if not font_name:
            continue
        path = font_name if os.path.isfile(font_name) else resolve_font_path(font_name)
        if not path:
            continue
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    for path in _system_font_candidates(name):
        if not _existing_file(path):
            continue
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    fallback_key = f"{name}|default"
    if fallback_key not in _logged_missing:
        _logged_missing.add(fallback_key)
        logger.error("[Font] 字体 %s 加载失败，已回退默认字体，卡片可能出现方块字", name)
    return ImageFont.load_default()
