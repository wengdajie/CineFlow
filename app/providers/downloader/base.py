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

    def default_save_path(self) -> str | None:
        """站点配置的默认保存目录。"""
        value = self.option("save_path")
        return str(value) if value else None

    def _map_extra(self) -> dict[str, Any]:
        return dict(self.option("extra", {}) or {})
