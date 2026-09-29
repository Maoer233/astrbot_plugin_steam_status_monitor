"""外部价格/商店 provider 的结构化错误。"""


class ProviderError(RuntimeError):
    """可安全传递到应用层的外部服务错误。"""

    def __init__(self, code: str, message: str = "", *, status_code: int | None = None, retryable: bool = False):
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message or code)
