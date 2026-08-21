"""统一使用 AstrBot 日志，并在输出前清理敏感信息。"""
import logging
import re
from urllib.parse import urlsplit, urlunsplit

try:
    from astrbot.api import logger as _astrbot_logger
except ImportError:  # 允许在未安装 AstrBot 的开发环境运行单元测试
    _astrbot_logger = logging.getLogger("steam_status_monitor")


_SENSITIVE_QUERY = re.compile(
    r"(?i)([?&](?:key|api_?key|apikey|token|access_token|secret|password)=)([^&#\s]+)"
)
_BEARER = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)(\S+)")
_registered_secrets = set()


def register_sensitive_values(*values):
    """注册运行时密钥，确保它们不会出现在后续日志中。"""
    for value in values:
        if value:
            text = str(value)
            if len(text) >= 4:
                _registered_secrets.add(text)


def redact_sensitive(value):
    """清理 URL 查询密钥、Bearer Token、代理认证及已注册密钥。"""
    text = str(value)
    text = _SENSITIVE_QUERY.sub(r"\1******", text)
    text = _BEARER.sub(r"\1******", text)
    try:
        parsed = urlsplit(text)
        if parsed.hostname and parsed.username is not None:
            host = parsed.hostname
            if parsed.port:
                host = f"{host}:{parsed.port}"
            text = urlunsplit((parsed.scheme, f"******:******@{host}", parsed.path, parsed.query, parsed.fragment))
    except (TypeError, ValueError):
        pass
    for secret in sorted(_registered_secrets, key=len, reverse=True):
        text = text.replace(secret, "******")
    return text


class RedactingLogger:
    def __init__(self, delegate):
        self._delegate = delegate

    def _log(self, level, message, *args, **kwargs):
        clean_args = tuple(redact_sensitive(arg) for arg in args)
        return getattr(self._delegate, level)(redact_sensitive(message), *clean_args, **kwargs)

    def debug(self, message, *args, **kwargs):
        return self._log("debug", message, *args, **kwargs)

    def info(self, message, *args, **kwargs):
        return self._log("info", message, *args, **kwargs)

    def warning(self, message, *args, **kwargs):
        return self._log("warning", message, *args, **kwargs)

    warn = warning

    def error(self, message, *args, **kwargs):
        return self._log("error", message, *args, **kwargs)

    def exception(self, message, *args, **kwargs):
        method = getattr(self._delegate, "exception", None)
        if method is not None:
            clean_args = tuple(redact_sensitive(arg) for arg in args)
            return method(redact_sensitive(message), *clean_args, **kwargs)
        kwargs["exc_info"] = True
        return self._log("error", message, *args, **kwargs)


logger = RedactingLogger(_astrbot_logger)
