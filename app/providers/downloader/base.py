"""下载器抽象。"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.providers.base import BaseProvider
from app.schemas.enums import ProviderKind, TaskStatus


@dataclass
class TorrentState:
    """下载器中的任务状态。"""

    external_id: str
    name: str = ""
    status: str = TaskStatus.DOWNLOADING.value
    progress: float = 0.0
    size: int = 0
    downloaded: int = 0
    speed: int = 0
    eta: int = 0
    save_path: str = ""
    content_path: str = ""
    files: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def finished(self) -> bool:
        return self.status in (
            TaskStatus.COMPLETED.value,
            TaskStatus.TRANSFERRED.value,
        ) or self.progress >= 1.0


class BaseDownloader(BaseProvider):
    """下载器基类。"""

    kind = ProviderKind.DOWNLOADER.value

    @abstractmethod
    async def add(
        self,
        link: str,
        *,
        save_path: str | None = None,
        category: str | None = None,
        paused: bool = False,
        cookie: str | None = None,
    ) -> str | None:
        """添加下载任务，返回下载器内的任务 ID。"""

    @abstractmethod
    async def get(self, external_id: str) -> TorrentState | None:
        """查询单个任务。"""

    @abstractmethod
    async def list_tasks(self, category: str | None = None) -> list[TorrentState]:
        """列出任务。"""

    @abstractmethod
    async def remove(self, external_id: str, *, delete_files: bool = False) -> bool:
        """删除任务。"""

    async def pause(self, external_id: str) -> bool:
        """暂停任务。"""
        return False

    async def resume(self, external_id: str) -> bool:
        """恢复任务。"""
        return False

    #: 是否支持全局限速。默认 ``False``：**不假装支持**。
    #: 迅雷的本地 CGI 与 yt-dlp 都没有"运行时改全局限速"的接口，
    #: 谎报支持只会让用户以为设了限速却毫无效果（沿用 ADR-28 能力位思路）。
    supports_speed_limit: bool = False

    async def set_speed_limit(
        self, *, download_kb: int | None = None, upload_kb: int | None = None
    ) -> bool:
        """设置全局限速（KB/s）。``0`` 表示不限速，``None`` 表示不改这一项。

        只有 ``supports_speed_limit`` 为真的下载器才需要覆写。
        """
        return False

    async def get_speed_limit(self) -> dict[str, int] | None:
        """读取当前全局限速（KB/s），不支持时返回 ``None``。"""
        return None

    def default_save_path(self) -> str | None:
        """站点配置的默认保存目录。"""
        value = self.option("save_path")
        return str(value) if value else None

    def _map_extra(self) -> dict[str, Any]:
        return dict(self.option("extra", {}) or {})
