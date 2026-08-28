"""定时调度：订阅巡检、追新雷达、下载状态同步、媒体库扫描、插件任务。

内置任务的**触发规则可在界面上修改并持久化**：静态配置（``.env`` /
``config.yaml``）提供默认值，用户改动写入 ``settings`` 表，重启后依然生效。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.logger import get_logger
from app.services import settings_store

logger = get_logger(__name__)

JOB_SUBSCRIBE = "cineflow.subscribe"
JOB_DOWNLOAD = "cineflow.download"
JOB_LIBRARY = "cineflow.library"
JOB_RADAR = "cineflow.radar"
JOB_PAN_TRANSFER = "cineflow.pan_transfer"
JOB_PAN_SUBSCRIBE = "cineflow.pan_subscribe"
JOB_STRM_SYNC = "cineflow.strm_sync"
JOB_SCRAPE = "cineflow.scrape"
JOB_UPGRADE = "cineflow.upgrade"
JOB_SITE_HEALTH = "cineflow.site_health"
JOB_RANKING = "cineflow.ranking"
_PLUGIN_PREFIX = "plugin."

#: 间隔型任务允许的分钟范围
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 7 * 24 * 60


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


def validate_cron(expression: str) -> str:
    """校验 5 段 cron，非法则抛 ``ValueError``。

    对外暴露是为了让运行期配置层（``config_store``）复用**同一份**规则，
    避免出现"设置页放过了、调度器却起不来"的两份校验漂移。
    """
    _parse_cron(expression)
    return str(expression).strip()


@dataclass(frozen=True)
class JobSpec:
    """内置任务的描述与默认触发规则。"""

    key: str
    job_id: str
    name: str
    description: str
    trigger: str = "interval"
    minutes: int = 30
    cron: str = "0 4 * * *"
    enabled: bool = True

    def defaults(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "trigger": self.trigger,
            "minutes": self.minutes,
            "cron": self.cron,
        }


def builtin_specs() -> list[JobSpec]:
    """内置任务规格（默认值来自静态配置，随配置变化）。"""
    return [
        JobSpec(
            key="subscribe",
            job_id=JOB_SUBSCRIBE,
            name="订阅巡检（自动追新）",
            description="逐个活跃订阅去各站点搜索缺失集，适合补全历史缺集",
            trigger="interval",
            minutes=settings.SUBSCRIBE_INTERVAL_MINUTES,
            enabled=True,
        ),
        JobSpec(
            key="radar",
            job_id=JOB_RADAR,
            name="追新雷达（站点最新流巡检）",
            description="只拉一次各站点最新发布流再匹配全部订阅，发现新集延迟最低",
            trigger="interval",
            minutes=settings.RADAR_INTERVAL_MINUTES or 15,
            enabled=bool(settings.RADAR_ENABLED and settings.RADAR_INTERVAL_MINUTES > 0),
        ),
        JobSpec(
            key="download",
            job_id=JOB_DOWNLOAD,
            name="下载状态同步与自动整理",
            description="同步下载器进度，完成后自动硬链入库并刷新媒体服务器",
            trigger="interval",
            minutes=settings.DOWNLOAD_CHECK_INTERVAL_MINUTES,
            enabled=True,
        ),
        JobSpec(
            key="pan_transfer",
            job_id=JOB_PAN_TRANSFER,
            name="网盘待转存队列",
            description="把命中网盘资源但尚未转存的任务批量转存到已配置的网盘",
            trigger="interval",
            minutes=settings.PAN_TRANSFER_INTERVAL_MINUTES or 20,
            enabled=bool(settings.PAN_AUTO_SAVE and settings.PAN_TRANSFER_INTERVAL_MINUTES > 0),
        ),
        JobSpec(
            key="pan_subscribe",
            job_id=JOB_PAN_SUBSCRIBE,
            name="网盘分享追更",
            description="盯住持续更新的分享链接，只转存新增文件（增量追更）",
            trigger="interval",
            minutes=settings.PAN_SUBSCRIBE_INTERVAL_MINUTES or 60,
            enabled=bool(settings.PAN_SUBSCRIBE_INTERVAL_MINUTES > 0),
        ),
        JobSpec(
            key="strm_sync",
            job_id=JOB_STRM_SYNC,
            name="网盘 STRM 同步",
            description="把网盘目录映射成本地 STRM 文件，并清理失效条目",
            trigger="interval",
            minutes=settings.STRM_SYNC_INTERVAL_MINUTES or 120,
            enabled=bool(settings.STRM_SYNC_INTERVAL_MINUTES > 0),
        ),
        JobSpec(
            key="site_health",
            job_id=JOB_SITE_HEALTH,
            name="站点健康巡检",
            description="逐个探测已启用站点，Cookie 过期/掉线时告警（静默 0 结果是最难发现的故障）",
            trigger="interval",
            minutes=settings.SITE_HEALTH_INTERVAL_MINUTES or 180,
            enabled=bool(
                settings.SITE_HEALTH_ENABLED and settings.SITE_HEALTH_INTERVAL_MINUTES > 0
            ),
        ),
        JobSpec(
            key="ranking",
            job_id=JOB_RANKING,
            name="榜单自动订阅",
            description="按已启用的榜单规则自动建订阅（TMDB 热门/高分等），单次有数量上限",
            trigger="interval",
            minutes=settings.RANKING_INTERVAL_MINUTES or 720,
            enabled=bool(settings.RANKING_INTERVAL_MINUTES > 0),
        ),
        JobSpec(
            key="scrape",
            job_id=JOB_SCRAPE,
            name="媒体库补刮（NFO + 图片）",
            description="为缺少 NFO 的历史文件补齐元数据，提升媒体服务器识别率",
            trigger="cron",
            cron=settings.SCRAPE_CRON or "30 4 * * *",
            enabled=bool(settings.SCRAPE_ENABLED and settings.SCRAPE_CRON),
        ),
        JobSpec(
            key="upgrade",
            job_id=JOB_UPGRADE,
            name="洗版巡检（更优版本替换）",
            description="为开启「最优版本」的订阅寻找更高画质并替换已入库文件",
            trigger="interval",
            minutes=max(settings.SUBSCRIBE_INTERVAL_MINUTES * 4, 60),
            enabled=bool(settings.UPGRADE_ENABLED),
        ),
        JobSpec(
            key="library",
            job_id=JOB_LIBRARY,
            name="媒体库全量扫描",
            description="重建入库文件索引，用于缺集计算与去重",
            trigger="cron",
            cron=settings.LIBRARY_SCAN_CRON,
            enabled=True,
        ),
    ]


def _spec_map() -> dict[str, JobSpec]:
    return {spec.key: spec for spec in builtin_specs()}


def _spec(key: str) -> JobSpec:
    """按 key 取任务规格，未知 key 抛 ``ValueError``。"""
    spec = _spec_map().get(key)
    if spec is None:
        raise ValueError(f"未知任务: {key}")
    return spec


def _overrides() -> dict[str, Any]:
    raw = settings_store.get_setting(settings_store.KEY_SCHEDULES, {}) or {}
    return raw if isinstance(raw, dict) else {}


def effective_schedule(key: str) -> dict[str, Any]:
    """某个内置任务当前生效的触发规则（默认值 + 用户覆盖）。"""
    config = _spec(key).defaults()
    override = _overrides().get(key) or {}
    if isinstance(override, dict):
        for field in ("enabled", "trigger", "minutes", "cron"):
            if field in override and override[field] is not None:
                config[field] = override[field]
    config["customized"] = bool(override)
    return config


def normalize_schedule(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """校验并规范化一份触发规则，非法时抛 ``ValueError``。"""
    current = effective_schedule(key)
    trigger = str(payload.get("trigger") or current["trigger"]).lower()
    if trigger not in ("interval", "cron"):
        raise ValueError("trigger 只能是 interval 或 cron")

    enabled = payload.get("enabled")
    enabled = current["enabled"] if enabled is None else bool(enabled)

    minutes = payload.get("minutes")
    minutes = current["minutes"] if minutes in (None, "") else int(minutes)
    cron = str(payload.get("cron") or current["cron"]).strip()

    if trigger == "interval" and not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
        raise ValueError(
            f"间隔需在 {MIN_INTERVAL_MINUTES}~{MAX_INTERVAL_MINUTES} 分钟之间"
        )
    # cron 字段**只要用户提交了就校验**，不管当前 trigger 是不是 cron：
    # 否则非法表达式会被静默存下来，等用户哪天把 trigger 切成 cron 才炸，
    # 那时他早就忘了自己填过什么（真实踩坑：冒烟测试发现 interval 任务能存 "这不是 cron"）。
    if payload.get("cron") not in (None, "") or trigger == "cron":
        _parse_cron(cron)  # 非法表达式直接抛错

    return {"enabled": enabled, "trigger": trigger, "minutes": minutes, "cron": cron}


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

    # ---------------- 内置任务 ----------------
    def _job_target(self, key: str) -> tuple[Callable[..., Any], dict[str, Any]]:
        """任务函数与调用参数。"""
        from app.services import download as download_service
        from app.services import library as library_service
        from app.services import pan_storage as pan_service
        from app.services import pan_subscribe as pan_subscribe_service
        from app.services import radar as radar_service
        from app.services import ranking as ranking_service
        from app.services import scraper as scraper_service
        from app.services import site_health as health_service
        from app.services import strm_sync as strm_service
        from app.services import subscribe as subscribe_service
        from app.services import upgrade as upgrade_service

        targets: dict[str, tuple[Callable[..., Any], dict[str, Any]]] = {
            "subscribe": (subscribe_service.run_all, {}),
            "radar": (
                radar_service.run,
                {"limit_per_site": settings.RADAR_LIMIT_PER_SITE},
            ),
            "download": (download_service.sync_tasks, {}),
            "library": (library_service.scan_library, {}),
            "pan_transfer": (
                pan_service.transfer_pending,
                {"limit": settings.PAN_TRANSFER_BATCH},
            ),
            "pan_subscribe": (pan_subscribe_service.check_all, {}),
            "strm_sync": (strm_service.sync_all, {}),
            "scrape": (
                scraper_service.scrape_library,
                {"limit": settings.SCRAPE_BATCH},
            ),
            "upgrade": (upgrade_service.run, {}),
            "site_health": (health_service.check_all, {}),
            "ranking": (ranking_service.run, {}),
        }
        if key not in targets:
            raise ValueError(f"未知任务: {key}")
        return targets[key]

    def _register(self, spec: JobSpec) -> bool:
        """按当前生效规则注册（或移除）一个内置任务。"""
        config = effective_schedule(spec.key)
        if not config["enabled"]:
            self.remove_job(spec.job_id)
            logger.info("任务 %s 已按配置禁用", spec.job_id)
            return False

        if config["trigger"] == "cron":
            trigger: Any = _parse_cron(config["cron"])
        else:
            trigger = IntervalTrigger(
                minutes=int(config["minutes"]), timezone=settings.TIMEZONE
            )

        func, kwargs = self._job_target(spec.key)
        self.scheduler.add_job(
            func,
            trigger=trigger,
            id=spec.job_id,
            name=spec.name,
            kwargs=kwargs or None,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        return True

    def start(self) -> None:
        """启动调度器并注册内置任务。"""
        if not settings.SCHEDULER_ENABLED:
            logger.info("调度器已被配置禁用")
            return
        if self.running:
            return

        registered = []
        for spec in builtin_specs():
            try:
                if self._register(spec):
                    registered.append(spec.key)
            except ValueError as exc:
                logger.warning("任务 %s 未注册: %s", spec.job_id, exc)

        self.scheduler.start()
        logger.info("调度器已启动，内置任务 %d 个：%s", len(registered), registered)
        for item in self.list_jobs():
            logger.info(
                "  · %-22s %s -> %s", item["id"], item["trigger"], item["next_run_time"]
            )

    def refresh_builtin_jobs(self) -> list[str]:
        """按当前生效配置重建全部内置任务的触发器。

        运行期配置（``config_store``）改了周期类配置后调用：
        ``builtin_specs()`` 每次都从 ``settings`` 现读，所以重跑一遍
        ``_register`` 就能让新周期立即生效，不需要重启进程。
        """
        if not self.running:
            return []
        changed: list[str] = []
        for spec in builtin_specs():
            try:
                if self._register(spec):
                    changed.append(spec.key)
            except ValueError as exc:
                logger.warning("任务 %s 重建失败: %s", spec.job_id, exc)
        logger.info("内置任务已按新配置重建：%s", changed)
        return changed

    def shutdown(self) -> None:
        """停止调度器。"""
        if self.running and self._scheduler:
            self._scheduler.shutdown(wait=False)
            logger.info("调度器已停止")

    def remove_job(self, job_id: str) -> bool:
        """移除指定任务（不存在时静默返回）。"""
        if not self._scheduler:
            return False
        job = self._scheduler.get_job(job_id)
        if not job:
            return False
        job.remove()
        return True

    def update_schedule(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        """修改某个内置任务的触发规则：校验 → 持久化 → 立即改期。"""
        config = normalize_schedule(key, payload)
        overrides = _overrides()
        overrides[key] = config
        settings_store.set_setting(settings_store.KEY_SCHEDULES, overrides)

        spec = _spec(key)
        active = False
        if self.running:
            active = self._register(spec)
        logger.info(
            "任务 %s 规则已更新：%s（已生效=%s）", spec.job_id, config, active
        )
        return {**self.describe_schedule(key), "applied": active}

    def reset_schedule(self, key: str) -> dict[str, Any]:
        """清除用户覆盖，回到静态配置的默认值。"""
        overrides = _overrides()
        if key in overrides:
            overrides.pop(key)
            settings_store.set_setting(settings_store.KEY_SCHEDULES, overrides)
        spec = _spec(key)
        if self.running:
            self._register(spec)
        return self.describe_schedule(key)

    def describe_schedule(self, key: str) -> dict[str, Any]:
        """单个内置任务的完整描述（含运行时状态）。"""
        spec = _spec(key)
        config = effective_schedule(key)
        job = self._scheduler.get_job(spec.job_id) if self._scheduler else None
        next_run = getattr(job, "next_run_time", None) if job else None
        return {
            "key": spec.key,
            "id": spec.job_id,
            "name": spec.name,
            "description": spec.description,
            "enabled": bool(config["enabled"]),
            "trigger": config["trigger"],
            "minutes": int(config["minutes"]),
            "cron": config["cron"],
            "customized": bool(config["customized"]),
            "default": spec.defaults(),
            "scheduled": job is not None,
            "trigger_text": str(job.trigger) if job else "",
            "next_run_time": next_run.isoformat() if next_run else None,
        }

    def describe_schedules(self) -> list[dict[str, Any]]:
        """全部内置任务的可编辑描述。"""
        return [self.describe_schedule(spec.key) for spec in builtin_specs()]

    # ---------------- 插件任务 ----------------
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
        specs = {spec.job_id: spec.key for spec in builtin_specs()}
        jobs = []
        for job in self._scheduler.get_jobs():
            next_run = getattr(job, "next_run_time", None)
            jobs.append(
                {
                    "id": job.id,
                    "key": specs.get(job.id),
                    "name": job.name,
                    "builtin": job.id in specs,
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
