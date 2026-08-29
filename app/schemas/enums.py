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
    WEBVIDEO = "webvideo" # 视频网页（由 yt-dlp 解析，如 B 站/YouTube 公开视频）


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
    PAN = "pan"                 # 盘搜（找分享链接）
    PANSTORAGE = "panstorage"   # 网盘存储（转存/浏览自己的网盘）
    DOWNLOADER = "downloader"
    MEDIASERVER = "mediaserver"
    NOTIFIER = "notifier"
    METADATA = "metadata"


class UserRole(StrEnum):
    """用户角色。

    只分三档、刻意不做细粒度 ACL：家用场景真实需求是
    "我自己全权 / 家人能点订阅 / 客人只能看"，
    再细就只会让人配不明白（ADR-19）。
    """

    ADMIN = "admin"        # 全权：改配置、管站点、管用户
    OPERATOR = "operator"  # 可搜索/订阅/下载/整理，不能改系统配置与用户
    VIEWER = "viewer"      # 只读：能看，不能改任何东西

    @property
    def rank(self) -> int:
        """权限等级，数字越大权限越高（用于 ``>=`` 比较）。"""
        return {"viewer": 1, "operator": 2, "admin": 3}[self.value]


class SiteHealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"   # 能连通但结果异常（如 0 结果、极慢）
    DOWN = "down"           # 连不通/报错


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
    PAN_SAVED = "pan.saved"
    PAN_SAVE_FAILED = "pan.save_failed"
    CHAT_COMMAND = "chat.command"
    SITE_UNHEALTHY = "site.unhealthy"
    SITE_RECOVERED = "site.recovered"
    RANKING_SUBSCRIBED = "ranking.subscribed"
    PLUGIN_ACTION = "plugin.action"
    SYSTEM_ERROR = "system.error"
