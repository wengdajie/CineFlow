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

    @property
    def paywalled(self) -> bool:
        """该资源是否指向会员/付费墙内容（当前只对网页视频有意义）。

        为什么放在 Resource 上：搜索结果里混着"能下的"和"下不了的"时，
        必须在**列表上**就能区分，否则用户只能靠一个个点来试错。
        判定复用 yt-dlp 那套 URL 特征（ADR-24 的唯一裁决点），
        不在这里重复一份规则，免得两边漂移。
        """
        if self.kind != ResourceKind.WEBVIDEO.value:
            return False
        try:
            from app.providers.downloader.ytdlp import is_blocked

            blocked, _ = is_blocked(self.link)
            return bool(blocked)
        except Exception:  # pragma: no cover - 判定不可用时按"可下"处理，交给下载入口兜
            return False

    @property
    def actions(self) -> list[str]:
        """这条资源支持哪些动作，供前端按能力渲染按钮。

        参考 T3FAP 的「能力位」思路：不让前端去猜「网盘资源能不能转存」，
        而是后端明确告知。这样新增资源类型时前端零改动。

        - ``save``：转存进自己的网盘（仅网盘分享链接）
        - ``download``：投给下载器（磁力/种子/直链，以及网盘直链下载）
        - ``open``：在浏览器打开详情页
        """
        result: list[str] = []
        link = str(self.link or "").lower()
        if self.kind == ResourceKind.PAN.value:
            # 网盘分享既可以「转存」进自己的盘，也可以「下载」——
            # 下载走的是先转存再换直链、或直接由支持的网盘取直链
            result.append("save")
            result.append("download")
        elif self.kind == ResourceKind.WEBVIDEO.value:
            # 公开视频页（YouTube/B 站等）只能交给 yt-dlp 下载。
            # 会员正片（腾讯/爱奇艺/优酷/苒果等）不给 download：
            # 按 ADR-24 它们会在入口被拒，渲染一个必然失败的按钮
            # 只会让用户白点一次。只留「详情页」让他去官方平台看。
            if not self.paywalled:
                result.append("download")
        else:
            # 种子/磁力：投下载器
            if link.startswith("magnet:") or link:
                result.append("download")
        if self.page_url:
            result.append("open")
        return result

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["publish_at"] = self.publish_at.isoformat() if self.publish_at else None
        data["unique_key"] = self.unique_key
        data["actions"] = self.actions
        data["paywalled"] = self.paywalled
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
