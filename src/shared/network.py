"""插件网络客户端的 TLS 与代理公共配置。"""
import os
import ssl


_ssl_ca_file = ""
_ssl_context = None


def configure_tls(ssl_ca_file=None):
    """配置可信自定义 CA；空值表示继续使用系统默认信任链。"""
    global _ssl_ca_file, _ssl_context
    raw_path = str(ssl_ca_file or "").strip()
    path = os.path.abspath(os.path.expanduser(raw_path)) if raw_path else ""
    if path and not os.path.isfile(path):
        raise ValueError(f"自定义 CA 文件不存在: {path}")
    _ssl_ca_file = path
    _ssl_context = ssl.create_default_context(cafile=path) if path else None


def get_ssl_ca_file():
    return _ssl_ca_file


def httpx_client_kwargs(proxy=None):
    kwargs = {"proxy": proxy}
    if _ssl_context is not None:
        kwargs["verify"] = _ssl_context
    return kwargs


def aiohttp_connector():
    import aiohttp

    return aiohttp.TCPConnector(ssl=_ssl_context) if _ssl_context is not None else None


def requests_verify():
    return _ssl_ca_file or True
