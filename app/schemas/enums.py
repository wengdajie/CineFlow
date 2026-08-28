"""全局枚举。"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """字符串枚举（兼容 Python 3.10）。"""

    def __str__(self) -> str:  # pragma: no cover
        return str(self.value)


class MediaType(StrEnum):
    MOVIE = "movie"
    TV = "tv"
    ANIME = "anime"
    UNKNOWN = "unknown"


class ResourceKind(StrEnum):
    """资源载体类型。"""

    TORRENT = "torrent"   # BT 站点/Torznab/RSS
    MAGNET = "magnet"
    PAN = "pan"           # 网盘分享链接（盘搜）
    DIRECT = "direct"     # 直链


class SubscribeStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    TRANSFERRED = "transferred"
    FAILED = "failed"
    CANCELED = "canceled"


class TransferMode(StrEnum):
    LINK = "link"
    COPY = "copy"
    MOVE = "move"
    SOFTLINK = "softlink"
    STRM = "strm"


class ProviderKind(StrEnum):
    INDEXER = "indexer"
    PAN = "pan"
    DOWNLOADER = "downloader"
    MEDIASERVER = "mediaserver"
    NOTIFIER = "notifier"
    METADATA = "metadata"


class NotifyLevel(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class EventType(StrEnum):
    SUBSCRIBE_ADDED = "subscribe.added"
    SUBSCRIBE_COMPLETED = "subscribe.completed"
    RESOURCE_MATCHED = "resource.matched"
    DOWNLOAD_ADDED = "download.added"
    DOWNLOAD_COMPLETED = "download.completed"
    TRANSFER_COMPLETED = "transfer.completed"
    TRANSFER_FAILED = "transfer.failed"
    LIBRARY_REFRESHED = "library.refreshed"
    PLUGIN_ACTION = "plugin.action"
    SYSTEM_ERROR = "system.error"
