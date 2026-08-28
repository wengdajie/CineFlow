"""网盘转存助手插件。

盘搜（PanSou / 自定义盘搜）命中的资源不会进入 BT 下载器，
CineFlow 会把它登记为 ``pending`` 状态的任务并保留分享链接与提取码。
本插件负责：

- 定时汇总这些待转存任务并推送提醒；
- 若配置了转存 Webhook（例如自建的 alist / cloud-saver 转存服务），
  则把分享链接与提取码投递过去，实现全自动转存。
"""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.models import DownloadTask
from app.db.session import session_scope
from app.plugins.base import PluginBase
from app.schemas.enums import EventType, NotifyLevel, ResourceKind, TaskStatus
from app.services import notify as notify_service
from app.utils.http import fetch_json
from app.utils.strings import split_keywords, truncate

logger = get_logger(__name__)


class PanTransferPlugin(PluginBase):
    """网盘待转存任务的汇总、提醒与自动转存。"""

    plugin_id = "pan_transfer"
    plugin_name = "网盘转存助手"
    plugin_version = "1.0.0"
    plugin_desc = "汇总盘搜命中的网盘待处理任务，按需推送提醒，并可对接转存 Webhook 自动转存。"
    plugin_author = "CineFlow"

    config_schema: ClassVar[list[dict[str, Any]]] = [
        {"key": "interval_minutes", "label": "巡检间隔（分钟）", "type": "number", "default": 30},
        {"key": "notify_pending", "label": "有待转存任务时推送提醒", "type": "checkbox", "default": True},
        {
            "key": "webhook_url",
            "label": "转存 Webhook 地址（留空则仅提醒）",
            "type": "text",
            "placeholder": "http://alist-helper:8080/api/save",
        },
        {"key": "webhook_token", "label": "Webhook 鉴权 Token", "type": "text"},
        {"key": "webhook_timeout", "label": "Webhook 超时（秒）", "type": "number", "default": 30},
        {
            "key": "mark_transferred",
            "label": "Webhook 成功后把任务标记为已完成",
            "type": "checkbox",
            "default": False,
        },
        {
            "key": "allow_pan_types",
            "label": "仅处理指定网盘（逗号分隔，如 quark,aliyun）",
            "type": "text",
        },
    ]

    # ---------------- 生命周期 ----------------
    async def on_load(self) -> None:
        mode = "自动转存" if self.get_config("webhook_url") else "仅提醒"
        logger.info("网盘转存助手已加载，模式：%s", mode)

    # ---------------- 能力声明 ----------------
    def scheduled_jobs(self) -> list[dict[str, Any]]:
        minutes = max(int(self.get_config("interval_minutes", 30) or 30), 1)
        return [
            {
                "id": "process",
                "name": "网盘转存：处理待转存任务",
                "func": self.process_pending,
                "trigger": "interval",
                "minutes": minutes,
            }
        ]

    def actions(self) -> dict[str, Any]:
        return {
            "process_pending": self.process_pending,
            "list_pending": self.list_pending,
        }

    def event_handlers(self) -> dict[str, Any]:
        return {EventType.DOWNLOAD_ADDED.value: self.on_download_added}

    # ---------------- 事件 ----------------
    async def on_download_added(self, payload: dict[str, Any]) -> None:
        """新登记的网盘任务立即尝试转存一次。"""
        if payload.get("kind") != ResourceKind.PAN.value:
            return
        if not self.get_config("webhook_url"):
            return
        task_id = payload.get("task_id")
        if task_id:
            await self.process_pending(task_id=int(task_id))

    # ---------------- 动作 ----------------
    def list_pending(self) -> list[dict[str, Any]]:
        """列出所有待转存的网盘任务。"""
        return self._pending_tasks()

    async def process_pending(self, task_id: int | None = None) -> dict[str, Any]:
        """处理待转存任务：调用 Webhook 或推送提醒。"""
        tasks = self._pending_tasks(task_id)
        allow = {item.lower() for item in split_keywords(self.get_config("allow_pan_types"))}
        if allow:
            tasks = [
                task
                for task in tasks
                if str(task.get("pan_type") or "").lower() in allow
            ]

        stats = {"pending": len(tasks), "saved": 0, "failed": 0, "notified": 0}
        if not tasks:
            return stats

        webhook = str(self.get_config("webhook_url") or "").strip()
        if webhook:
            for task in tasks:
                if await self._call_webhook(webhook, task):
                    stats["saved"] += 1
                else:
                    stats["failed"] += 1
        elif self.get_config("notify_pending", True):
            lines = [
                f"· {truncate(task['title'], 50)}"
                + (f"（码:{task['password']}）" if task.get("password") else "")
                for task in tasks[:10]
            ]
            more = f"\n… 另有 {len(tasks) - 10} 条" if len(tasks) > 10 else ""
            await notify_service.send(
                f"有 {len(tasks)} 个网盘资源待转存",
                "\n".join(lines) + more,
                level=NotifyLevel.WARNING.value,
            )
            stats["notified"] = 1

        logger.info(
            "网盘转存巡检：待处理 %d、成功 %d、失败 %d",
            stats["pending"],
            stats["saved"],
            stats["failed"],
        )
        return stats

    # ---------------- 内部 ----------------
    @staticmethod
    def _pending_tasks(task_id: int | None = None) -> list[dict[str, Any]]:
        """查询待转存的网盘任务快照。"""
        with session_scope() as session:
            stmt = select(DownloadTask).where(
                DownloadTask.kind == ResourceKind.PAN.value,
                DownloadTask.status == TaskStatus.PENDING.value,
            )
            if task_id is not None:
                stmt = stmt.where(DownloadTask.id == task_id)
            return [
                {
                    "id": task.id,
                    "title": task.title,
                    "link": task.link,
                    "site": task.site,
                    "media_type": task.media_type,
                    "season": task.season,
                    "episodes": task.episodes or [],
                    "save_path": task.save_path,
                    "password": (task.meta or {}).get("password"),
                    "pan_type": (task.meta or {}).get("pan_type"),
                }
                for task in session.execute(stmt).scalars()
            ]

    async def _call_webhook(self, url: str, task: dict[str, Any]) -> bool:
        """把转存请求投递给外部服务。"""
        headers = {}
        token = str(self.get_config("webhook_token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        result = await fetch_json(
            url,
            method="POST",
            json_body={
                "task_id": task["id"],
                "title": task["title"],
                "share_url": task["link"],
                "password": task.get("password") or "",
                "pan_type": task.get("pan_type") or "",
                "media_type": task.get("media_type"),
                "season": task.get("season"),
                "episodes": task.get("episodes"),
                "save_path": task.get("save_path"),
            },
            headers=headers,
            timeout=float(self.get_config("webhook_timeout", 30) or 30),
        )
        ok = bool(result) and result.get("success") is not False
        if not ok:
            self._record_error(task["id"], "转存 Webhook 无响应或返回失败")
            return False

        if self.get_config("mark_transferred", False):
            with session_scope() as session:
                record = session.get(DownloadTask, task["id"])
                if record:
                    record.status = TaskStatus.TRANSFERRED.value
                    record.progress = 1.0
                    record.error = None
        logger.info("网盘任务 #%s 已投递转存：%s", task["id"], truncate(task["title"], 60))
        return True

    @staticmethod
    def _record_error(task_id: int, message: str) -> None:
        with session_scope() as session:
            record = session.get(DownloadTask, task_id)
            if record:
                record.error = message
