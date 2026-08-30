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
    UserRole,
)


class User(IdMixin, TimestampMixin, Base):
    """本地用户。"""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    #: 角色：admin（全权）/ operator（可订阅下载，不可改系统配置）/ viewer（只读）
    #: 保留 ``is_superuser`` 是为了兼容老库与已签发的 JWT，两者由 role 推导保持一致
    role: Mapped[str] = mapped_column(String(16), default=UserRole.ADMIN.value, index=True)
    #: 备注（给家人开号时标记这是谁）
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


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
    #: 引用的过滤规则组（有序偏好），为空表示只用全局评分
    rule_group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
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
    #: 入库时该版本的质量评分，洗版时用它与新资源比较
    quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    #: 已洗版次数，用尽 CF_UPGRADE_MAX_TIMES 后不再替换（防止无限横跳）
    upgrade_count: Mapped[int] = mapped_column(Integer, default=0)

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
class PanSubscribe(IdMixin, TimestampMixin, Base):
    """网盘分享追更订阅。

    对标 quark-auto-save 的核心任务模型：盯住一个**会持续更新**的分享链接
    （如整季连载的剧集），每次巡检只转存新增文件，并可用正则过滤与重命名。
    与 ``subscribes`` 表的区别：那个是「按片名去各站搜」，
    这个是「盯死一个已知分享链接」。
    """

    __tablename__ = "pan_subscribes"

    name: Mapped[str] = mapped_column(String(255), index=True)
    share_url: Mapped[str] = mapped_column(Text)
    password: Mapped[str | None] = mapped_column(String(32), nullable=True)
    #: 目标网盘站点；为空表示按分享链接自动挑同家网盘
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True
    )
    #: 转存落地目录
    target_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 只转存匹配此正则的文件名（空=全部）
    include_regex: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 排除匹配此正则的文件名
    exclude_regex: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 转存后重命名：正则 + 替换模板
    rename_search: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rename_replace: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=SubscribeStatus.ACTIVE.value, index=True
    )
    #: 已转存过的文件名集合，用于增量判断（避免重复转存）
    saved_files: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: 连续失败次数，达到阈值后自动标记失效
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    #: 分享是否已失效（失效后巡检直接跳过，不再浪费请求）
    invalid: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: 累计转存文件数
    total_saved: Mapped[int] = mapped_column(Integer, default=0)
    #: 到期时间，过期后不再执行（对标 quark-auto-save 的「任务结束期限」）
    expire_at: Mapped[datetime | None] = mapped_column(nullable=True)
    #: 仅在这些星期几执行（0=周一 … 6=周日；空=每天）
    weekdays: Mapped[list[int]] = mapped_column(JSON, default=list)


class StrmRecord(IdMixin, TimestampMixin, Base):
    """已生成的 STRM 文件索引。

    存在的意义是**增量与清理**：知道每个 STRM 对应网盘上的哪个文件，
    才能判断哪些是新增（要生成）、哪些源文件已消失（要清理失效 STRM）。
    """

    __tablename__ = "strm_records"
    __table_args__ = (UniqueConstraint("strm_path", name="uq_strm_path"),)

    #: 本地 .strm 文件路径
    strm_path: Mapped[str] = mapped_column(Text)
    #: 网盘站点
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), nullable=True, index=True
    )
    #: 网盘内的源文件路径
    source_path: Mapped[str] = mapped_column(Text, index=True)
    #: 网盘文件 ID（部分网盘用 ID 换直链比用路径更稳）
    file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    #: 写入 STRM 的链接形式：direct / proxy
    link_mode: Mapped[str] = mapped_column(String(16), default="proxy")
    #: 上次校验时源文件是否仍存在
    alive: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(nullable=True)


