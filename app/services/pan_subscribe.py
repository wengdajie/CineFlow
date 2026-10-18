"""网盘分享追更订阅。

**要解决的问题**：v1.3.0 的转存是**一次性**的——贴一个分享链接、转存一次、结束。
但连载剧的分享链接是**持续更新**的：今天 12 集，下周 14 集。
用户不该每周手动来点一次。

对标 quark-auto-save（★3000+）的任务模型，本模块提供：

- **增量转存**：记住已转存过的文件名，每次只转新增的
- **正则过滤**：``include_regex`` / ``exclude_regex`` 控制哪些文件要
- **正则重命名**：把 ``[字幕组]xxx.第12集.1080p.mp4`` 规整成 ``S01E12.mp4``
- **失效标记**：连续失败到阈值就停手，不再无意义地重试
- **执行窗口**：``expire_at`` 到期停止、``weekdays`` 只在指定星期几跑

与 ``subscribes`` 表的区别：那个是「按片名去各站搜」，
这个是「盯死一个已知分享链接」，两者互补。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import PanSubscribe
from app.db.session import session_scope
from app.providers.panstorage.base import PanFile
from app.schemas.enums import EventType, NotifyLevel, SubscribeStatus
from app.utils.strings import truncate

logger = get_logger(__name__)


# 单个订阅最多记忆多少条已转存文件。超限时截断末尾（保留最近转存的）。
# 防止 JSON 字段无限膨胀导致页面加载慢 + 备份文件大。
_MAX_SAVED_FILES = 1000


def _as_naive_utc(value: Any) -> datetime | None:
    """把用户提交的到期时间归一成**naive UTC**再落库。

    ``PanSubscribe.expire_at`` 是不带时区的列，而 API 收到的可能是
    ``2026-10-01T12:00:00+08:00``。直接存 aware 值的话，
    :func:`_should_run` 里 ``expire.replace(tzinfo=utc)`` 会把 +08:00
    当成 UTC 读，**期限凭空多出 8 小时**；混存 naive/aware 还会让
    SQLAlchemy 的比较在某些后端上直接抛 TypeError。
    """
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("到期时间格式无法解析，已忽略：%s", value)
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _compile(pattern: str | None) -> re.Pattern[str] | None:
    """编译用户填的正则；写错了就当没填，**不能让界面上一个错正则搞崩巡检**。"""
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        logger.warning("正则无效，已忽略：%s（%s）", pattern, exc)
        return None


def saved_key(item: PanFile) -> str:
    """这个文件在「已转存清单」里的身份。

    用**分享内的完整路径**而不是文件名：递归列举后同名文件会来自不同
    子目录（``/第一季/EP01.mp4`` 与 ``/第二季/EP01.mp4`` 都叫 ``EP01.mp4``），
    只按名字去重会让第二季永远被当成「已转存过」而整季漏掉。

    ``path`` 缺失时退回文件名，保证老数据与不填 path 的 Provider 仍能工作。
    """
    return str(item.path or "").strip() or item.name


def match_files(
    files: list[PanFile],
    *,
    include: str | None = None,
    exclude: str | None = None,
    saved: list[str] | None = None,
) -> list[PanFile]:
    """挑出「需要转存」的文件：符合过滤规则且尚未转存过。

    去重同时认**路径**和**文件名**两种历史记录：v1.28.0 之前存的是纯文件名，
    升级后如果只比路径，所有老订阅会把已转存过的文件重新转一遍
    （网盘侧变成一堆 ``xxx(1).mp4``）。两种都查一遍才是无痛升级。

    正则一律匹配**文件名**而不是路径：用户填 ``EP\\d+`` 是在描述文件，
    拿整条路径去匹配会让父目录名意外参与判断。
    """
    include_re = _compile(include)
    exclude_re = _compile(exclude)
    already = set(saved or [])
    selected: list[PanFile] = []
    for item in files:
        if item.is_dir:
            continue
        if saved_key(item) in already or item.name in already:
            continue
        if include_re and not include_re.search(item.name):
            continue
        if exclude_re and exclude_re.search(item.name):
            continue
        selected.append(item)
    return selected


def apply_rename(name: str, search: str | None, replace: str | None) -> str:
    """按正则重命名；规则无效或没匹配上就原样返回。"""
    if not search:
        return name
    pattern = _compile(search)
    if not pattern:
        return name
    try:
        return pattern.sub(replace or "", name) or name
    except re.error as exc:
        logger.warning("重命名替换失败 %s: %s", search, exc)
        return name


def _should_run(record: PanSubscribe, *, now: datetime | None = None) -> tuple[bool, str]:
    """判断此刻是否该执行这条订阅。"""
    moment = now or datetime.now(timezone.utc)
    if record.invalid:
        return False, "分享已失效"
    if record.status != SubscribeStatus.ACTIVE.value:
        return False, f"状态为 {record.status}"
    if record.expire_at:
        expire = record.expire_at
        if expire.tzinfo is None:
            expire = expire.replace(tzinfo=timezone.utc)
        if expire < moment:
            return False, "已过任务期限"
    # Python 的 weekday()：周一=0 … 周日=6，与界面一致
    if record.weekdays and moment.weekday() not in [int(day) for day in record.weekdays]:
        return False, "今天不在执行星期内"
    return True, "ok"


def _storage_for(record: PanSubscribe):
    """挑网盘：指定了就用指定的，否则按分享链接自动挑同家网盘。"""
    from app.services import pan_storage

    if record.site_id:
        return pan_storage.get_storage(int(record.site_id))
    return pan_storage._pick_for_share(record.share_url)


_inflight: set[int] = set()


async def check_one(subscribe_id: int, *, notify: bool = True) -> dict[str, Any]:
    if subscribe_id in _inflight:
        return {"success": True, "skipped": True, "saved": 0, "message": "该分享订阅正在巡检"}
    _inflight.add(subscribe_id)
    try:
        return await _check_one(subscribe_id, notify=notify)
    finally:
        _inflight.discard(subscribe_id)


async def _check_one(subscribe_id: int, *, notify: bool = True) -> dict[str, Any]:
    """巡检单条分享追更订阅，只转存新增文件。"""
    with session_scope() as session:
        record = session.get(PanSubscribe, subscribe_id)
        if not record:
            return {"success": False, "message": "订阅不存在"}
        runnable, reason = _should_run(record)
        snapshot = {
            "id": record.id,
            "name": record.name,
            "share_url": record.share_url,
            "password": record.password,
            "site_id": record.site_id,
            "target_dir": record.target_dir,
            "include_regex": record.include_regex,
            "exclude_regex": record.exclude_regex,
            "rename_search": record.rename_search,
            "rename_replace": record.rename_replace,
            "saved_files": list(record.saved_files or []),
            "failure_count": record.failure_count,
        }
    if not runnable:
        return {"success": True, "skipped": True, "message": reason, "saved": 0}

    storage = None
    with session_scope() as session:
        record = session.get(PanSubscribe, subscribe_id)
        storage = _storage_for(record) if record else None

    if not storage:
        return {
            "success": False,
            "saved": 0,
            "message": "没有可用的网盘存储，请先在站点管理中添加并启用",
        }

    # 列分享内容；网盘不支持列举时退化为整体转存
    files = await storage.list_share(snapshot["share_url"], password=snapshot["password"])
    result: dict[str, Any] = {"success": False, "saved": 0, "message": ""}

    if not files:
        # 退化路径：无法看清分享内部，只能整体转存一次。
        # 这里用 saved_files 里的哨兵值保证不会每小时重复整体转存。
        sentinel = "__whole_share__"
        if sentinel in snapshot["saved_files"]:
            return {"success": True, "saved": 0, "message": "该网盘不支持增量，整体转存已完成过"}
        outcome = await storage.save_share(
            snapshot["share_url"],
            password=snapshot["password"],
            target_dir=snapshot["target_dir"],
        )
        result["success"] = outcome.success
        result["saved"] = outcome.file_count
        result["message"] = outcome.message
        if outcome.success:
            snapshot["saved_files"].append(sentinel)
    else:
        pending = match_files(
            files,
            include=snapshot["include_regex"],
            exclude=snapshot["exclude_regex"],
            saved=snapshot["saved_files"],
        )
        if not pending:
            _finish(subscribe_id, snapshot, success=True, message="没有新增文件", saved=0)
            return {"success": True, "saved": 0, "message": "没有新增文件"}

        outcome = await storage.save_share_files(
            snapshot["share_url"],
            pending,
            password=snapshot["password"],
            target_dir=snapshot["target_dir"],
        )
        result["success"] = outcome.success
        result["saved"] = len(pending) if outcome.success else 0
        result["message"] = outcome.message
        if outcome.success:
            snapshot["saved_files"].extend(saved_key(item) for item in pending)
            # 转存成功后按规则重命名（网盘侧改名，失败不影响转存结果）
            if snapshot["rename_search"]:
                await _rename_saved(storage, snapshot, pending)

    receipt = _finish(
        subscribe_id,
        snapshot,
        success=result["success"],
        message=result["message"],
        saved=result["saved"],
    )

    if notify and receipt.get("invalidated"):
        # 追更自己停了必须告诉用户。否则他要等到「怎么好久没更新」才察觉，
        # 而那时的排查方向通常是错的（会去怀疑分享链接，而不是登录态）。
        from app.services import notify as notify_service

        await notify_service.send(
            f"分享追更已失效：{receipt.get('name') or snapshot['name']}",
            f"连续失败 {receipt.get('failure_count')} 次，已自动停止巡检。\n"
            f"最后一次失败原因：{truncate(result['message'], 160)}\n"
            "请检查分享链接是否仍有效、网盘 Cookie 是否过期；"
            "修好后在任务上勾选「清除失效标记」即可重新开始。",
            level=NotifyLevel.WARNING.value,
            event=EventType.SITE_UNHEALTHY.value,
            suppress_key=f"pan_subscribe.invalid:{subscribe_id}",
            suppress_seconds=86400,
        )

    if notify and result["saved"]:
        from app.services import notify as notify_service

        await notify_service.send(
            f"分享追更：{snapshot['name']} 新增 {result['saved']} 个文件",
            truncate(result["message"], 200),
            level=NotifyLevel.SUCCESS.value,
            event=EventType.PAN_SAVED.value,
        )
    return result


async def _rename_saved(storage, snapshot: dict[str, Any], files: list[PanFile]) -> int:
    """转存落地后按正则重命名（网盘需支持 rename，否则跳过）。"""
    if not getattr(storage, "supports_rename", False):
        return 0
    target_dir = snapshot["target_dir"] or storage.root_path
    count = 0
    for item in files:
        new_name = apply_rename(
            item.name, snapshot["rename_search"], snapshot["rename_replace"]
        )
        if new_name == item.name:
            continue
        # 转存会**保留分享内的目录结构**，所以落地路径 = 落地目录 + 分享内的父目录。
        # 只拼 target_dir + 文件名的话，凡是嵌在子目录里的文件都会因为
        # 「路径不存在」而静默改名失败（rename 返回 False，没人看得见）。
        share_dir = str((item.extra or {}).get("share_dir") or "").strip()
        landed = storage.join_path(target_dir, share_dir, item.name)
        try:
            if await storage.rename(landed, new_name):
                count += 1
            else:
                logger.debug("重命名未生效（网盘拒绝或路径不存在）：%s", landed)
        except Exception as exc:
            logger.debug("重命名失败 %s: %s", item.name, exc)
    return count


def _finish(
    subscribe_id: int,
    snapshot: dict[str, Any],
    *,
    success: bool,
    message: str,
    saved: int,
) -> dict[str, Any]:
    """回写巡检结果：已转存清单、失败计数、失效标记。

    返回 ``{"invalidated", "name", "failure_count"}`` 回执。
    「本轮是否刚好被判定失效」只有这里算得出（依赖读改写后的计数），
    但**通知不能在这里发**：本函数是同步的，而且还持着数据库事务 ——
    一次慢速 webhook 会把事务和它的行锁一起拖住。所以把结论交给
    异步调用方 :func:`check_one` 去推送。
    """
    just_invalidated = False
    name = ""
    failures = 0
    with session_scope() as session:
        record = session.get(PanSubscribe, subscribe_id)
        if not record:
            return {"invalidated": False, "name": "", "failure_count": 0}
        record.last_checked_at = utcnow()
        record.last_message = (message or "")[:500]
        record.saved_files = snapshot["saved_files"]
        if success:
            record.failure_count = 0
            record.total_saved = (record.total_saved or 0) + saved
            # 防 saved_files 无限膨胀：JSON 字段没有长度约束，追更跑几年后
            # 这个数组会拖慢每一次列表查询（它随行一起被读出来）。
            # 只保留最近的 N 条：更早的文件早就不在分享里了，留着也没用。
            if len(record.saved_files or []) > _MAX_SAVED_FILES:
                record.saved_files = record.saved_files[-_MAX_SAVED_FILES:]
        else:
            record.failure_count = (record.failure_count or 0) + 1
            # 连续失败到阈值就认定分享失效，停止无意义的重试
            if record.failure_count >= max(settings.PAN_SUBSCRIBE_MAX_FAILURES, 1):
                record.invalid = True
                record.status = SubscribeStatus.FAILED.value
                just_invalidated = True
                logger.warning(
                    "分享追更「%s」连续失败 %d 次，已标记失效",
                    record.name,
                    record.failure_count,
                )
        name = record.name
        failures = record.failure_count or 0
    return {
        "invalidated": just_invalidated,
        "name": name,
        "failure_count": failures,
    }


async def check_all(*, limit: int = 50, notify: bool = True) -> dict[str, Any]:
    """巡检所有有效的分享追更订阅（供定时任务调用）。"""
    with session_scope() as session:
        ids = [
            row.id
            for row in session.execute(
                select(PanSubscribe)
                .where(
                    PanSubscribe.status == SubscribeStatus.ACTIVE.value,
                    PanSubscribe.invalid.is_(False),
                )
                .order_by(PanSubscribe.last_checked_at.asc().nullsfirst())
                .limit(max(limit, 1))
            ).scalars()
        ]

    stats: dict[str, Any] = {
        "checked": 0,
        "saved": 0,
        "failed": 0,
        "skipped": 0,
        "details": [],
    }
    for subscribe_id in ids:
        outcome = await check_one(subscribe_id, notify=notify)
        stats["checked"] += 1
        if outcome.get("skipped"):
            stats["skipped"] += 1
        elif outcome.get("success"):
            stats["saved"] += outcome.get("saved", 0)
        else:
            stats["failed"] += 1
        stats["details"].append({"id": subscribe_id, **outcome})

    stats["message"] = (
        f"巡检 {stats['checked']} 条分享追更，新增转存 {stats['saved']} 个文件，"
        f"失败 {stats['failed']}，跳过 {stats['skipped']}"
    )
    logger.info(stats["message"])
    return stats


# ---------------- CRUD ----------------
def create(payload: dict[str, Any]) -> dict[str, Any]:
    """新建分享追更订阅。"""
    with session_scope() as session:
        record = PanSubscribe(
            name=str(payload.get("name") or "未命名")[:255],
            share_url=str(payload.get("share_url") or ""),
            password=payload.get("password") or None,
            site_id=payload.get("site_id"),
            target_dir=payload.get("target_dir") or None,
            include_regex=payload.get("include_regex") or None,
            exclude_regex=payload.get("exclude_regex") or None,
            rename_search=payload.get("rename_search") or None,
            rename_replace=payload.get("rename_replace") or None,
            weekdays=_validated_weekdays(payload.get("weekdays")),
            expire_at=_as_naive_utc(payload.get("expire_at")),
            saved_files=[],
        )
        session.add(record)
        session.flush()
        return _to_dict(record)


def _validated_weekdays(raw: Any) -> list[int]:
    """校验并归一星期几范围（0-6），超范围值会被静默丢弃。

    用户手写 ``[99]`` 或拖动时误触会存进数据库。``_should_run()``
    看到 ``moment.weekday() not in [99]`` 永远为 True，订阅就**永远不执行**。
    更糟的是这个错误不报、不提示 —— 用户创建完就以为设置好了，第二天发现
    没更新，排查方向却是「分享链接是不是失效了」。
    """
    raw_list = [int(day) for day in (raw or [])]
    valid = [d for d in raw_list if 0 <= d <= 6]
    if len(valid) != len(raw_list):
        logger.warning("星期几包含无效值（有效范围 0-6），已自动过滤：%s", raw_list)
    return valid


def update(subscribe_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    """更新订阅配置。"""
    fields = (
        "name",
        "share_url",
        "password",
        "site_id",
        "target_dir",
        "include_regex",
        "exclude_regex",
        "rename_search",
        "rename_replace",
        "status",
    )
    with session_scope() as session:
        record = session.get(PanSubscribe, subscribe_id)
        if not record:
            return None
        for field in fields:
            if field in payload:
                setattr(record, field, payload[field] or None)
        if "weekdays" in payload:
            record.weekdays = _validated_weekdays(payload.get("weekdays"))
        if "expire_at" in payload:
            # 显式传 null 表示「取消期限」，所以不能用 `or None` 的写法混淆
            # 「没提交这个字段」和「提交了空值」（前者不该改动已有期限）。
            record.expire_at = _as_naive_utc(payload.get("expire_at"))
        if payload.get("reset_invalid"):
            # 用户换了新链接/重填了 Cookie，给它一次重新开始的机会
            record.invalid = False
            record.failure_count = 0
            record.status = SubscribeStatus.ACTIVE.value
        if payload.get("reset_history"):
            record.saved_files = []
        session.flush()
        return _to_dict(record)


def delete(subscribe_id: int) -> bool:
    with session_scope() as session:
        record = session.get(PanSubscribe, subscribe_id)
        if not record:
            return False
        session.delete(record)
        return True


def list_all() -> list[dict[str, Any]]:
    with session_scope() as session:
        return [
            _to_dict(row)
            for row in session.execute(
                select(PanSubscribe).order_by(PanSubscribe.created_at.desc())
            ).scalars()
        ]


def _to_dict(record: PanSubscribe) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "share_url": record.share_url,
        "password": record.password,
        "site_id": record.site_id,
        "target_dir": record.target_dir,
        "include_regex": record.include_regex,
        "exclude_regex": record.exclude_regex,
        "rename_search": record.rename_search,
        "rename_replace": record.rename_replace,
        "status": record.status,
        "invalid": record.invalid,
        "failure_count": record.failure_count,
        "total_saved": record.total_saved,
        "saved_count": len(record.saved_files or []),
        "last_message": record.last_message,
        "last_checked_at": record.last_checked_at.isoformat() if record.last_checked_at else None,
        "weekdays": list(record.weekdays or []),
        "expire_at": record.expire_at.isoformat() if record.expire_at else None,
    }
