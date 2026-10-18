"""网盘存储 Provider 抽象。

**与「盘搜」的区别**：``app/providers/pan/`` 下的 ``pansou`` / ``pan_generic``
是**搜索**器（找分享链接），实现 :class:`SearchProvider`；
本模块是**存储**器（把分享转存进自己的网盘、浏览自己的网盘目录），
实现 :class:`BasePanStorage`。两者互补：先搜到分享链接，再转存进自己的盘。
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.providers.base import BaseProvider
from app.schemas.enums import ProviderKind


@dataclass
class PanFile:
    """网盘中的一个文件或目录。"""

    name: str
    path: str
    is_dir: bool = False
    size: int = 0
    file_id: str | None = None
    modified_at: str | None = None
    #: 可直接播放/下载的临时直链（多数网盘需单独换取，可能为空）
    download_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "is_dir": self.is_dir,
            "size": self.size,
            "file_id": self.file_id,
            "modified_at": self.modified_at,
            "download_url": self.download_url,
            "extra": self.extra,
        }


class IncompleteDirectory(OSError):
    """目录只返回部分条目，允许同步已知文件，但不能据此推断删除。"""

    def __init__(self, files: list[PanFile]):
        super().__init__("网盘目录返回不完整，跳过失效清理")
        self.files = files


@dataclass
class PanQuota:
    """网盘容量信息。"""

    total: int = 0
    used: int = 0

    @property
    def free(self) -> int:
        return max(self.total - self.used, 0)

    @property
    def percent(self) -> float:
        """已用百分比（0~100，总量未知时返回 0）。"""
        if self.total <= 0:
            return 0.0
        return round(self.used / self.total * 100, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "used": self.used,
            "free": self.free,
            "percent": self.percent,
        }


@dataclass
class SaveResult:
    """转存结果。"""

    success: bool
    message: str = ""
    #: 转存后在网盘中的落地路径
    saved_path: str | None = None
    file_count: int = 0
    saved_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "saved_path": self.saved_path,
            "file_count": self.file_count,
            **({"saved_files": self.saved_files} if self.saved_files else {}),
        }


class BasePanStorage(BaseProvider):
    """网盘存储基类。

    子类至少要实现 :meth:`list_dir` 与 :meth:`save_share`；
    其余方法有默认实现（返回"未实现"而不是抛异常），保证**优雅降级**：
    某个网盘不支持删除，界面上按钮点了会得到明确提示而不是 500。
    """

    kind = ProviderKind.PANSTORAGE.value

    #: 该网盘是否支持从分享链接转存
    supports_save: bool = True
    supports_share_download: bool = False
    #: 该网盘是否支持删除
    supports_delete: bool = True
    #: 该网盘是否支持重命名
    supports_rename: bool = False
    #: 该网盘是否支持移动/复制
    supports_move: bool = False
    #: 该网盘是否支持盘内搜索
    supports_search: bool = False
    #: 该网盘是否支持 Cookie/凭据保活（keep_alive）
    supports_strict_list: bool = False
    supports_keepalive: bool = False

    def capabilities(self) -> dict[str, bool]:
        """汇总能力位，供 API/前端按能力渲染按钮（而不是写死一堆 if）。

        这样新增网盘时前端零改动：能力位为 False 的按钮直接不渲染，
        避免用户点了才发现「不支持」。
        """
        return {
            "save": self.supports_save,
            "share_download": self.supports_share_download,
            "delete": self.supports_delete,
            "rename": self.supports_rename,
            "move": self.supports_move,
            "search": self.supports_search,
            "keepalive": self.supports_keepalive,
        }

    @property
    def root_path(self) -> str:
        """转存的默认落地目录。"""
        return str(self.option("root_path") or "/")

    async def list_dir_for_sync(self, path: str = "/") -> list[PanFile]:
        """内置存储使用严格读取；第三方存储须在读取失败时抛异常。"""
        if self.supports_strict_list:
            return await self.list_dir(path, strict=True)
        return await self.list_dir(path)

    @abstractmethod
    async def list_dir(self, path: str = "/", *, strict: bool = False) -> list[PanFile]:
        """列出目录内容。"""

    @abstractmethod
    async def save_share(
        self,
        share_url: str,
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        """把分享链接转存到自己的网盘（整个分享）。"""

    async def list_share(
        self, share_url: str, *, password: str | None = None
    ) -> list[PanFile]:
        """列出分享链接**内部**的文件清单。

        这是「分享追更」能做增量的前提：先看清分享里有什么，
        才能只转存新增的那几集。不支持的网盘返回空列表，
        调用方会退化成「整个分享转存 + 分享级去重」。
        """
        return []

    async def share_downloads(self, share_url: str, *, password: str | None = None) -> list[PanFile]:
        """解析分享内可下载的原文件；不得调用转存或文件写入接口。"""
        raise ValueError(f"{self.site_name} 暂不支持直接下载分享，请使用官方页面，或明确转存后从盘内下载")

    async def save_share_files(
        self,
        share_url: str,
        files: list[PanFile],
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        """只转存分享内指定的若干文件（增量转存）。

        默认实现退回整体转存——对不支持挑文件的网盘，
        转多了总比漏更新好（重复文件网盘侧会自己去重或覆盖）。
        """
        return await self.save_share(
            share_url, password=password, target_dir=target_dir
        )

    async def quota(self) -> PanQuota:
        """查询容量（不支持时返回全 0）。"""
        return PanQuota()

    async def mkdir(self, path: str) -> bool:
        """创建目录。"""
        return False

    async def delete(self, path: str, *, file_id: str | None = None) -> bool:
        """删除文件或目录。"""
        return False

    async def download_url(self, path: str, *, file_id: str | None = None) -> str | None:
        """换取临时直链（用于 STRM 或投给 aria2）。"""
        return None

    #: 请求本网盘直链时必须使用的 User-Agent；``None`` 表示无特殊要求。
    #:
    #: **为什么这是 Provider 的属性而不是调用方的常量**：直链的 UA 要求是
    #: 网盘自己的鉴权规则，只有 Provider 知道。百度的 CDN 直链换成别的 UA
    #: 时小文件能过、超过 ~50MB 直接 403 —— 把这个知识留在 Provider 里，
    #: 下载链路只管照着传，才不会每加一个网盘就漏一次。
    direct_link_user_agent: str | None = None

    def download_headers(self, url: str) -> dict[str, str]:
        """只返回下载该 CDN 原文件所需的请求头，不返回 API 的鉴权头。"""
        return {}

    # ---------------- 文件管理（v1.7.0 新增，参考 T3FAP 网盘插件能力矩阵） ----------------
    async def rename(
        self, path: str, new_name: str, *, file_id: str | None = None
    ) -> bool:
        """重命名文件/目录。不支持的网盘返回 False（不抛异常）。"""
        return False

    async def move(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> bool:
        """移动文件/目录到 ``target_dir``。"""
        return False

    async def copy(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> bool:
        """复制文件/目录到 ``target_dir``。"""
        return False

    async def search(self, keyword: str, *, limit: int = 50) -> list[PanFile]:
        """盘内搜索。不支持的网盘返回空列表，调用方会提示「该网盘不支持搜索」。"""
        return []

    async def keep_alive(self) -> tuple[bool, str]:
        """凭据保活探活。

        网盘 Cookie 普遍是「不用就过期」，所以定时轻量调用一次接口既能
        探测有效期、又能顺带续期。默认实现直接复用 :meth:`health_check`，
        这样即使子类没重写也能参与巡检。
        """
        return await self.health_check()

    def normalize_path(self, path: str | None) -> str:
        """规范化网盘路径：统一用 ``/`` 开头、去掉重复斜杠与尾部斜杠。"""
        raw = str(path or "/").replace("\\", "/").strip()
        if not raw.startswith("/"):
            raw = "/" + raw
        while "//" in raw:
            raw = raw.replace("//", "/")
        if len(raw) > 1:
            raw = raw.rstrip("/")
        return raw or "/"

    def join_path(self, *parts: str) -> str:
        """拼接网盘路径。"""
        segments: list[str] = []
        for part in parts:
            for piece in str(part or "").replace("\\", "/").split("/"):
                if piece and piece != ".":
                    segments.append(piece)
        return "/" + "/".join(segments) if segments else "/"
