"""定时调度：订阅巡检、下载状态同步、媒体库扫描、插件任务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)

JOB_SUBSCRIBE = "cineflow.subscribe"
JOB_DOWNLOAD = "cineflow.download"
JOB_LIBRARY = "cineflow.library"
JOB_RADAR = "cineflow.radar"
_PLUGIN_PREFIX = "plugin."


def _parse_cron(expression: str) -> CronTrigger:
    """解析 5 段 cron 表达式。"""
    parts = str(expression or "").split()
    if len(parts) != 5:
        raise ValueError(f"非法 cron 表达式: {expression}")
    minute, hour, day, month, day_of_week = parts
    return CronTrigger(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
        timezone=settings.TIMEZONE,
    )


class SchedulerService:
    """调度服务封装。"""

    def __init__(self) -> None:
        self._scheduler: AsyncIOScheduler | None = None

    @property
    def scheduler(self) -> AsyncIOScheduler:
        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)
        return self._scheduler

    @property
    def running(self) -> bool:
        return bool(self._scheduler and self._scheduler.running)

    def start(self) -> None:
        """启动调度器并注册内置任务。"""
        if not settings.SCHEDULER_ENABLED:
            logger.info("调度器已被配置禁用")
            return
        if self.running:
            return

        from app.services import download as download_service
        from app.services import library as library_service
        from app.services import radar as radar_service
        from app.services import subscribe as subscribe_service

        self.scheduler.add_job(
            subscribe_service.run_all,
            trigger=IntervalTrigger(minutes=settings.SUBSCRIBE_INTERVAL_MINUTES),
            id=JOB_SUBSCRIBE,
            name="订阅巡检（自动追新）",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        if settings.RADAR_ENABLED and settings.RADAR_INTERVAL_MINUTES > 0:
            self.scheduler.add_job(
                radar_service.run,
                trigger=IntervalTrigger(minutes=settings.RADAR_INTERVAL_MINUTES),
                id=JOB_RADAR,
                name="追新雷达（站点最新流巡检）",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
        self.scheduler.add_job(
            download_service.sync_tasks,
            trigger=IntervalTrigger(minutes=settings.DOWNLOAD_CHECK_INTERVAL_MINUTES),
            id=JOB_DOWNLOAD,
            name="下载状态同步与自动整理",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        try:
            self.scheduler.add_job(
                library_service.scan_library,
                trigger=_parse_cron(settings.LIBRARY_SCAN_CRON),
                id=JOB_LIBRARY,
                name="媒体库全量扫描",
                replace_existing=True,
                max_instances=1,
            )
        except ValueError as exc:
            logger.warning("媒体库扫描任务未注册: %s", exc)

        self.scheduler.start()
        logger.info(
            "调度器已启动：订阅每 %d 分钟、雷达每 %s 分钟、下载每 %d 分钟",
            settings.SUBSCRIBE_INTERVAL_MINUTES,
            settings.RADAR_INTERVAL_MINUTES
            if settings.RADAR_ENABLED and settings.RADAR_INTERVAL_MINUTES > 0
            else "关闭",
            settings.DOWNLOAD_CHECK_INTERVAL_MINUTES,
        )

    def shutdown(self) -> None:
        """停止调度器。"""
        if self.running and self._scheduler:
            self._scheduler.shutdown(wait=False)
            logger.info("调度器已停止")

    def add_plugin_job(self, plugin_id: str, job: dict[str, Any]) -> None:
        """注册插件定时任务。"""
        func: Callable[..., Any] | None = job.get("func")
        if not callable(func):
            raise ValueError("插件任务缺少可调用的 func")

        trigger_type = str(job.get("trigger") or "interval")
        if trigger_type == "cron":
            trigger = _parse_cron(job.get("cron") or job.get("expression") or "")
        else:
            # 只传入非零单位；APScheduler 不接受 None
            units = {
                key: int(job.get(key) or 0)
                for key in ("weeks", "days", "hours", "minutes", "seconds")
                if int(job.get(key) or 0) > 0
            }
            if not units:
                units = {"minutes": 30}
            trigger = IntervalTrigger(timezone=settings.TIMEZONE, **units)

        job_id = f"{_PLUGIN_PREFIX}{plugin_id}.{job.get('id') or func.__name__}"
        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=job_id,
            name=job.get("name") or job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("已注册插件任务：%s", job_id)

    def remove_plugin_jobs(self, plugin_id: str) -> None:
        """移除某插件的全部任务。"""
        if not self._scheduler:
            return
        prefix = f"{_PLUGIN_PREFIX}{plugin_id}."
        for job in self._scheduler.get_jobs():
            if job.id.startswith(prefix):
                job.remove()

    def list_jobs(self) -> list[dict[str, Any]]:
        """列出所有任务。"""
        if not self._scheduler:
            return []
        jobs = []
        for job in self._scheduler.get_jobs():
            next_run = getattr(job, "next_run_time", None)
            jobs.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "trigger": str(job.trigger),
                    "next_run_time": next_run.isoformat() if next_run else None,
                }
            )
        return jobs

    async def run_job_now(self, job_id: str) -> bool:
        """立即执行一次任务。"""
        if not self._scheduler:
            return False
        job = self._scheduler.get_job(job_id)
        if not job:
            return False
        self._scheduler.modify_job(job_id, next_run_time=datetime.now())
        return True


#: 全局调度服务
scheduler_service = SchedulerService()
