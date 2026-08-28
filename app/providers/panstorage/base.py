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

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "saved_path": self.saved_path,
            "file_count": self.file_count,
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
    #: 该网盘是否支持删除
    supports_delete: bool = True

    @property
    def root_path(self) -> str:
        """转存的默认落地目录。"""
        return str(self.option("root_path") or "/")

    @abstractmethod
    async def list_dir(self, path: str = "/") -> list[PanFile]:
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
