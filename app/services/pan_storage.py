"""网盘管理服务：容量、目录浏览、转存待处理任务。

与「盘搜」的分工：盘搜负责**找到**分享链接（写进 ``download_tasks``，
kind=pan、status=pending），本服务负责**把它转存进自己的网盘**并推进任务状态。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import DownloadTask, PanSaveRecord, SiteConfig
from app.db.session import session_scope
from app.providers.panstorage.base import BasePanStorage
from app.schemas.enums import EventType, NotifyLevel, ProviderKind, ResourceKind, TaskStatus
from app.services import notify as notify_service
from app.services import sites as site_service
from app.utils.strings import format_size, truncate

logger = get_logger(__name__)


# ---------------- Provider 构建 ----------------
def storages(*, enabled_only: bool = True) -> list[BasePanStorage]:
    """所有网盘存储 Provider。"""
    return [
        provider
        for provider in site_service.build_providers(
            ProviderKind.PANSTORAGE.value, enabled_only=enabled_only
        )
        if isinstance(provider, BasePanStorage)
    ]


def get_storage(site_id: int) -> BasePanStorage | None:
    """按站点 ID 取网盘存储 Provider。"""
    provider = site_service.get_provider_by_site_id(site_id)
    return provider if isinstance(provider, BasePanStorage) else None


def default_storage(prefer: str | None = None) -> BasePanStorage | None:
    """取默认网盘（可按名字或站点名优先）。"""
    items = storages()
    if not items:
        return None
    if prefer:
        for item in items:
            if str(prefer) in (item.name, item.site_name, str(item.config.get("id"))):
                return item
    return items[0]


def _pick_for_share(share_url: str) -> BasePanStorage | None:
    """为某个分享链接挑选合适的网盘。

    优先选**同家网盘**（夸克分享优先给夸克），因为同家转存最可靠；
    没有同家则退回任意支持转存的网盘（如 AList 离线下载）。
    """
    candidates = [item for item in storages() if item.supports_save]
    if not candidates:
        return None
    lowered = str(share_url or "").lower()
    hints = {
        "quark": ("pan.quark.cn",),
        "alipan": ("alipan.com", "aliyundrive.com"),
        "baidu": ("pan.baidu.com",),
        # 键必须是 provider 的 name：115 的 Provider 叫 "pan115" 而不是 "115"，
        # 写错的话 115 分享永远匹配不到 115 网盘，会被兜底分给别的盘
        "pan115": ("115.com", "115cdn.com"),
        "xunlei": ("pan.xunlei.com",),
    }
    for item in candidates:
        for domains in hints.get(item.name, ()):
            if domains in lowered:
                return item
    return candidates[0]


def _unwrap(result: Any) -> tuple[bool, str]:
    """把 Provider 的返回值统一成 ``(是否成功, 消息)``。

    **为什么必须有它**：:class:`BasePanStorage` 声明 ``rename/move/copy``
    返回 ``bool``，但 115 的实现返回的是 ``(bool, str)``。元组非空恒为真，
    所以 ``if ok:`` 会把 ``(False, "目标目录不存在")`` 判成成功 ——
    115 上改名/移动失败会如实地被回报成"已重命名"。
    这里同时兼容两种形状，并优先采用 Provider 自己给出的原因说明
    （比服务层那句笼统的"失败（检查权限）"有用得多）。
    """
    if isinstance(result, tuple):
        ok = bool(result[0]) if result else False
        message = str(result[1]) if len(result) > 1 and result[1] else ""
        return ok, message
    return bool(result), ""


# ---------------- 只读查询 ----------------
async def overview() -> dict[str, Any]:
    """网盘总览：每个盘的容量与连通性。"""
    items: list[dict[str, Any]] = []
    with session_scope() as session:
        rows = {
            site.name: site.id
            for site in session.execute(
                select(SiteConfig).where(SiteConfig.kind == ProviderKind.PANSTORAGE.value)
            ).scalars()
        }

    for storage in storages():
        quota = await storage.quota()
        items.append(
            {
                "site_id": storage.config.get("id") or rows.get(storage.site_name),
                "name": storage.site_name,
                "provider": storage.name,
                "display_name": storage.display_name,
                "supports_save": storage.supports_save,
                "supports_delete": storage.supports_delete,
                # v1.7.0：能力位整体下发，前端按能力渲染文件管理按钮
                "capabilities": storage.capabilities(),
                "root_path": storage.root_path,
                "quota": quota.to_dict(),
            }
        )
    return {"total": len(items), "items": items}


async def list_files(site_id: int, path: str = "/") -> dict[str, Any]:
    """浏览某个网盘的目录。"""
    storage = get_storage(site_id)
    if not storage:
        return {"success": False, "message": "网盘不存在或未启用", "items": []}
    files = await storage.list_dir(path)
    current = storage.normalize_path(path)
    parent = storage.join_path(*current.split("/")[:-1]) if current != "/" else None
    return {
        "success": True,
        "name": storage.site_name,
        "path": current,
        "parent": parent,
        "total": len(files),
        "items": [item.to_dict() for item in files],
    }


# ---------------- 写操作 ----------------
async def save_share(
    share_url: str,
    *,
    site_id: int | None = None,
    password: str | None = None,
    target_dir: str | None = None,
    task_id: int | None = None,
) -> dict[str, Any]:
    """把一个分享链接转存进网盘。"""
    storage = get_storage(site_id) if site_id else _pick_for_share(share_url)
    if not storage:
        return {"success": False, "message": "没有可用的网盘存储，请先在站点管理中添加并启用"}
    if not storage.supports_save:
        return {"success": False, "message": f"{storage.site_name} 不支持从分享链接转存"}

    result = await storage.save_share(
        share_url, password=password, target_dir=target_dir
    )
    payload = {
        "success": result.success,
        "storage": storage.site_name,
        **result.to_dict(),
    }

    # 转存成功则推进对应下载任务的状态，避免反复转存
    if task_id and result.success:
        with session_scope() as session:
            task = session.get(DownloadTask, task_id)
            if task:
                task.status = TaskStatus.TRANSFERRED.value
                task.progress = 1.0
                task.error = None
                task.completed_at = utcnow()
                meta = dict(task.meta or {})
                meta.update(
                    {
                        "pan_storage": storage.site_name,
                        "saved_path": result.saved_path,
                    }
                )
                task.meta = meta
    elif task_id and not result.success:
        with session_scope() as session:
            task = session.get(DownloadTask, task_id)
            if task:
                task.error = result.message[:500]

    # 落一条转存记录，便于回溯「哪个分享转存到了哪里」
    try:
        with session_scope() as session:
            session.add(
                PanSaveRecord(
                    task_id=task_id,
                    storage=storage.site_name[:128],
                    share_url=share_url,
                    password=(password or None),
                    saved_path=result.saved_path,
                    file_count=result.file_count,
                    success=result.success,
                    message=result.message[:500] if result.message else None,
                )
            )
    except Exception as exc:  # pragma: no cover - 记录失败不影响转存结果
        logger.warning("写入转存记录失败: %s", exc)

    await notify_service.emit(
        EventType.PAN_SAVED.value if result.success else EventType.PAN_SAVE_FAILED.value,
        {"share_url": share_url, "storage": storage.site_name, "task_id": task_id},
    )
    return payload


async def transfer_pending(
    *, limit: int = 20, site_id: int | None = None, notify: bool = True
) -> dict[str, Any]:
    """批量转存所有待处理的网盘任务。

    这是「网盘管理」页与定时任务共用的入口：把盘搜命中后登记为
    ``pending`` 的网盘资源逐个转存进自己的网盘。
    """
    with session_scope() as session:
        tasks = [
            {
                "id": task.id,
                "title": task.title,
                "link": task.link,
                "password": (task.meta or {}).get("password"),
                "save_path": task.save_path,
            }
            for task in session.execute(
                select(DownloadTask)
                .where(
                    DownloadTask.kind == ResourceKind.PAN.value,
                    DownloadTask.status == TaskStatus.PENDING.value,
                )
                .order_by(DownloadTask.created_at.asc())
                .limit(max(limit, 1))
            ).scalars()
        ]

    stats = {"pending": len(tasks), "saved": 0, "failed": 0, "details": []}
    if not tasks:
        return stats

    if not storages():
        stats["message"] = "没有可用的网盘存储，请先在站点管理中添加并启用"
        return stats

    for task in tasks:
        result = await save_share(
            task["link"],
            site_id=site_id,
            password=task.get("password"),
            task_id=task["id"],
        )
        ok = bool(result.get("success"))
        stats["saved" if ok else "failed"] += 1
        stats["details"].append(
            {
                "task_id": task["id"],
                "title": truncate(task["title"], 60),
                "success": ok,
                "message": result.get("message", ""),
            }
        )

    logger.info(
        "网盘转存巡检：待处理 %d、成功 %d、失败 %d",
        stats["pending"],
        stats["saved"],
        stats["failed"],
    )

    if notify and stats["saved"]:
        lines = [
            f"· {item['title']}" for item in stats["details"] if item["success"]
        ][:10]
        await notify_service.send(
            f"已自动转存 {stats['saved']} 个网盘资源",
            "\n".join(lines),
            level=NotifyLevel.SUCCESS.value,
            event=EventType.PAN_SAVED.value,
        )
    return stats


async def delete_file(site_id: int, path: str, *, file_id: str | None = None) -> dict[str, Any]:
    """删除网盘中的文件或目录。"""
    storage = get_storage(site_id)
    if not storage:
        return {"success": False, "message": "网盘不存在或未启用"}
    if not storage.supports_delete:
        return {"success": False, "message": f"{storage.site_name} 不支持删除"}
    ok, detail = _unwrap(await storage.delete(path, file_id=file_id))
    return {
        "success": ok,
        "message": detail or ("已删除" if ok else "删除失败（检查权限或路径）"),
    }


async def make_dir(site_id: int, path: str) -> dict[str, Any]:
    """在网盘中创建目录。"""
    storage = get_storage(site_id)
    if not storage:
        return {"success": False, "message": "网盘不存在或未启用"}
    ok = await storage.mkdir(path)
    return {"success": ok, "message": "已创建" if ok else "创建失败"}


async def rename_file(
    site_id: int, path: str, new_name: str, *, file_id: str | None = None
) -> dict[str, Any]:
    """重命名网盘文件/目录。"""
    storage = get_storage(site_id)
    if not storage:
        return {"success": False, "message": "网盘不存在或未启用"}
    if not storage.supports_rename:
        return {"success": False, "message": f"{storage.site_name} 不支持重命名"}
    name = str(new_name or "").strip()
    if not name:
        return {"success": False, "message": "新名称不能为空"}
    ok, detail = _unwrap(await storage.rename(path, name, file_id=file_id))
    fallback = "已重命名" if ok else "重命名失败（检查权限或同名冲突）"
    return {"success": ok, "message": detail or fallback}


async def move_file(
    site_id: int,
    path: str,
    target_dir: str,
    *,
    file_id: str | None = None,
    copy: bool = False,
) -> dict[str, Any]:
    """移动或复制网盘文件/目录。``copy=True`` 时走复制。"""
    storage = get_storage(site_id)
    if not storage:
        return {"success": False, "message": "网盘不存在或未启用"}
    if not storage.supports_move:
        return {"success": False, "message": f"{storage.site_name} 不支持移动/复制"}
    if not str(target_dir or "").strip():
        return {"success": False, "message": "目标目录不能为空"}
    action = "复制" if copy else "移动"
    ok, detail = _unwrap(
        await (
            storage.copy(path, target_dir, file_id=file_id)
            if copy
            else storage.move(path, target_dir, file_id=file_id)
        )
    )
    fallback = f"已{action}" if ok else f"{action}失败（检查目标目录是否存在）"
    return {"success": ok, "message": detail or fallback}


async def search_files(
    site_id: int, keyword: str, *, limit: int = 50
) -> dict[str, Any]:
    """盘内搜索文件。"""
    storage = get_storage(site_id)
    if not storage:
        return {"success": False, "message": "网盘不存在或未启用", "items": []}
    if not storage.supports_search:
        return {
            "success": False,
            "message": f"{storage.site_name} 不支持盘内搜索",
            "items": [],
        }
    word = str(keyword or "").strip()
    if not word:
        return {"success": False, "message": "关键词不能为空", "items": []}
    files = await storage.search(word, limit=limit)
    return {
        "success": True,
        "name": storage.site_name,
        "keyword": word,
        "total": len(files),
        "items": [item.to_dict() for item in files],
    }


async def keep_alive_all(*, notify: bool = True) -> dict[str, Any]:
    """对全部启用的网盘做一次凭据保活巡检。

    网盘 Cookie 最常见的故障是「静默过期」——任务半夜跑失败了才发现。
    这里主动轮一遍，**失效时主动推通知**（v1.12.0 补上）。

    ⚠️ 本函数原先的文档写着"异常时走通知"，但代码里**根本没有发通知**：
    保活发现 Cookie 失效后只改了状态，用户得自己想起来去页面看。
    这正是路线图里「网盘登录态失效主动通知」那一条要解决的问题。

    通知按「站点名」去抖（``settings.NOTIFY_ALERT_COOLDOWN_MINUTES``），
    否则每 6 小时一轮保活会把同一条失效告警反复推出去。
    ``notify=False`` 供用户在界面手点巡检时使用——他正看着结果，再推一条是噪音。
    """
    items: list[dict[str, Any]] = []
    for storage in storages():
        site_id = storage.config.get("id")
        if not storage.supports_keepalive:
            items.append(
                {
                    "site_id": site_id,
                    "name": storage.site_name,
                    "provider": storage.name,
                    "skipped": True,
                    "success": True,
                    "message": "该网盘无需保活",
                }
            )
            continue
        try:
            ok, message = await storage.keep_alive()
        except Exception as exc:  # 单个盘异常不能影响其它盘
            ok, message = False, f"保活异常：{exc}"
        items.append(
            {
                "site_id": site_id,
                "name": storage.site_name,
                "provider": storage.name,
                "skipped": False,
                "success": ok,
                "message": message,
            }
        )
    failed = [i for i in items if not i["success"]]

    if notify and failed:
        for item in failed:
            await notify_service.send(
                f"网盘登录已失效：{item['name']}",
                f"{item['message']}\n"
                "请到「网盘管理」页重新扫码登录或更新 Cookie，"
                "否则自动转存与 STRM 同步都会失败。",
                level=NotifyLevel.WARNING.value,
                event=EventType.SITE_UNHEALTHY.value,
                suppress_key=f"pan.keepalive:{item['name']}",
                suppress_seconds=int(settings.NOTIFY_ALERT_COOLDOWN_MINUTES) * 60,
            )
    if notify:
        # 恢复即清抑制，保证"失效→修好→又失效"能再次收到告警
        for item in items:
            if item["success"]:
                notify_service.clear_suppression(f"pan.keepalive:{item['name']}")

    return {
        "total": len(items),
        "failed": len(failed),
        "items": items,
    }


async def resolve_download_url(
    site_id: int, path: str, *, file_id: str | None = None
) -> dict[str, Any]:
    """换取临时直链（可投给 aria2 或生成 STRM）。"""
    storage = get_storage(site_id)
    if not storage:
        return {"success": False, "message": "网盘不存在或未启用"}
    url = await storage.download_url(path, file_id=file_id)
    if not url:
        return {"success": False, "message": "该网盘不支持或换取直链失败"}
    return {"success": True, "url": url}


def list_save_records(*, limit: int = 100) -> list[dict[str, Any]]:
    """查询转存记录。"""
    with session_scope() as session:
        rows = session.execute(
            select(PanSaveRecord).order_by(PanSaveRecord.created_at.desc()).limit(limit)
        ).scalars()
        return [
            {
                "id": row.id,
                "task_id": row.task_id,
                "storage": row.storage,
                "share_url": row.share_url,
                "saved_path": row.saved_path,
                "file_count": row.file_count,
                "success": row.success,
                "message": row.message,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]


async def test_storage(site_id: int) -> dict[str, Any]:
    """连通性测试。"""
    storage = get_storage(site_id)
    if not storage:
        return {"success": False, "message": "网盘不存在"}
    ok, message = await storage.health_check()
    quota = await storage.quota()
    return {
        "success": ok,
        "message": message,
        "quota": quota.to_dict(),
        "capacity_text": (
            f"{format_size(quota.used)} / {format_size(quota.total)}"
            if quota.total
            else "容量未知"
        ),
    }
