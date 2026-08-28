"""每日追剧日报插件。

演示 cron 触发的定时任务 + 手动动作，把系统状态汇总成一条推送。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import DownloadTask, LibraryFile, Subscribe
from app.db.session import session_scope
from app.plugins.base import PluginBase
from app.schemas.enums import NotifyLevel, SubscribeStatus, TaskStatus
from app.services import library as library_service
from app.services import notify as notify_service
from app.utils.strings import format_size, truncate

logger = get_logger(__name__)


class DailyDigestPlugin(PluginBase):
    """每天推送一份追剧日报。"""

    plugin_id = "daily_digest"
    plugin_name = "每日追剧日报"
    plugin_version = "1.0.0"
    plugin_desc = "每天定时推送新入库剧集、订阅进度、缺集提醒与下载器状态。"
    plugin_author = "CineFlow"

    config_schema: ClassVar[list[dict[str, Any]]] = [
        {
            "key": "cron",
            "label": "推送时间（5 段 cron）",
            "type": "text",
            "default": "0 9 * * *",
            "placeholder": "0 9 * * *",
        },
        {"key": "include_library", "label": "包含近 24 小时新入库", "type": "checkbox", "default": True},
        {"key": "include_subscribes", "label": "包含订阅进度与缺集", "type": "checkbox", "default": True},
        {"key": "include_downloads", "label": "包含下载中任务", "type": "checkbox", "default": True},
        {"key": "max_items", "label": "每段最多列出条数", "type": "number", "default": 12},
    ]

    # ---------------- 能力声明 ----------------
    def scheduled_jobs(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "digest",
                "name": "每日追剧日报",
                "func": self.send_digest,
                "trigger": "cron",
                "cron": str(self.get_config("cron", "0 9 * * *") or "0 9 * * *"),
            }
        ]

    def actions(self) -> dict[str, Any]:
        return {"send_digest": self.send_digest, "preview": self.build_digest}

    # ---------------- 动作 ----------------
    def build_digest(self) -> dict[str, Any]:
        """生成日报文本（不推送，便于前端预览）。"""
        limit = max(int(self.get_config("max_items", 12) or 12), 1)
        sections: list[str] = []
        since = utcnow() - timedelta(hours=24)

        stats = library_service.library_stats()
        header = (
            f"媒体库：{stats.get('files', 0)} 个文件 / "
            f"{format_size(stats.get('size', 0))} · "
            f"剧集 {stats.get('series', 0)} 部 / {stats.get('episodes', 0)} 集 · "
            f"电影 {stats.get('movies', 0)} 部"
        )

        if self.get_config("include_library", True):
            with session_scope() as session:
                rows = list(
                    session.execute(
                        select(LibraryFile)
                        .where(LibraryFile.created_at >= since)
                        .order_by(LibraryFile.created_at.desc())
                        .limit(limit)
                    ).scalars()
                )
                lines = [self._format_library_row(row) for row in rows]
            sections.append(
                "【近 24 小时新入库】\n" + ("\n".join(lines) if lines else "· 无")
            )

        if self.get_config("include_subscribes", True):
            with session_scope() as session:
                rows = list(
                    session.execute(
                        select(Subscribe)
                        .where(Subscribe.status == SubscribeStatus.ACTIVE.value)
                        .order_by(Subscribe.lack_episodes.desc())
                        .limit(limit)
                    ).scalars()
                )
                lines = [self._format_subscribe_row(row) for row in rows]
            sections.append(
                "【追新中的订阅】\n" + ("\n".join(lines) if lines else "· 无")
            )

        if self.get_config("include_downloads", True):
            with session_scope() as session:
                rows = list(
                    session.execute(
                        select(DownloadTask)
                        .where(
                            DownloadTask.status.in_(
                                [
                                    TaskStatus.DOWNLOADING.value,
                                    TaskStatus.PENDING.value,
                                ]
                            )
                        )
                        .order_by(DownloadTask.created_at.desc())
                        .limit(limit)
                    ).scalars()
                )
                lines = [self._format_task_row(row) for row in rows]
            sections.append(
                "【进行中的下载】\n" + ("\n".join(lines) if lines else "· 无")
            )

        body = header + "\n\n" + "\n\n".join(sections)
        return {"title": "CineFlow 追剧日报", "body": body}

    async def send_digest(self) -> dict[str, Any]:
        """生成并推送日报。"""
        digest = self.build_digest()
        channels = await notify_service.send(
            digest["title"], digest["body"], level=NotifyLevel.INFO.value
        )
        logger.info("追剧日报已推送到 %d 个渠道", channels)
        return {"channels": channels, **digest}

    # ---------------- 内部格式化 ----------------
    @staticmethod
    def _format_library_row(row: LibraryFile) -> str:
        tag = ""
        if row.season is not None and row.episode is not None:
            tag = f" S{row.season:02d}E{row.episode:02d}"
        elif row.episode is not None:
            tag = f" 第{row.episode}集"
        size = format_size(row.size)
        return f"· {truncate(row.title, 40)}{tag} · {row.resolution or '未知画质'} · {size}"

    @staticmethod
    def _format_subscribe_row(row: Subscribe) -> str:
        done = len(row.downloaded_episodes or [])
        total = row.total_episodes or 0
        progress = f"{done}/{total}" if total else f"{done}/?"
        lack = f" 缺 {row.lack_episodes} 集" if row.lack_episodes else ""
        return f"· {truncate(row.title, 40)} S{row.season:02d} · {progress}{lack}"

    @staticmethod
    def _format_task_row(row: DownloadTask) -> str:
        percent = f"{(row.progress or 0) * 100:.0f}%"
        state = "待转存" if row.status == TaskStatus.PENDING.value else percent
        return f"· {truncate(row.title, 44)} · {state}"
