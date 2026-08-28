"""Provider 抽象与注册表。

所有外部能力（索引器、网盘搜索、下载器、媒体服务器、通知）都实现为
Provider，通过注册表按名字实例化，便于插件扩展。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.strings import parse_size


@dataclass
class Resource:
    """统一资源模型（BT 种子 / 网盘分享 / 直链）。"""

    title: str
    link: str
    site: str = ""
    kind: str = ResourceKind.TORRENT.value
    page_url: str | None = None
    description: str | None = None
    size: int = 0
    seeders: int = 0
    leechers: int = 0
    grabs: int = 0
    publish_at: datetime | None = None
    priority: int = 50
    password: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.size = parse_size(self.size)
        self.title = str(self.title or "").strip()

    @property
    def unique_key(self) -> str:
        """用于跨站去重的键。"""
        from hashlib import md5

        base = (self.link or self.title).strip().lower()
        if base.startswith("magnet:"):
            # 磁力链取 infohash 部分
            for segment in base.split("&"):
                if "btih:" in segment:
                    base = segment.split("btih:")[-1]
                    break
        return md5(f"{self.kind}:{base}".encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["publish_at"] = self.publish_at.isoformat() if self.publish_at else None
        data["unique_key"] = self.unique_key
        return data


class BaseProvider(ABC):  # noqa: B024
    """Provider 基类。

    本身不声明抽象方法：它只承载所有 Provider 共用的配置读取与元信息。
    各类别的抽象契约由子类声明（如 ``SearchProvider.search``、
    ``BaseDownloader.add``、``BaseNotifier.send``）。
    继承 ABC 是为了让子类的 ``@abstractmethod`` 能被正确强制。
    """

    #: Provider 唯一标识（配置中的 ``provider`` 字段）
    name: ClassVar[str] = "base"
    #: Provider 类别
    kind: ClassVar[str] = ProviderKind.INDEXER.value
    #: 展示名
    display_name: ClassVar[str] = "Base Provider"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    @property
    def site_name(self) -> str:
        return str(self.config.get("name") or self.display_name)

    @property
    def enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    @property
    def priority(self) -> int:
        return int(self.config.get("priority", 50))

    def option(self, key: str, default: Any = None) -> Any:
        """读取 ``options`` 中的扩展配置。"""
        options = self.config.get("options") or {}
        if key in options:
            return options[key]
        return self.config.get(key, default)

    async def health_check(self) -> tuple[bool, str]:
        """连通性检查。"""
        return True, "未实现健康检查"


class SearchProvider(BaseProvider):
    """可搜索的 Provider（BT 站点 / 盘搜）。"""

    @abstractmethod
    async def search(
        self,
        keyword: str,
        *,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        page: int = 0,
    ) -> list[Resource]:
        """按关键词搜索资源。"""

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        """拉取最新资源（RSS 追新用）。默认退化为空搜索。"""
        return await self.search("")
