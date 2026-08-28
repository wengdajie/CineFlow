"""ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin
from app.schemas.enums import (
    MediaType,
    ProviderKind,
    ResourceKind,
    SubscribeStatus,
    TaskStatus,
)


class User(IdMixin, TimestampMixin, Base):
    """本地用户。"""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MediaItem(IdMixin, TimestampMixin, Base):
    """媒体条目（影片/剧集主体）。"""

    __tablename__ = "media_items"
    __table_args__ = (UniqueConstraint("media_type", "tmdb_id", name="uq_media_tmdb"),)

    title: Mapped[str] = mapped_column(String(255), index=True)
    original_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    media_type: Mapped[str] = mapped_column(String(16), default=MediaType.UNKNOWN.value)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    imdb_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tvdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    overview: Mapped[str | None] = mapped_column(Text, nullable=True)
    poster: Mapped[str | None] = mapped_column(String(512), nullable=True)
    backdrop: Mapped[str | None] = mapped_column(String(512), nullable=True)
    vote_average: Mapped[float | None] = mapped_column(Float, nullable=True)
    genres: Mapped[list[str]] = mapped_column(JSON, default=list)
    total_seasons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    subscribes: Mapped[list[Subscribe]] = relationship(
        back_populates="media", cascade="all, delete-orphan"
    )
    files: Mapped[list[LibraryFile]] = relationship(
        back_populates="media", cascade="all, delete-orphan"
    )


class Subscribe(IdMixin, TimestampMixin, Base):
    """订阅（追剧/追新的核心实体）。"""

    __tablename__ = "subscribes"
    __table_args__ = (
        UniqueConstraint("title", "year", "season", "media_type", name="uq_subscribe"),
    )

    media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_items.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), index=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), default=MediaType.TV.value)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    season: Mapped[int] = mapped_column(Integer, default=1)
    total_episodes: Mapped[int] = mapped_column(Integer, default=0)
    start_episode: Mapped[int] = mapped_column(Integer, default=1)
    downloaded_episodes: Mapped[list[int]] = mapped_column(JSON, default=list)
    lack_episodes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(16), default=SubscribeStatus.ACTIVE.value, index=True
    )
    # 过滤策略
    quality: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    effect: Mapped[str | None] = mapped_column(String(64), nullable=True)
    include: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exclude: Mapped[str | None] = mapped_column(String(255), nullable=True)
    min_seeders: Mapped[int] = mapped_column(Integer, default=0)
    sources: Mapped[list[str]] = mapped_column(JSON, default=list)  # 限定站点/网盘
    allow_pan: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_torrent: Mapped[bool] = mapped_column(Boolean, default=True)
    best_version: Mapped[bool] = mapped_column(Boolean, default=False)
    save_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_matched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    media: Mapped[MediaItem | None] = relationship(back_populates="subscribes")
    tasks: Mapped[list[DownloadTask]] = relationship(back_populates="subscribe")


class SiteConfig(IdMixin, TimestampMixin, Base):
    """站点 / Provider 配置。"""

    __tablename__ = "sites"

    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    kind: Mapped[str] = mapped_column(
        String(24), default=ProviderKind.INDEXER.value, index=True
    )
    provider: Mapped[str] = mapped_column(String(64))  # torznab / rss / pansou / qb ...
    url: Mapped[str] = mapped_column(String(512), default="")
    api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cookie: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=50)
    timeout: Mapped[int] = mapped_column(Integer, default=25)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_status: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ResourceRecord(IdMixin, TimestampMixin, Base):
    """搜索到的候选资源（去重/命中缓存）。"""

    __tablename__ = "resources"
    __table_args__ = (UniqueConstraint("unique_key", name="uq_resource_key"),)

    unique_key: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16), default=ResourceKind.TORRENT.value)
    site: Mapped[str] = mapped_column(String(128), index=True)
    link: Mapped[str] = mapped_column(Text)
    page_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    seeders: Mapped[int] = mapped_column(Integer, default=0)
    leechers: Mapped[int] = mapped_column(Integer, default=0)
    publish_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), default=MediaType.UNKNOWN.value)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episodes: Mapped[list[int]] = mapped_column(JSON, default=list)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quality: Mapped[str | None] = mapped_column(String(64), nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String(64), nullable=True)
    release_group: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DownloadTask(IdMixin, TimestampMixin, Base):
    """下载任务。"""

    __tablename__ = "download_tasks"

    subscribe_id: Mapped[int | None] = mapped_column(
        ForeignKey("subscribes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16), default=ResourceKind.TORRENT.value)
    site: Mapped[str | None] = mapped_column(String(128), nullable=True)
    link: Mapped[str] = mapped_column(Text)
    downloader: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    save_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=TaskStatus.PENDING.value, index=True
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    speed: Mapped[int] = mapped_column(BigInteger, default=0)
    eta: Mapped[int] = mapped_column(Integer, default=0)
    media_type: Mapped[str] = mapped_column(String(16), default=MediaType.UNKNOWN.value)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episodes: Mapped[list[int]] = mapped_column(JSON, default=list)
    tmdb_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    subscribe: Mapped[Subscribe | None] = relationship(back_populates="tasks")


class TransferRecord(IdMixin, TimestampMixin, Base):
    """整理（转移/硬链/STRM）记录，支持回溯与重试。"""

    __tablename__ = "transfer_records"

    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("download_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_path: Mapped[str] = mapped_column(Text)
    target_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="link")
    success: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)


class LibraryFile(IdMixin, TimestampMixin, Base):
    """媒体库已入库文件索引（用于去重与缺集计算）。"""

    __tablename__ = "library_files"
    __table_args__ = (UniqueConstraint("path", name="uq_library_path"),)

    media_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_items.id", ondelete="CASCADE"), nullable=True, index=True
    )
    path: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(255), index=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_type: Mapped[str] = mapped_column(String(16), default=MediaType.UNKNOWN.value)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)

    media: Mapped[MediaItem | None] = relationship(back_populates="files")


class PluginState(IdMixin, TimestampMixin, Base):
    """插件安装状态与配置。"""

    __tablename__ = "plugins"

    plugin_id: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32), default="1.0.0")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str | None] = mapped_column(String(128), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class NotificationRecord(IdMixin, TimestampMixin, Base):
    """通知/消息记录。"""

    __tablename__ = "notifications"

    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="info", index=True)
    event: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    channel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SystemSetting(IdMixin, TimestampMixin, Base):
    """运行期可变设置（覆盖静态配置）。"""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    value: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AuditLog(IdMixin, TimestampMixin, Base):
    """操作审计：谁在什么时候通过哪个渠道下了什么指令。

    主要用于 ChatOps——机器人可以被多人使用，必须能追溯是谁下的指令。
    Web 界面的写操作也可以复用这张表。
    """

    __tablename__ = "audit_logs"

    #: 来源，如 ``chatops.feishu`` / ``web`` / ``api``
    source: Mapped[str] = mapped_column(String(64), index=True)
    #: 操作者展示名
    actor: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 操作者在该平台的唯一 ID
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    #: 规范化后的动作，如 ``search`` / ``download``
    action: Mapped[str] = mapped_column(String(64), index=True)
    #: 动作目标（关键词、序号、链接等）
    target: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 原始指令文本
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    #: 回复/执行结果摘要
    result: Mapped[str | None] = mapped_column(Text, nullable=True)


class PanSaveRecord(IdMixin, TimestampMixin, Base):
    """网盘转存记录，便于回溯「哪个分享转存到了哪里」。"""

    __tablename__ = "pan_saves"

    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("download_tasks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    storage: Mapped[str] = mapped_column(String(128), index=True)
    share_url: Mapped[str] = mapped_column(Text)
    password: Mapped[str | None] = mapped_column(String(32), nullable=True)
    saved_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class SearchHistory(IdMixin, TimestampMixin, Base):
    """搜索历史。"""

    __tablename__ = "search_history"

    keyword: Mapped[str] = mapped_column(String(255), index=True)
    media_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    sites: Mapped[list[str]] = mapped_column(JSON, default=list)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0)

