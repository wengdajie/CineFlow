"""全局配置。

配置来源优先级（从低到高）：
1. 代码默认值
2. ``config/config.yaml``（可选，便于 NAS 上手工编辑）
3. 环境变量 / ``.env``（前缀 ``CF_``）
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_FILE = ROOT_DIR / "config" / "config.yaml"


def _yaml_source() -> dict[str, Any]:
    """读取 YAML 配置文件（扁平化 key，忽略不存在的文件）。"""
    path = Path(os.environ.get("CF_CONFIG_FILE", DEFAULT_CONFIG_FILE))
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # pragma: no cover - 配置损坏时不应阻塞启动
        return {}
    if not isinstance(raw, dict):
        return {}
    flat: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                flat[f"{key}_{sub_key}".upper()] = sub_value
        else:
            flat[str(key).upper()] = value
    # 环境变量优先级高于 YAML：命中同名环境变量时丢弃 YAML 值
    return {k: v for k, v in flat.items() if f"CF_{k}" not in os.environ}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CF_",
        env_file=(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 基础 ----------
    HOST: str = "0.0.0.0"
    PORT: int = 6060
    DEBUG: bool = False
    TIMEZONE: str = "Asia/Shanghai"
    LOG_LEVEL: str = "INFO"
    LOG_MAX_BYTES: int = 5 * 1024 * 1024
    LOG_BACKUP_COUNT: int = 5

    # ---------- 目录 ----------
    DATA_DIR: Path = ROOT_DIR / "data"
    DOWNLOAD_DIR: Path = ROOT_DIR / "downloads"
    LIBRARY_DIR: Path = ROOT_DIR / "library"
    STRM_DIR: Path = ROOT_DIR / "strm"
    PLUGIN_DIR: Path = ROOT_DIR / "plugins"

    # ---------- 数据库 ----------
    DB_URL: str = ""
    DB_ECHO: bool = False

    # ---------- 安全 ----------
    SECRET_KEY: str = "cineflow-dev-secret-change-me"
    TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7
    SUPERUSER: str = "admin"
    SUPERUSER_PASSWORD: str = "cineflow"
    ALLOW_ORIGINS: list[str] = Field(default_factory=lambda: ["*"])
    API_TOKEN: str = ""

    # ---------- 媒体整理 ----------
    TRANSFER_MODE: str = "link"  # link | copy | move | softlink
    MOVIE_TEMPLATE: str = "{title} ({year})/{title} ({year}) - {quality}{ext}"
    TV_TEMPLATE: str = (
        "{title} ({year})/Season {season:02d}/"
        "{title} - S{season:02d}E{episode:02d}{ext}"
    )
    MEDIA_EXTENSIONS: list[str] = Field(
        default_factory=lambda: [
            ".mkv", ".mp4", ".ts", ".iso", ".rmvb", ".avi", ".mov",
            ".mpeg", ".mpg", ".wmv", ".3gp", ".asf", ".m4v", ".flv",
            ".m2ts", ".tp", ".f4v",
        ]
    )
    SUBTITLE_EXTENSIONS: list[str] = Field(
        default_factory=lambda: [".srt", ".ass", ".ssa", ".sub", ".sup"]
    )
    MIN_FILE_SIZE_MB: int = 100

    # ---------- 调度 ----------
    SUBSCRIBE_INTERVAL_MINUTES: int = 30
    #: 追新雷达（站点最新流巡检）间隔，0 表示关闭
    RADAR_INTERVAL_MINUTES: int = 15
    RADAR_ENABLED: bool = True
    RADAR_LIMIT_PER_SITE: int = 100
    DOWNLOAD_CHECK_INTERVAL_MINUTES: int = 5
    LIBRARY_SCAN_CRON: str = "0 4 * * *"
    SCHEDULER_ENABLED: bool = True

    # ---------- 搜索/订阅策略 ----------
    SEARCH_TIMEOUT: int = 25
    SEARCH_MAX_RESULTS: int = 200
    #: 单站安全阀：防某站返回上万条拖死后续处理（0=不限）。公平性由轮转交错保证，不靠砍量
    SEARCH_MAX_PER_SITE: int = 300
    SEARCH_CONCURRENCY: int = 8
    #: 搜索熔断：一个站连续 N 次「吃满超时预算且零结果」就冷却一段时间。
    #: asyncio.gather 要等最慢的站，所以一个连不通的站会决定所有人的等待时间
    #: （实测 YouTube 连不上时把 25s 预算吃满，其余站 5s 内全回来了）。
    SEARCH_BREAKER_ENABLED: bool = True
    SEARCH_BREAKER_THRESHOLD: int = 3
    #: 冷却分钟数；0=只计数不熔断。到期自动半开，恢复后从零计数
    SEARCH_BREAKER_COOLDOWN_MINUTES: int = 10
    AUTO_DOWNLOAD_BEST: bool = True
    PREFER_RESOLUTIONS: list[str] = Field(
        default_factory=lambda: ["2160p", "1080p", "720p"]
    )
    EXCLUDE_KEYWORDS: list[str] = Field(
        default_factory=lambda: ["枪版", "抢先版", "CAM", "TS预告", "预告片", "Sample"]
    )
    INCLUDE_KEYWORDS: list[str] = Field(default_factory=list)
    MIN_SEEDERS: int = 0

    # ---------- 元数据 ----------
    TMDB_API_KEY: str = ""
    TMDB_API_HOST: str = "https://api.themoviedb.org"
    TMDB_IMAGE_HOST: str = "https://image.tmdb.org"
    TMDB_LANGUAGE: str = "zh-CN"
    METADATA_CACHE_TTL: int = 60 * 60 * 12

    # ---------- 网络 ----------
    HTTP_PROXY: str = ""
    USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 CineFlow/1.0"
    )

    # ---------- 刮削（NFO + 图片） ----------
    #: 入库后是否自动生成 NFO（媒体服务器识别率的决定性因素）
    SCRAPE_ENABLED: bool = True
    #: 是否连同海报/背景图一起下载到本地
    SCRAPE_IMAGES: bool = True
    #: 已有 NFO 时是否覆盖（默认不覆盖，尊重用户手工修正）
    SCRAPE_OVERWRITE: bool = False
    #: 媒体库补刮巡检的 cron（补齐历史文件缺失的 NFO），空表示关闭
    SCRAPE_CRON: str = "30 4 * * *"
    #: 单次补刮最多处理多少个文件，避免一次打满 TMDB 限速
    SCRAPE_BATCH: int = 200

    # ---------- 媒体分类归档 ----------
    #: 是否按「电影/剧集/动漫/纪录片/综艺」二级归档
    CATEGORY_ENABLED: bool = False

    # ---------- STRM ----------
    STRM_ENABLED: bool = False
    STRM_BASE_URL: str = "http://127.0.0.1:6060"
    #: 网盘 STRM 同步：写入 STRM 的链接形式
    #:   direct = 网盘临时直链（可能过期）
    #:   proxy  = 指向 CineFlow 的 302 端点（推荐，链接永不过期）
    STRM_LINK_MODE: str = "proxy"
    #: STRM 同步巡检间隔（分钟），0 表示关闭
    STRM_SYNC_INTERVAL_MINUTES: int = 0
    #: 同步时是否清理源文件已消失的失效 STRM 与空目录
    STRM_CLEAN_INVALID: bool = True
    #: 是否连同字幕/NFO 等元数据一起下载到 STRM 目录
    STRM_SYNC_METADATA: bool = True

    # ---------- 网盘分享追更订阅 ----------
    #: 分享追更巡检间隔（分钟），0 表示关闭
    PAN_SUBSCRIBE_INTERVAL_MINUTES: int = 60
    #: 连续多少次转存失败后自动标记分享失效并停止重试
    PAN_SUBSCRIBE_MAX_FAILURES: int = 5

    # ---------- 网页视频订阅（UP 主/频道追更） ----------
    #: 视频追更巡检间隔（分钟），0 表示关闭。默认 120：UP 主更新频率远低于剧集，
    #: 查太勤只是白白增加被风控的概率
    VIDEO_SUBSCRIBE_INTERVAL_MINUTES: int = 120

    # ---------- 洗版 ----------
    #: 是否启用洗版（发现明显更优的版本时替换已入库文件）
    UPGRADE_ENABLED: bool = False
    #: 新资源评分需超过已入库版本多少分才触发替换（防止反复横跳）
    UPGRADE_SCORE_DELTA: float = 15.0
    #: 每个剧集/电影最多洗版几次，用尽后不再替换
    UPGRADE_MAX_TIMES: int = 2

    # ---------- 站点健康探测 ----------
    #: 是否启用站点健康巡检（Cookie 过期表现为静默 0 结果，必须主动探测）
    SITE_HEALTH_ENABLED: bool = True
    #: 健康巡检间隔（分钟），0 表示关闭
    SITE_HEALTH_INTERVAL_MINUTES: int = 180
    #: 连续失败多少次后发出掉线告警
    SITE_HEALTH_FAIL_THRESHOLD: int = 3

    # ---- 社区站点清单（awesome-zhuiju-free） ----
    #: 是否自动同步社区维护的追剧站点清单（只拉 JSON，不外发任何本地数据）
    ZHUIJU_SYNC_ENABLED: bool = True
    #: 清单同步间隔（分钟），0 表示关闭。上游每天 01:00(UTC+8) 更新一次，1440 足够
    ZHUIJU_SYNC_INTERVAL_MINUTES: int = 1440
    #: 同步后是否顺带对候选站点做一次「真搜一次」探测。
    #: 默认开：上游只探首页，`reachable` 里有大半其实搜不到东西（见 ADR-70）。
    ZHUIJU_PROBE_ON_SYNC: bool = True
    #: 单轮最多探测多少个候选站（探测要打别人的站，串行且留间隔，不宜太多）
    ZHUIJU_PROBE_LIMIT: int = 20
    #: 达到告警阈值后是否自动停用该站点（默认不停用，只告警）
    SITE_AUTO_DISABLE: bool = False

    # ---------- 通知去抖 ----------
    #: 同一条告警（站点掉线、网盘登录失效等）的最短重复间隔（分钟），0=不去抖。
    #: 默认 6 小时：站点坏着不动时，每轮巡检都推一条会让用户学会忽略告警
    NOTIFY_ALERT_COOLDOWN_MINUTES: int = 360

    # ---------- 下载器调度 ----------
    #: 多下载器时的选择策略：priority（按站点优先级）/ least_tasks（最少任务）/ round_robin（轮询）
    DOWNLOADER_STRATEGY: str = "priority"
    #: 投递失败时是否自动换下一个健康的下载器重试
    DOWNLOADER_FAILOVER: bool = True
    #: 限速时段巡检间隔（分钟），0 表示关闭。默认 10 分钟：
    #: 时段切换只需分钟级精度，查太勤没有意义
    SPEED_LIMIT_INTERVAL_MINUTES: int = 10

    # ---------- 榜单订阅 ----------
    #: 榜单自动订阅巡检间隔（分钟），0 表示关闭
    RANKING_INTERVAL_MINUTES: int = 720
    #: 单次巡检最多自动创建多少个订阅（防止一次刷进上百个）
    RANKING_MAX_PER_RUN: int = 5

    # ---------- 网盘管理 ----------
    #: 盘搜命中网盘资源时是否自动转存进已配置的网盘存储
    PAN_AUTO_SAVE: bool = True
    #: 定时转存待处理网盘任务的间隔（分钟），0 表示关闭
    PAN_TRANSFER_INTERVAL_MINUTES: int = 20
    #: 网盘凭据保活巡检间隔（分钟，0=关闭）。Cookie 静默过期是网盘最常见故障
    PAN_KEEPALIVE_INTERVAL_MINUTES: int = 360
    #: 单次批量转存的最大任务数
    PAN_TRANSFER_BATCH: int = 20

    # ---------- 内置 AI（站点分析） ----------
    #: AI 总开关。默认关：它要把站点页面发给第三方模型，
    #: 必须用户显式同意才能开，不能"装完就在往外发数据"。
    AI_ENABLED: bool = False
    #: OpenAI 兼容接口地址（只要它支持 /chat/completions 就能用：
    #: OpenAI / DeepSeek / 智谱 / 通义 / Ollama / OneAPI 等）
    AI_BASE_URL: str = "https://api.openai.com/v1"
    AI_API_KEY: str = ""
    AI_MODEL: str = "gpt-4o-mini"
    #: 单次请求超时（秒）。分析站点要读整页 HTML，比聊天慢得多
    AI_TIMEOUT: int = 60
    #: 发给模型的页面正文最大字符数。太大会烧钱也容易超上下文，
    #: 实测一个 MacCMS 搜索页 ~21KB，裁到 16000 足够判断结构
    AI_MAX_PAGE_CHARS: int = 16000
    AI_TEMPERATURE: float = 0.0

    # ---------- ChatOps 机器人 ----------
    #: 是否启用入站 Webhook（飞书/钉钉/Telegram 指令控制）
    CHATOPS_ENABLED: bool = True
    #: 收到指令后是否自动下载最优资源（关闭则只返回列表等用户选择）
    CHATOPS_AUTO_DOWNLOAD: bool = False
    #: 搜索结果最多回复多少条
    CHATOPS_RESULT_LIMIT: int = 5
    #: 允许下发指令的用户白名单（各平台的用户 ID，空表示不限制）
    CHATOPS_ALLOW_USERS: list[str] = Field(default_factory=list)
    #: 会话上下文保留时长（秒），用于「搜索」后回「下载 2」
    CHATOPS_SESSION_TTL: int = 900

    @field_validator(
        "ALLOW_ORIGINS",
        "PREFER_RESOLUTIONS",
        "EXCLUDE_KEYWORDS",
        "INCLUDE_KEYWORDS",
        "CHATOPS_ALLOW_USERS",
        "MEDIA_EXTENSIONS",
        "SUBTITLE_EXTENSIONS",
        mode="before",
    )
    @classmethod
    def _split_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("DOWNLOADER_STRATEGY", "STRM_LINK_MODE", mode="before")
    @classmethod
    def _normalize_choice(cls, value: Any) -> Any:
        """取值型配置统一小写去空格，避免 .env 里写成 Priority 就失效。"""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("TRANSFER_MODE", mode="before")
    @classmethod
    def _normalize_mode(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    def model_post_init(self, __context: Any) -> None:
        for directory in (
            self.DATA_DIR,
            self.DOWNLOAD_DIR,
            self.LIBRARY_DIR,
            self.STRM_DIR,
            self.PLUGIN_DIR,
            self.DATA_DIR / "logs",
            self.DATA_DIR / "cache",
        ):
            Path(directory).mkdir(parents=True, exist_ok=True)
        if not self.DB_URL:
            db_path = Path(self.DATA_DIR) / "cineflow.db"
            object.__setattr__(self, "DB_URL", f"sqlite:///{db_path.as_posix()}")

    @property
    def log_file(self) -> Path:
        return Path(self.DATA_DIR) / "logs" / "cineflow.log"

    @property
    def proxies(self) -> dict[str, str] | None:
        if not self.HTTP_PROXY:
            return None
        return {"http://": self.HTTP_PROXY, "https://": self.HTTP_PROXY}


@lru_cache
def get_settings() -> Settings:
    return Settings(**_yaml_source())


settings = get_settings()

