"""自动清理插件。

演示插件的三种能力：
1. ``scheduled_jobs()``  —— 注册周期性定时任务
2. ``actions()``         —— 暴露可由 Web/API 手动触发的动作
3. ``event_handlers()``  —— 订阅系统事件（此处在入库完成后做一次轻量清理）
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any, ClassVar

from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import DownloadTask, LibraryFile, NotificationRecord
from app.db.session import session_scope
from app.plugins.base import PluginBase
from app.schemas.enums import TaskStatus
from app.services import download as download_service

logger = get_logger(__name__)


class AutoCleanupPlugin(PluginBase):
    """清理已入库/失败的下载任务与过期通知。"""

    plugin_id = "auto_cleanup"
    plugin_name = "自动清理"
    plugin_version = "1.0.0"
    plugin_desc = "定时清理已入库的下载任务、失败任务与过期通知记录。"
    plugin_author = "CineFlow"

    config_schema: ClassVar[list[dict[str, Any]]] = [
        {"key": "interval_hours", "label": "巡检间隔（小时）", "type": "number", "default": 6},
        {"key": "clean_transferred", "label": "清理已入库任务", "type": "checkbox", "default": True},
        {
            "key": "transferred_keep_hours",
            "label": "已入库任务保留（小时）",
            "type": "number",
            "default": 72,
        },
        {
            "key": "delete_source_files",
            "label": "同时删除下载器源文件（硬链模式请勿开启）",
            "type": "checkbox",
            "default": False,
        },
        {"key": "clean_failed", "label": "清理失败任务", "type": "checkbox", "default": True},
        {"key": "failed_keep_hours", "label": "失败任务保留（小时）", "type": "number", "default": 24},
        {"key": "clean_notifications", "label": "清理历史通知", "type": "checkbox", "default": True},
        {"key": "notification_keep_days", "label": "通知保留天数", "type": "number", "default": 30},
    ]

    # ---------------- 生命周期 ----------------
    async def on_load(self) -> None:
        logger.info("自动清理插件已加载，间隔 %s 小时", self.get_config("interval_hours", 6))

    # ---------------- 能力声明 ----------------
    def scheduled_jobs(self) -> list[dict[str, Any]]:
        hours = max(int(self.get_config("interval_hours", 6) or 6), 1)
        return [
            {
                "id": "cleanup",
                "name": "自动清理：任务与通知",
                "func": self.cleanup,
                "trigger": "interval",
                "hours": hours,
            }
        ]

    def actions(self) -> dict[str, Any]:
        return {
            "cleanup": self.cleanup,
            "prune_missing_library_files": self.prune_missing_library_files,
        }

    def event_handlers(self) -> dict[str, Any]:
        return {"transfer.completed": self.on_transfer_completed}

    # ---------------- 事件 ----------------
    async def on_transfer_completed(self, payload: dict[str, Any]) -> None:
        """入库完成后立即清理该任务（若配置允许）。"""
        if not self.get_config("clean_transferred", True):
            return
        # 保留期 > 0 时交给定时任务处理，避免刚入库就删掉记录
        if int(self.get_config("transferred_keep_hours", 72) or 0) > 0:
            return
        task_id = payload.get("task_id")
        if task_id:
            await download_service.remove_task(
                int(task_id),
                delete_files=bool(self.get_config("delete_source_files", False)),
            )
            logger.info("自动清理：已移除入库完成的任务 #%s", task_id)

    # ---------------- 动作 ----------------
    async def cleanup(self) -> dict[str, int]:
        """执行一次完整清理，返回各项数量。"""
        stats = {"transferred": 0, "failed": 0, "notifications": 0}
        now = utcnow()
        delete_files = bool(self.get_config("delete_source_files", False))

        if self.get_config("clean_transferred", True):
            deadline = now - timedelta(
                hours=max(int(self.get_config("transferred_keep_hours", 72) or 0), 0)
            )
            for task_id in self._pick_tasks(TaskStatus.TRANSFERRED.value, deadline):
                if await download_service.remove_task(task_id, delete_files=delete_files):
                    stats["transferred"] += 1

        if self.get_config("clean_failed", True):
            deadline = now - timedelta(
                hours=max(int(self.get_config("failed_keep_hours", 24) or 0), 0)
            )
            for task_id in self._pick_tasks(TaskStatus.FAILED.value, deadline):
                if await download_service.remove_task(task_id, delete_files=False):
                    stats["failed"] += 1

        if self.get_config("clean_notifications", True):
            days = max(int(self.get_config("notification_keep_days", 30) or 0), 0)
            deadline = now - timedelta(days=days)
            with session_scope() as session:
                stats["notifications"] = (
                    session.query(NotificationRecord)
                    .filter(NotificationRecord.created_at < deadline)
                    .delete(synchronize_session=False)
                )

        logger.info(
            "自动清理完成：已入库 %d、失败 %d、通知 %d",
            stats["transferred"],
            stats["failed"],
            stats["notifications"],
        )
        return stats

    def prune_missing_library_files(self) -> dict[str, int]:
        """清理媒体库索引中已不存在的文件记录。"""
        removed = 0
        with session_scope() as session:
            for record in session.query(LibraryFile).all():
                if not Path(record.path).exists():
                    session.delete(record)
                    removed += 1
        logger.info("自动清理：移除失效媒体库索引 %d 条", removed)
        return {"removed": removed}

    # ---------------- 内部 ----------------
    @staticmethod
    def _pick_tasks(status: str, deadline: Any) -> list[int]:
        """挑出指定状态且早于 deadline 的任务 ID。"""
        with session_scope() as session:
            return [
                row[0]
                for row in session.query(DownloadTask.id)
                .filter(
                    DownloadTask.status == status,
                    DownloadTask.updated_at < deadline,
                )
                .all()
            ]
