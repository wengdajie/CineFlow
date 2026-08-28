"""业务异常。"""

from __future__ import annotations


class CineFlowError(Exception):
    """基础异常。"""

    status_code = 400

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class NotFoundError(CineFlowError):
    status_code = 404


class ConflictError(CineFlowError):
    status_code = 409


class ProviderError(CineFlowError):
    """外部服务（索引器/下载器/网盘）调用失败。"""

    status_code = 502


class ConfigError(CineFlowError):
    status_code = 400
