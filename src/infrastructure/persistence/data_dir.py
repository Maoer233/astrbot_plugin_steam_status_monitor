"""插件数据目录：统一落到 AstrBot 规范的 data/plugin_data/<plugin_name>/。

- 新目录：优先用 AstrBot 的 StarTools.get_data_dir()，异常时回退
  get_astrbot_data_path()/plugin_data/<name>。
- 历史数据：旧版本曾把数据写在 data/steam_status_monitor/，升级后首次启动
  自动迁移一次（复制，保留旧目录作备份，不删除）。
"""
import os
import shutil
from pathlib import Path

from ...shared.logging import logger

# 与 metadata.yaml 的 name 保持一致（StarTools.get_data_dir 按 metadata.name 建目录）
PLUGIN_NAME = "steam_status_monitor_V3"
LEGACY_DIR_NAME = "steam_status_monitor"
MIGRATION_FLAG = ".migrated_plugin_data"


def _astrbot_data_path() -> Path:
    """AstrBot 的 data 目录（绝对）；不可用时回退 cwd/data。"""
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
        return Path(get_astrbot_data_path())
    except Exception:
        return Path(os.getcwd()) / "data"


def resolve_plugin_data_dir(plugin_name: str = PLUGIN_NAME) -> Path:
    """规范插件数据目录 data/plugin_data/<plugin_name>。"""
    try:
        from astrbot.core.star.star_tools import StarTools
        path = StarTools.get_data_dir(plugin_name)
        if path:
            return Path(path)
    except Exception as e:
        logger.warning(f"[数据目录] StarTools.get_data_dir 不可用，回退手动拼接: {e}")
    return _astrbot_data_path() / "plugin_data" / plugin_name


def legacy_data_dir() -> Path:
    """旧版本数据目录 data/steam_status_monitor/。"""
    return _astrbot_data_path() / LEGACY_DIR_NAME


def _copy_missing(src: Path, dst: Path) -> int:
    """递归复制 src 到 dst，只补齐 dst 中缺失的文件（不覆盖已存在）。返回复制文件数。"""
    copied = 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, str(src))
        target_root = dst if rel == "." else dst / rel
        target_root.mkdir(parents=True, exist_ok=True)
        for name in files:
            source = Path(root) / name
            target = target_root / name
            if target.exists():
                continue
            try:
                shutil.copy2(source, target)
                copied += 1
            except Exception as e:
                logger.warning(f"[数据迁移] 复制失败 {source} -> {target}: {e}")
    return copied


def ensure_plugin_data_dir(plugin_name: str = PLUGIN_NAME) -> Path:
    """返回规范数据目录，并在升级后首次启动时把旧目录数据迁移过来（仅一次）。"""
    new_dir = resolve_plugin_data_dir(plugin_name)
    try:
        new_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning(f"[数据目录] 创建 {new_dir} 失败，回退旧目录: {e}")
        legacy = legacy_data_dir()
        legacy.mkdir(parents=True, exist_ok=True)
        return legacy

    flag = new_dir / MIGRATION_FLAG
    if flag.exists():
        return new_dir

    legacy = legacy_data_dir()
    try:
        if legacy.exists() and legacy.resolve() != new_dir.resolve():
            copied = _copy_missing(legacy, new_dir)
            logger.info(
                f"[数据迁移] 已从旧目录 {legacy} 迁移 {copied} 个文件到 {new_dir}（旧目录保留作备份）"
            )
    except Exception as e:
        logger.warning(f"[数据迁移] 迁移旧数据失败（旧目录保留，继续启动）: {e}")
    try:
        flag.write_text("1", encoding="utf-8")
    except Exception as e:
        logger.warning(f"[数据迁移] 写迁移标记失败: {e}")
    return new_dir
