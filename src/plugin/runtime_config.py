import json
import subprocess
import sys

from ..infrastructure.clients.itad import ITADClient
from ..shared.logging import logger, register_sensitive_values
from ..shared.network import configure_tls
from ..shared.paths import CONFIG_PATH


def load_config_dict(config=None):
    """读 AstrBot 配置；空则回退本地 config.json。"""
    if config:
        return config
    try:
        with open(str(CONFIG_PATH), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"steam_status_monitor 配置读取失败: {e}")
        return {}


def migrate_legacy_steam_ids(config):
    if "steam_ids" in config and "group_steam_ids" not in config:
        steam_ids = config.get("steam_ids", [])
        if isinstance(steam_ids, str):
            steam_ids = [x.strip() for x in steam_ids.split(",") if x.strip()]
        config["group_steam_ids"] = {"default": steam_ids}
        config.pop("steam_ids", None)
        logger.info("已自动迁移旧 steam_ids 配置到 group_steam_ids['default']")
    return config


def parse_smart_poll_intervals(raw_intervals):
    if isinstance(raw_intervals, str):
        return [int(x.strip()) for x in raw_intervals.split(",") if x.strip()]
    return list(raw_intervals)


def ensure_socks_support(proxy):
    if not (proxy and str(proxy).startswith("socks")):
        return
    try:
        import socksio  # noqa: F401
    except ImportError:
        logger.info(f"[SteamStatusMonitor] 检测到 SOCKS 代理 ({proxy})，socksio 未安装，尝试自动安装...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "httpx[socks]", "-q"],
                timeout=60,
            )
            import socksio  # noqa: F401
            logger.info("[SteamStatusMonitor] socksio 自动安装成功")
        except Exception as ie:
            logger.error(
                f"[SteamStatusMonitor] socksio 自动安装失败: {ie}。"
                f"请手动执行: pip install httpx[socks]"
            )


def apply_runtime_config(plugin, config=None):
    """把配置写到插件运行时属性，不拉任务、不落盘。"""
    config = migrate_legacy_steam_ids(load_config_dict(config))
    plugin.config = config
    plugin.API_KEY = config.get("steam_api_key", "")
    register_sensitive_values(plugin.API_KEY, config.get("sgdb_api_key", ""))
    plugin.SSL_CA_FILE = config.get("ssl_ca_file", "")
    try:
        configure_tls(plugin.SSL_CA_FILE)
    except ValueError as exc:
        logger.error(f"TLS 配置无效，将使用系统默认信任链: {exc}")
        plugin.SSL_CA_FILE = ""
        configure_tls()
    plugin.STEAM_API_BASE = (config.get("steam_api_base", "") or "https://api.steampowered.com").rstrip("/")
    plugin.STEAM_STORE_BASE = (config.get("steam_store_base", "") or "https://store.steampowered.com").rstrip("/")
    plugin.SGDB_API_BASE = (config.get("sgdb_api_base", "") or "https://www.steamgriddb.com").rstrip("/")
    plugin.SGDB_API_KEY = config.get("sgdb_api_key", "")
    plugin.group_steam_ids = config.get("group_steam_ids", {})
    plugin.RETRY_TIMES = config.get("retry_times", 3)
    plugin.ENABLE_PROXY = config.get("enable_proxy", False)
    plugin.PROXY_URL = config.get("proxy_url", "")
    plugin.proxy = plugin.PROXY_URL if plugin.ENABLE_PROXY and plugin.PROXY_URL else None
    plugin.ITAD_CLIENT = ITADClient(
        config.get("itad_api_key", ""),
        proxy=plugin.proxy,
        base_url=config.get("itad_api_base", ""),
    )
    plugin.max_group_size = config.get("max_group_size", 20)
    plugin.GROUP_ID = None
    plugin.fixed_poll_interval = config.get("fixed_poll_interval", 0)
    plugin.poll_interval_mid_sec = config.get("poll_interval_mid_sec", 600)
    plugin.poll_interval_long_sec = config.get("poll_interval_long_sec", 1800)
    plugin.detailed_poll_log = config.get("detailed_poll_log", True)
    plugin.smart_poll_intervals = parse_smart_poll_intervals(
        config.get("smart_poll_intervals", "1,3,5,10,20,30")
    )
    config["smart_poll_intervals"] = ",".join(str(x) for x in plugin.smart_poll_intervals)
    plugin.max_achievement_notifications = config.get("max_achievement_notifications", 5)
    plugin.rank_push_hour = config.get("rank_push_hour", 8)
    plugin.rank_push_minute = config.get("rank_push_minute", 30)
    ensure_socks_support(plugin.proxy)
    return config


def apply_hot_update(plugin, key, value):
    """/steam set 热更新：改 config 并同步已暴露的运行时属性。"""
    if key not in plugin.config:
        return False, f"无效参数: {key}"
    old = plugin.config[key]
    if key == "smart_poll_intervals":
        value_list = parse_smart_poll_intervals(value)
        value = ",".join(str(x) for x in value_list)
        plugin.smart_poll_intervals = value_list
    elif isinstance(old, int):
        try:
            value = int(value)
        except Exception:
            return False, "类型错误，应为整数"
    elif isinstance(old, float):
        try:
            value = float(value)
        except Exception:
            return False, "类型错误，应为浮点数"
    elif isinstance(old, list):
        value = [int(x.strip()) for x in value.split(",") if x.strip()]
    plugin.config[key] = value
    plugin.API_KEY = plugin.config.get("steam_api_key", "")
    plugin.STEAM_IDS = plugin.config.get("steam_ids", [])
    plugin.RETRY_TIMES = plugin.config.get("retry_times", 3)
    plugin.GROUP_ID = plugin.config.get("notify_group_id", None)
    plugin.fixed_poll_interval = plugin.config.get("fixed_poll_interval", 0)
    plugin.smart_poll_intervals = parse_smart_poll_intervals(
        plugin.config.get("smart_poll_intervals", "1,3,5,10,20,30")
    )
    if hasattr(plugin.config, "save_config"):
        plugin.config.save_config()
    return True, f"已设置 {key} = {value}"