class SiteHealthRecord(IdMixin, TimestampMixin, Base):
    """站点健康探测记录。

    为什么单独一张表而不是只更新 ``sites.last_status``：
    Cookie 过期最典型的表现是**静默返回 0 条结果**而不是报错，
    要判断"是真没有资源还是站点挂了"必须看**历史趋势**，
    所以每次探测都留一条，界面上能看出"从哪天开始不行的"。
    """

    __tablename__ = "site_health"

    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), nullable=True, index=True
    )
    site_name: Mapped[str] = mapped_column(String(128), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="", index=True)
    provider: Mapped[str] = mapped_column(String(64), default="")
    #: ok / degraded / down
    status: Mapped[str] = mapped_column(String(16), default="ok", index=True)
    #: 探测耗时，用于发现"能连但极慢"的站点
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    #: 探测搜索返回的结果条数（0 结果 + 无报错 = 疑似 Cookie 过期）
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class RankingRule(IdMixin, TimestampMixin, Base):
    """榜单自动订阅规则。

    对标 MoviePilot 的「榜单订阅」：把某个榜单（TMDB 热门/高分/趋势）
    的前 N 项自动变成订阅，免得每部剧都手动加。
    """

    __tablename__ = "ranking_rules"
    __table_args__ = (UniqueConstraint("name", name="uq_ranking_rule_name"),)

    name: Mapped[str] = mapped_column(String(128), index=True)
    #: 榜单来源：tmdb_trending / tmdb_popular / tmdb_top_rated / local_trending
    source: Mapped[str] = mapped_column(String(32), default="tmdb_trending", index=True)
    media_type: Mapped[str] = mapped_column(String(16), default=MediaType.TV.value)
    #: 取榜单前多少项参与匹配
    limit: Mapped[int] = mapped_column(Integer, default=10)
    #: 最低评分门槛（TMDB vote_average），0 表示不限
    min_vote: Mapped[float] = mapped_column(Float, default=0.0)
    #: 只要这些年份之后的（避免把老片全刷进来）
    min_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 标题包含/排除关键词
    include: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exclude: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 创建订阅时套用的过滤条件（resolution/quality/include/exclude 等）
    subscribe_defaults: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    #: 已由本规则创建过的 tmdb_id，避免用户删掉订阅后又被自动加回来
    handled_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_count: Mapped[int] = mapped_column(Integer, default=0)


class VideoSubscribe(IdMixin, TimestampMixin, Base):
    """网页视频订阅（UP 主 / YouTube 频道 / 播放列表 更新自动下载）。

    **补的是哪个洞**：v1.6.0 起能用 yt-dlp **下载**单个视频，v1.7.0 起能**搜到**
    B 站/YouTube 的视频，但一直不能"**追**"——用户关注的 UP 主更新了，
    仍要自己去看、自己贴链接。这是 ``subscribes``（按片名去各站搜）和
    ``pan_subscribes``（盯死一个分享链接）都覆盖不到的第三种追更形态。

    与另两张订阅表的关键区别是**增量的判定依据**：
    ``pan_subscribes`` 用文件名，``subscribes`` 用集号，
    而这里必须用**视频 ID**（B 站 BV 号 / YouTube videoId）——因为 yt-dlp 的
    扁平提取对 B 站**不返回标题也不返回上传日期**（实测 title/upload_date 均为
    None），只有 ID 是稳定可得的。用标题去重会把所有条目当成同一个。
    """

    __tablename__ = "video_subscribes"

    name: Mapped[str] = mapped_column(String(255), index=True)
    #: UP 主空间页 / 频道页 / 播放列表地址，交给 yt-dlp 做扁平提取
    url: Mapped[str] = mapped_column(Text)
    #: 展示用的来源标记（bilibili / youtube / …），由地址推断
    site: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 落地目录，为空则用全局下载目录
    save_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 标题包含/排除正则（只追某个系列时很有用）
    include_regex: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exclude_regex: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: 每次巡检最多取列表里前多少个（UP 主主页动辄上千投稿，不能全量拉）
    check_limit: Mapped[int] = mapped_column(Integer, default=10)
    #: 单次巡检最多下载几个，防止首次订阅就一口气下几十个
    max_per_run: Mapped[int] = mapped_column(Integer, default=3)
    #: 指定画质上限（如 1080），为空按下载器配置自动挑
    max_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), default=SubscribeStatus.ACTIVE.value, index=True
    )
    #: 已处理过的视频 ID（增量判据，见类文档）
    handled_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    #: 首次订阅时是否跳过历史投稿（只追之后的新作），默认跳过：
    #: 否则加一个十年老 UP 会瞬间投出几十个下载任务
    skip_existing: Mapped[bool] = mapped_column(Boolean, default=True)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_downloaded: Mapped[int] = mapped_column(Integer, default=0)


class FilterRuleGroup(IdMixin, TimestampMixin, Base):
    """自定义过滤规则组（优先级规则）。

    对标 MoviePilot / nexus-media 的「规则组」：用户把
    "先要 4K REMUX，没有就要 4K WEB-DL，再没有才要 1080p"
    这种**有序偏好**表达出来，而不是只能调一个全局评分权重。
    订阅可以引用某个规则组（``subscribes.rule_group_id``）。
    """

    __tablename__ = "filter_rule_groups"
    __table_args__ = (UniqueConstraint("name", name="uq_rule_group_name"),)

    name: Mapped[str] = mapped_column(String(128), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 有序规则层级；每层是一组条件，命中靠前层的资源优先
    #: [{"name":"4K REMUX","resolution":"2160p","quality":"REMUX","min_seeders":1}, ...]
    levels: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    #: 任何层都不命中时是否仍然接受（False = 只下命中规则的资源）
    accept_unmatched: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    #: 是否为默认规则组（新订阅自动套用）
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

