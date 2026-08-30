"""API 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import MediaType, ProviderKind, SubscribeStatus, UserRole


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
    role: str = UserRole.ADMIN.value


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
    rule_group_id: int | None = Field(default=None, description="绑定的过滤规则组，留空用全局默认组")
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
    rule_group_id: int | None = None
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
    rule_group_id: int | None = None
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


class PanRenameRequest(BaseModel):
    """重命名网盘文件/目录。"""

    site_id: int
    path: str = Field(min_length=1, description="待改名的完整路径")
    new_name: str = Field(min_length=1, description="新名称（不含路径分隔符）")
    file_id: str | None = Field(default=None, description="已知文件 ID 可跳过路径解析，更快")


class PanMoveRequest(BaseModel):
    """移动或复制网盘文件/目录。"""

    site_id: int
    path: str = Field(min_length=1, description="源路径")
    target_dir: str = Field(min_length=1, description="目标目录")
    file_id: str | None = Field(default=None, description="已知文件 ID 可跳过路径解析")
    # 字段名不能直接叫 copy —— 会遮蔽 BaseModel.copy 并触发 pydantic 警告。
    # 用 alias 保持请求体字段仍是 "copy"，对前端与 API 完全无感。
    copy_mode: bool = Field(
        default=False, alias="copy", description="true=复制，false=移动"
    )

    model_config = ConfigDict(populate_by_name=True)


# ---------------- 网盘登录 ----------------
class PanLoginStartRequest(BaseModel):
    """开始一次扫码登录。"""

    provider: str = Field(description="pan115 / baidu")


class PanLoginCompleteRequest(BaseModel):
    """扫码成功后把凭据落库。"""

    token: str = Field(min_length=1, description="扫码会话 token")
    site_id: int | None = Field(default=None, description="写入已有站点；留空则自动创建")
    site_name: str | None = Field(default=None, description="自动创建时的站点名")


class PanCookieImportRequest(BaseModel):
    """导入 Cookie（夸克等不支持扫码的网盘走这条）。"""

    provider: str = Field(description="pan115 / baidu / quark")
    cookie: str = Field(min_length=1, description="浏览器完整 Cookie")
    site_id: int | None = Field(default=None)
    site_name: str | None = Field(default=None)
    # 故意**不提供** verify 开关：带上 verify=false 就能把任意字符串
    # 当 Cookie 写进站点记录，等于绕过 ADR-40（校验不过不写库）。
    # 服务层仍保留 verify 参数，但只给扫码流程内部用——
    # 扫码本身已证明凭据有效，再校验一次反而更容易撞风控。


# ---------------- STRM 同步 ----------------
class StrmSyncRequest(BaseModel):
    """手动触发一次 STRM 同步。site_id 为空表示全部网盘。"""

    site_id: int | None = Field(default=None, description="网盘站点 ID，留空遍历全部启用网盘")
    pan_path: str = Field(default="/", description="从网盘的哪个目录开始同步")
    strm_subdir: str | None = Field(default=None, description="STRM 根目录下的子目录，便于多盘并存")
    link_mode: str | None = Field(default=None, description="proxy（302 端点，永不过期）或 direct（写临时直链）")
    clean: bool | None = Field(default=None, description="是否清理源文件已消失的失效 STRM")


# ---------------- 网盘分享追更 ----------------
class PanSubscribeCreate(BaseModel):
    """新建一条分享追更任务：盯死一个会持续更新的分享链接。"""

    name: str = Field(min_length=1, description="任务名称")
    share_url: str = Field(min_length=1, description="网盘分享链接")
    password: str | None = Field(default=None, description="提取码")
    site_id: int | None = Field(default=None, description="转存到哪个网盘，留空自动挑同家网盘")
    target_dir: str | None = Field(default=None, description="转存落地目录")
    include_regex: str | None = Field(default=None, description="只转存匹配该正则的文件名")
    exclude_regex: str | None = Field(default=None, description="排除匹配该正则的文件名")
    rename_search: str | None = Field(default=None, description="转存后重命名的匹配正则")
    rename_replace: str | None = Field(default=None, description="重命名替换模板，支持 \\1 反向引用")
    weekdays: list[int] = Field(default_factory=list, description="仅在这些星期几执行（0=周一…6=周日），空=每天")


class PanSubscribeUpdate(BaseModel):
    """更新分享追更任务，只提交需要改的字段。"""

    name: str | None = None
    share_url: str | None = None
    password: str | None = None
    site_id: int | None = None
    target_dir: str | None = None
    include_regex: str | None = None
    exclude_regex: str | None = None
    rename_search: str | None = None
    rename_replace: str | None = None
    status: SubscribeStatus | None = None
    weekdays: list[int] | None = None
    reset_invalid: bool = Field(default=False, description="清除失效标记与失败计数，让任务重新开始")
    reset_history: bool = Field(default=False, description="清空已转存记录，下次巡检会重新转存全部文件")


class VideoSubscribeCreate(BaseModel):
    """新建网页视频订阅：盯住一个 UP 主 / 频道 / 播放列表自动追新。"""

    name: str = Field(min_length=1, description="订阅名称")
    url: str = Field(min_length=1, description="UP 主空间页 / 频道页 / 播放列表地址")
    site: str | None = Field(default=None, description="来源标记，留空按地址自动判断")
    save_path: str | None = Field(default=None, description="下载落地目录，留空用全局下载目录")
    include_regex: str | None = Field(default=None, description="只下载标题匹配该正则的投稿")
    exclude_regex: str | None = Field(default=None, description="排除标题匹配该正则的投稿")
    check_limit: int = Field(default=10, ge=1, le=50, description="每次巡检取列表前多少条")
    max_per_run: int = Field(default=3, ge=1, le=20, description="单次巡检最多下载几个")
    max_height: int | None = Field(default=None, ge=144, le=4320, description="画质上限（如 1080），留空自动")
    skip_existing: bool = Field(default=True, description="首次巡检只记账不补历史（推荐，避免一次投出几十个任务）")


class VideoSubscribeUpdate(BaseModel):
    """更新网页视频订阅，只提交需要改的字段。"""

    name: str | None = None
    url: str | None = None
    site: str | None = None
    save_path: str | None = None
    include_regex: str | None = None
    exclude_regex: str | None = None
    check_limit: int | None = Field(default=None, ge=1, le=50)
    max_per_run: int | None = Field(default=None, ge=1, le=20)
    max_height: int | None = Field(default=None, ge=144, le=4320)
    status: SubscribeStatus | None = None
    skip_existing: bool | None = None
    reset_history: bool = Field(default=False, description="清空已处理记录，下次巡检会重新补历史")
    reset_failures: bool = Field(default=False, description="清零失败计数，被自动暂停的订阅会一并恢复")


# ---------------- 刮削与洗版 ----------------
class ScrapeRequest(BaseModel):
    """批量补刮 NFO 与图片。"""

    path: str | None = Field(default=None, description="要刮削的目录，留空用媒体库根目录")
    limit: int = Field(default=200, ge=1, le=5000, description="单次最多处理多少个文件")
    overwrite: bool = Field(default=False, description="已有 NFO 是否覆盖重写")


class UpgradeRequest(BaseModel):
    """洗版试算/执行。"""

    dry_run: bool = Field(default=True, description="仅试算不实际提交下载（默认只看结果，避免误下）")


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


# ---------------- 用户与权限（v1.5.0） ----------------
class UserCreate(BaseModel):
    """新建本地用户。"""

    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, description="至少 6 位")
    role: UserRole = Field(default=UserRole.VIEWER, description="默认给最小权限，避免误开管理员")
    note: str | None = Field(default=None, description="备注，例如「老婆的账号」")
    is_active: bool = True


class UserUpdate(BaseModel):
    """更新用户，只提交要改的字段。"""

    password: str | None = Field(default=None, min_length=6)
    role: UserRole | None = None
    note: str | None = None
    is_active: bool | None = None


class UserOut(ORMModel):
    id: int
    username: str
    role: str
    role_label: str = ""
    note: str | None = None
    is_active: bool = True
    is_superuser: bool = False
    last_login_at: datetime | None = None
    created_at: datetime | None = None


# ---------------- 运行期配置（v1.5.0） ----------------
class SettingsUpdate(BaseModel):
    """在线修改配置。只接受白名单内的键，一项非法则整体拒绝。"""

    values: dict[str, Any] = Field(default_factory=dict, description="配置键 -> 新值")


class SettingsReset(BaseModel):
    """把配置恢复为 .env / config.yaml 里的静态值。"""

    keys: list[str] | None = Field(default=None, description="要重置的键，留空表示全部")


# ---------------- 榜单自动订阅（v1.5.0） ----------------
class RankingRuleCreate(BaseModel):
    """一条榜单订阅规则：从哪个榜、取多少、满足什么条件就自动订阅。"""

    name: str = Field(min_length=1)
    source: str = Field(default="tmdb_trending", description="tmdb_trending/tmdb_popular/tmdb_top_rated/local_trending")
    media_type: MediaType = MediaType.TV
    limit: int = Field(default=10, ge=1, le=100, description="从榜单取前 N 条")
    min_vote: float = Field(default=0, ge=0, le=10, description="TMDB 评分下限")
    min_year: int | None = Field(default=None, description="年份下限，过滤老片")
    include: str | None = Field(default=None, description="标题必须包含（任一）")
    exclude: str | None = Field(default=None, description="标题命中即跳过")
    subscribe_defaults: dict[str, Any] = Field(default_factory=dict, description="自动建订阅时套用的默认字段")
    enabled: bool = True


class RankingRuleUpdate(BaseModel):
    name: str | None = None
    source: str | None = None
    media_type: MediaType | None = None
    limit: int | None = Field(default=None, ge=1, le=100)
    min_vote: float | None = Field(default=None, ge=0, le=10)
    min_year: int | None = None
    include: str | None = None
    exclude: str | None = None
    subscribe_defaults: dict[str, Any] | None = None
    enabled: bool | None = None
    reset_handled: bool = Field(default=False, description="清空已处理记录，让规则重新扫全榜")


# ---------------- 过滤规则组（v1.5.0） ----------------
class RuleLevelIn(BaseModel):
    """规则组里的一层。所有条件都可留空，留空即不限制。"""

    name: str = ""
    resolution: str = ""
    quality: str = ""
    effect: str = ""
    video_codec: str = ""
    include: str = ""
    exclude: str = ""
    min_seeders: int = 0
    min_size_gb: float = 0
    max_size_gb: float = 0


class RuleGroupCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    levels: list[RuleLevelIn] = Field(default_factory=list, description="有序：越靠前越优先")
    accept_unmatched: bool = Field(default=True, description="关掉即「宁可不下也不要不合规的」")
    enabled: bool = True
    is_default: bool = False


class RuleGroupUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    levels: list[RuleLevelIn] | None = None
    accept_unmatched: bool | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class RuleGroupPreviewRequest(BaseModel):
    """用一批样例资源试算规则组效果。"""

    resources: list[dict[str, Any]] = Field(default_factory=list)
