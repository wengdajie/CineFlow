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
    PORT: int = 8611
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
    SEARCH_CONCURRENCY: int = 8
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

    # ---------- STRM ----------
    STRM_ENABLED: bool = False
    STRM_BASE_URL: str = "http://127.0.0.1:8611"

    @field_validator(
        "ALLOW_ORIGINS",
        "PREFER_RESOLUTIONS",
        "EXCLUDE_KEYWORDS",
        "INCLUDE_KEYWORDS",
        "MEDIA_EXTENSIONS",
        "SUBTITLE_EXTENSIONS",
        mode="before",
    )
    @classmethod
    def _split_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
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

