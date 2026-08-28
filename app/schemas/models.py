"""API 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import MediaType, ProviderKind, SubscribeStatus


class ORMModel(BaseModel):
    """允许从 ORM 对象构造。"""

    model_config = ConfigDict(from_attributes=True)


# ---------------- 通用 ----------------
class Message(BaseModel):
    success: bool = True
    message: str = "ok"
    data: Any | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    is_superuser: bool = False


class LoginRequest(BaseModel):
    username: str
    password: str


# ---------------- 搜索 ----------------
class SearchRequest(BaseModel):
    keyword: str = Field(min_length=1, description="搜索关键词")
    media_type: MediaType | None = None
    season: int | None = None
    episode: int | None = None
    resolutions: list[str] = Field(default_factory=list)
    qualities: list[str] = Field(default_factory=list)
    include: str | None = None
    exclude: str | None = None
    min_seeders: int = 0
    sites: list[str] = Field(default_factory=list)
    allow_pan: bool = True
    allow_torrent: bool = True


class ResourceOut(BaseModel):
    title: str
    link: str
    site: str = ""
    kind: str = ""
    page_url: str | None = None
    description: str | None = None
    size: int = 0
    seeders: int = 0
    leechers: int = 0
    publish_at: str | None = None
    password: str | None = None
    score: float = 0
    unique_key: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------- 订阅 ----------------
class SubscribeCreate(BaseModel):
    title: str = Field(min_length=1)
    media_type: MediaType = MediaType.TV
    year: int | None = None
    tmdb_id: int | None = None
    season: int = 1
    total_episodes: int = 0
    start_episode: int = 1
    quality: str | None = None
    resolution: str | None = None
    effect: str | None = None
    include: str | None = None
    exclude: str | None = None
    min_seeders: int = 0
    sources: list[str] = Field(default_factory=list)
    allow_pan: bool = True
    allow_torrent: bool = True
    best_version: bool = False
    save_path: str | None = None
    note: str | None = None


class SubscribeUpdate(BaseModel):
    status: SubscribeStatus | None = None
    total_episodes: int | None = None
    start_episode: int | None = None
    quality: str | None = None
    resolution: str | None = None
    effect: str | None = None
    include: str | None = None
    exclude: str | None = None
    min_seeders: int | None = None
    sources: list[str] | None = None
    allow_pan: bool | None = None
    allow_torrent: bool | None = None
    best_version: bool | None = None
    save_path: str | None = None
    note: str | None = None


class SubscribeOut(ORMModel):
    id: int
    title: str
    year: int | None = None
    media_type: str
    tmdb_id: int | None = None
    season: int
    total_episodes: int
    start_episode: int
    downloaded_episodes: list[int] = Field(default_factory=list)
    lack_episodes: int
    status: str
    quality: str | None = None
    resolution: str | None = None
    include: str | None = None
    exclude: str | None = None
    sources: list[str] = Field(default_factory=list)
    allow_pan: bool = True
    allow_torrent: bool = True
    best_version: bool = False
    save_path: str | None = None
    note: str | None = None
    last_check_at: datetime | None = None
    last_matched_at: datetime | None = None
    error: str | None = None
    created_at: datetime | None = None


# ---------------- 站点 ----------------
class SiteCreate(BaseModel):
    name: str = Field(min_length=1)
    kind: ProviderKind = ProviderKind.INDEXER
    provider: str = Field(min_length=1)
    url: str = ""
    api_key: str | None = None
    username: str | None = None
    password: str | None = None
    cookie: str | None = None
    enabled: bool = True
    priority: int = 50
    timeout: int = 25
    options: dict[str, Any] = Field(default_factory=dict)


class SiteUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    api_key: str | None = None
    username: str | None = None
    password: str | None = None
    cookie: str | None = None
    enabled: bool | None = None
    priority: int | None = None
    timeout: int | None = None
    options: dict[str, Any] | None = None


class SiteOut(ORMModel):
    id: int
    name: str
    kind: str
    provider: str
    url: str
    enabled: bool
    priority: int
    timeout: int
    options: dict[str, Any] = Field(default_factory=dict)
    last_status: str | None = None
    last_check_at: datetime | None = None
    has_credentials: bool = False


# ---------------- 下载 ----------------
class DownloadRequest(BaseModel):
    title: str
    link: str
    kind: str = "torrent"
    site: str | None = None
    size: int = 0
    password: str | None = None
    page_url: str | None = None
    subscribe_id: int | None = None
    save_path: str | None = None
    downloader: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class DownloadTaskOut(ORMModel):
    id: int
    subscribe_id: int | None = None
    title: str
    kind: str
    site: str | None = None
    downloader: str | None = None
    external_id: str | None = None
    save_path: str | None = None
    status: str
    progress: float
    size: int
    speed: int
    eta: int
    media_type: str
    season: int | None = None
    episodes: list[int] = Field(default_factory=list)
    error: str | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------- 媒体 ----------------
class MediaOut(BaseModel):
    tmdb_id: int | None = None
    title: str
    original_title: str | None = None
    year: int | None = None
    media_type: str
    overview: str | None = None
    poster: str | None = None
    backdrop: str | None = None
    vote_average: float | None = None
    genres: list[str] = Field(default_factory=list)
    total_seasons: int | None = None


class TransferRequest(BaseModel):
    source: str = Field(min_length=1, description="源文件或目录")
    title: str | None = None
    season: int | None = None
    mode: str | None = None
    library_dir: str | None = None
    overwrite: bool = False
    dry_run: bool = False


class TransferResultOut(BaseModel):
    success: bool
    source: str
    target: str | None = None
    mode: str
    message: str
    size: int = 0
    meta: dict[str, Any] | None = None


# ---------------- 定时任务 ----------------
class ScheduleUpdate(BaseModel):
    """内置定时任务的触发规则（仅提交需要修改的字段）。"""

    enabled: bool | None = None
    trigger: str | None = Field(default=None, description="interval 或 cron")
    minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    cron: str | None = Field(default=None, description="5 段 cron，如 0 4 * * *")


# ---------------- 网盘管理 ----------------
class PanSaveRequest(BaseModel):
    """转存一个分享链接。"""

    share_url: str = Field(description="网盘分享链接")
    site_id: int | None = Field(default=None, description="指定网盘站点，留空自动选择")
    password: str | None = Field(default=None, description="提取码")
    target_dir: str | None = Field(default=None, description="落地目录，留空用网盘默认")
    task_id: int | None = Field(default=None, description="关联的下载任务，转存成功后推进其状态")


class PanMkdirRequest(BaseModel):
    """创建网盘目录。"""

    site_id: int
    path: str = Field(min_length=1)


# ---------------- ChatOps 机器人 ----------------
class ChatOpsConfigUpdate(BaseModel):
    """更新 ChatOps 配置。所有字段可选，只更新提交过的键。"""

    enabled: bool | None = Field(default=None, description="总开关")
    auto_download: bool | None = Field(default=None, description="搜索后是否自动下第一个")
    result_limit: int | None = Field(default=None, ge=1, le=20, description="搜索结果回复条数")
    allow_users: list[str] | None = Field(default=None, description="白名单用户 ID，空表示不限制")
    platforms: dict[str, Any] | None = Field(default=None, description="各平台密钥配置")


class ChatOpsTestRequest(BaseModel):
    """在界面上模拟一条聊天指令。"""

    text: str = Field(min_length=1, description="指令文本，如 搜索 沙丘")
    platform: str | None = Field(default=None, description="模拟的平台，默认 console")
    user_id: str | None = Field(default=None, description="模拟的用户 ID")


# ---------------- 插件 ----------------
class PluginConfigUpdate(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class PluginActionRequest(BaseModel):
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
