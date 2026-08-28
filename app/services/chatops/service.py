"""ChatOps 执行引擎：把聊天指令变成真实的搜索/下载/订阅动作。

处理流程：

    入站 Webhook
      → 适配器验签（失败 401）
      → 幂等去重（同一 message_id 只处理一次）
      → 用户白名单校验
      → 指令解析（commands.parse）
      → 执行并生成回复文本
      → 写审计日志 + 通过适配器回复

会话上下文：``搜索`` 的结果会按会话缓存，用户接着发 ``下载 2`` 就能选中
第 2 条，无需重复贴链接。上下文默认保留 15 分钟（``CF_CHATOPS_SESSION_TTL``）。
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.core.logger import get_logger
from app.db.models import AuditLog, DownloadTask, Subscribe
from app.db.session import session_scope
from app.schemas.enums import EventType, ResourceKind, SubscribeStatus, TaskStatus
from app.services import settings_store
from app.services.chatops import commands as command_parser
from app.services.chatops.adapters import ChatAdapter, InboundMessage, get_adapter
from app.utils.strings import format_size, truncate

logger = get_logger(__name__)

#: ChatOps 配置在 settings 表中的存储键
KEY_CHATOPS = "chatops"

#: 会话上下文：dedupe/会话键 -> {"items": [...], "at": ts}
_SESSIONS: dict[str, dict[str, Any]] = {}
#: 已处理过的消息幂等键 -> 处理时间
_PROCESSED: dict[str, float] = {}
#: 幂等键保留时长（秒）
_DEDUPE_TTL = 600


# ---------------- 配置 ----------------
def default_config() -> dict[str, Any]:
    """ChatOps 默认配置（静态配置提供默认值）。"""
    return {
        "enabled": bool(settings.CHATOPS_ENABLED),
        "auto_download": bool(settings.CHATOPS_AUTO_DOWNLOAD),
        "result_limit": int(settings.CHATOPS_RESULT_LIMIT),
        "allow_users": list(settings.CHATOPS_ALLOW_USERS),
        "platforms": {},
    }


def get_config() -> dict[str, Any]:
    """当前生效的 ChatOps 配置（默认值 + 运行期覆盖）。"""
    config = default_config()
    stored = settings_store.get_setting(KEY_CHATOPS, {}) or {}
    if isinstance(stored, dict):
        for key, value in stored.items():
            if value is not None:
                config[key] = value
    return config


def save_config(payload: dict[str, Any]) -> dict[str, Any]:
    """更新 ChatOps 配置（只覆盖提交的字段）。"""
    stored = settings_store.get_setting(KEY_CHATOPS, {}) or {}
    if not isinstance(stored, dict):
        stored = {}
    for key in ("enabled", "auto_download", "result_limit", "allow_users", "platforms"):
        if key in payload and payload[key] is not None:
            stored[key] = payload[key]
    settings_store.set_setting(KEY_CHATOPS, stored)
    return get_config()


def platform_config(platform: str) -> dict[str, Any]:
    """某个平台的配置（app_id / secret / token 等）。"""
    platforms = get_config().get("platforms") or {}
    config = platforms.get(platform) or {}
    return config if isinstance(config, dict) else {}


def build_adapter(platform: str) -> ChatAdapter | None:
    """构建某个平台的适配器（带其配置）。"""
    return get_adapter(platform, platform_config(platform))


# ---------------- 幂等与会话 ----------------
def _seen(key: str) -> bool:
    """幂等判断：已处理过返回 True，同时清理过期记录。"""
    now = time.time()
    for old in [k for k, ts in _PROCESSED.items() if now - ts > _DEDUPE_TTL]:
        _PROCESSED.pop(old, None)
    if key in _PROCESSED:
        return True
    _PROCESSED[key] = now
    return False


def _session_key(message: InboundMessage) -> str:
    return f"{message.platform}:{message.chat_id or message.user_id}"


def _remember(message: InboundMessage, items: list[dict[str, Any]]) -> None:
    """记住搜索结果，供后续「下载 N」引用。"""
    _SESSIONS[_session_key(message)] = {"items": items, "at": time.time()}


def _recall(message: InboundMessage) -> list[dict[str, Any]]:
    """取回上一次搜索结果（过期则返回空）。"""
    entry = _SESSIONS.get(_session_key(message))
    if not entry:
        return []
    if time.time() - entry["at"] > max(int(settings.CHATOPS_SESSION_TTL), 60):
        _SESSIONS.pop(_session_key(message), None)
        return []
    return entry.get("items") or []


def clear_sessions() -> None:
    """清空会话与幂等缓存（测试与重载用）。"""
    _SESSIONS.clear()
    _PROCESSED.clear()


# ---------------- 审计 ----------------
def _audit(
    message: InboundMessage,
    command: command_parser.Command,
    *,
    success: bool,
    reply: str,
) -> None:
    """记录一条指令审计。"""
    try:
        with session_scope() as session:
            session.add(
                AuditLog(
                    source=f"chatops.{message.platform}",
                    actor=message.user_name or message.user_id or "unknown",
                    actor_id=message.user_id or None,
                    action=command.name or "unknown",
                    target=truncate(command.argument or str(command.index or ""), 200),
                    command=truncate(message.text, 500),
                    success=success,
                    result=truncate(reply, 1000),
                )
            )
    except Exception as exc:  # pragma: no cover - 审计失败不应阻断指令
        logger.warning("写入审计日志失败: %s", exc)


def list_audit(*, limit: int = 100, source: str | None = None) -> list[dict[str, Any]]:
    """查询审计日志。"""
    with session_scope() as session:
        stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        if source:
            stmt = select(AuditLog).where(AuditLog.source.like(f"{source}%")).order_by(
                AuditLog.created_at.desc()
            ).limit(limit)
        return [
            {
                "id": row.id,
                "source": row.source,
                "actor": row.actor,
                "actor_id": row.actor_id,
                "action": row.action,
                "target": row.target,
                "command": row.command,
                "success": row.success,
                "result": row.result,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in session.execute(stmt).scalars()
        ]


# ---------------- 指令实现 ----------------
async def _do_search(
    message: InboundMessage,
    command: command_parser.Command,
    limit: int,
    *,
    auto_download: bool = False,
) -> str:
    from app.services import search as search_service

    keyword = command.argument.strip()
    if not keyword:
        return "请告诉我要搜什么，例如：搜索 庆余年 第二季"

    results = await search_service.search(
        keyword,
        media_type=None,
        season=command.season,
        episode=command.episode,
    )
    if not results:
        return f"没搜到「{keyword}」。可能是站点未启用，或换个片名再试。"

    top = results[:limit]
    _remember(message, top)

    # 开了自动下载就直接投最优的一条（search 已按评分排序），
    # 省掉「再回一句下载 1」的往返
    if auto_download:
        from app.services import download as download_service

        best = top[0]
        task = await download_service.add_download(best)
        if task:
            extra = (
                "\n☁️ 网盘资源已自动转存"
                if task.status == TaskStatus.TRANSFERRED.value
                else ""
            )
            return (
                f"🔍 「{keyword}」共 {len(results)} 条，已自动下载最优的一条：\n"
                f"{truncate(str(best.get('title')), 58)}\n"
                f"{format_size(best.get('size'))} · {best.get('site') or ''}\n"
                f"任务 #{task.id} · 状态 {task.status}{extra}"
            )
        # 自动下载失败就退回列表模式，让用户手动选
        logger.warning("ChatOps 自动下载失败，退回列表模式")

    lines = [f"🔍 「{keyword}」找到 {len(results)} 条，前 {len(top)} 条："]
    for index, item in enumerate(top, start=1):
        info = item.get("meta") or {}
        tags = []
        if info.get("resolution"):
            tags.append(str(info["resolution"]))
        if item.get("kind") == ResourceKind.PAN.value:
            tags.append("网盘")
        if item.get("seeders"):
            tags.append(f"{item['seeders']}↑")
        lines.append(
            f"{index}. {truncate(str(item.get('title')), 58)}\n"
            f"   {format_size(item.get('size'))}"
            + (f" · {' · '.join(tags)}" if tags else "")
            + f" · {item.get('site') or ''}"
        )
    lines.append("\n回复「下载 序号」即可下载，例如：下载 1")
    return "\n".join(lines)


async def _do_download(
    message: InboundMessage, command: command_parser.Command
) -> tuple[bool, str]:
    from app.services import download as download_service

    # 情况一：直接给了链接
    argument = (command.argument or "").strip()
    if argument.startswith(("magnet:", "http://", "https://")):
        kind = (
            ResourceKind.MAGNET.value
            if argument.startswith("magnet:")
            else ResourceKind.PAN.value
            if "pan." in argument or "aliyundrive" in argument or "alipan" in argument
            else ResourceKind.DIRECT.value
        )
        task = await download_service.add_download(
            {"title": f"ChatOps 手动下载 {truncate(argument, 40)}", "link": argument, "kind": kind}
        )
        if not task:
            return False, "添加失败，请检查下载器配置"
        return True, f"✅ 已提交下载（任务 #{task.id}，状态 {task.status}）"

    # 情况二：引用上一次搜索结果
    items = _recall(message)
    if not items:
        return False, "没有可用的搜索结果（可能已过期），请先发「搜索 片名」"

    index = command.index or 1
    if index < 1 or index > len(items):
        return False, f"序号超出范围，请选 1~{len(items)}"

    chosen = items[index - 1]
    task = await download_service.add_download(chosen)
    if not task:
        return False, "添加失败，请检查下载器配置"

    title = truncate(str(chosen.get("title")), 50)
    extra = ""
    if task.status == TaskStatus.TRANSFERRED.value:
        extra = "\n☁️ 网盘资源已自动转存"
    elif task.kind == ResourceKind.PAN.value:
        extra = "\n☁️ 网盘资源已登记，可回复「转存」批量转存"
    return True, f"✅ 已提交下载：{title}\n任务 #{task.id} · 状态 {task.status}{extra}"


async def _do_subscribe(command: command_parser.Command) -> tuple[bool, str]:
    from app.services import subscribe as subscribe_service

    title = command.argument.strip()
    if not title:
        return False, "请告诉我要订阅什么，例如：订阅 凡人修仙传 第二季"

    with session_scope() as session:
        exists = session.execute(
            select(Subscribe).where(
                Subscribe.title == title,
                Subscribe.season == (command.season or 1),
            )
        ).scalar_one_or_none()
        if exists:
            return True, f"「{title}」已在追剧列表中（订阅 #{exists.id}）"

    try:
        record = await subscribe_service.create_subscribe(
            {
                "title": title,
                "media_type": "tv",
                "season": command.season or 1,
            }
        )
    except Exception as exc:
        return False, f"创建订阅失败：{exc}"

    return True, (
        f"⭐ 已订阅「{title}」第 {command.season or 1} 季（订阅 #{record.id}）\n"
        "系统会自动追新并下载，有新集会通知你"
    )


def _do_status() -> str:
    with session_scope() as session:
        active = list(
            session.execute(
                select(DownloadTask)
                .where(
                    DownloadTask.status.in_(
                        [TaskStatus.DOWNLOADING.value, TaskStatus.PENDING.value]
                    )
                )
                .order_by(DownloadTask.created_at.desc())
                .limit(10)
            ).scalars()
        )
        done_today = session.execute(
            select(DownloadTask).where(
                DownloadTask.status.in_(
                    [TaskStatus.COMPLETED.value, TaskStatus.TRANSFERRED.value]
                )
            )
        ).scalars()
        done_count = len(list(done_today))

        if not active:
            return f"📊 当前没有进行中的任务\n累计已完成 {done_count} 个"

        lines = [f"📊 进行中 {len(active)} 个任务："]
        for task in active:
            percent = f"{task.progress * 100:.0f}%" if task.progress else "0%"
            lines.append(
                f"· {truncate(task.title, 46)}\n"
                f"  {task.status} {percent}"
                + (f" · {format_size(task.speed)}/s" if task.speed else "")
            )
        lines.append(f"\n累计已完成 {done_count} 个")
        return "\n".join(lines)


def _do_subscribes() -> str:
    with session_scope() as session:
        rows = list(
            session.execute(
                select(Subscribe)
                .where(Subscribe.status == SubscribeStatus.ACTIVE.value)
                .order_by(Subscribe.created_at.desc())
                .limit(15)
            ).scalars()
        )
    if not rows:
        return "📋 还没有追剧中的订阅，回复「订阅 片名」添加一个"

    lines = [f"📋 追剧中 {len(rows)} 部："]
    for item in rows:
        got = len(item.downloaded_episodes or [])
        total = item.total_episodes or 0
        progress = f"{got}/{total}" if total else f"{got} 集"
        lack = f" · 缺 {item.lack_episodes} 集" if item.lack_episodes else ""
        lines.append(f"· {truncate(item.title, 40)} S{item.season:02d} · {progress}{lack}")
    return "\n".join(lines)


async def _do_transfer() -> str:
    from app.services import pan_storage as pan_service

    stats = await pan_service.transfer_pending(
        limit=int(settings.PAN_TRANSFER_BATCH), notify=False
    )
    if not stats.get("pending"):
        return "☁️ 没有待转存的网盘资源"
    if stats.get("message"):
        return f"☁️ 有 {stats['pending']} 个待转存，但{stats['message']}"
    return (
        f"☁️ 转存完成：成功 {stats['saved']} 个"
        + (f"，失败 {stats['failed']} 个" if stats.get("failed") else "")
    )


def _do_trending() -> str:
    from app.services import trending as trending_service

    data = trending_service.resource_ranking(limit=8, days=14)
    items = data.get("items") or []
    if not items:
        return "🔥 还没有足够的搜索数据来生成热榜，先搜几部片吧"

    lines = ["🔥 资源热度榜 TOP" + str(len(items)) + "："]
    for item in items:
        season = f" S{item['season']:02d}" if item.get("season") else ""
        lines.append(
            f"{item['rank']}. {truncate(str(item.get('title')), 40)}{season}"
            f" · 热度 {item.get('heat_percent')}%"
            f" · {item.get('site_count')} 站"
        )
    lines.append("\n回复「搜索 片名」查看可下载资源")
    return "\n".join(lines)


# ---------------- 主入口 ----------------
async def handle_message(message: InboundMessage) -> dict[str, Any]:
    """执行一条入站消息，返回处理结果（含回复文本）。"""
    config = get_config()

    if not config.get("enabled", True):
        return {"handled": False, "reply": "", "reason": "ChatOps 已停用"}

    if not message.actionable:
        return {"handled": False, "reply": "", "reason": "非指令消息"}

    # 幂等：平台重试不重复执行
    if _seen(ChatAdapter.dedupe_key(message)):
        return {"handled": False, "reply": "", "reason": "重复消息，已忽略"}

    # 白名单
    allow = [str(item) for item in (config.get("allow_users") or []) if str(item).strip()]
    if allow and message.user_id and str(message.user_id) not in allow:
        reply = "⛔ 你不在允许使用的白名单中"
        _audit(message, command_parser.Command(name="denied"), success=False, reply=reply)
        return {"handled": True, "reply": reply, "reason": "用户不在白名单"}

    command = command_parser.parse(message.text)
    limit = max(int(config.get("result_limit") or 5), 1)
    success = True

    if not command.ok or command.name == "help":
        reply = command_parser.HELP_TEXT
    elif command.name == "search":
        reply = await _do_search(
            message, command, limit, auto_download=bool(config.get("auto_download"))
        )
    elif command.name == "download":
        success, reply = await _do_download(message, command)
    elif command.name == "subscribe":
        success, reply = await _do_subscribe(command)
    elif command.name == "status":
        reply = _do_status()
    elif command.name == "subscribes":
        reply = _do_subscribes()
    elif command.name == "transfer":
        reply = await _do_transfer()
    elif command.name == "trending":
        reply = _do_trending()
    else:
        reply = command_parser.HELP_TEXT
        success = False

    _audit(message, command, success=success, reply=reply)

    # 广播事件，插件可监听
    from app.services import notify as notify_service

    await notify_service.emit(
        EventType.CHAT_COMMAND.value,
        {
            "platform": message.platform,
            "user_id": message.user_id,
            "command": command.name,
            "argument": command.argument,
            "success": success,
        },
    )

    logger.info(
        "ChatOps[%s] %s <- %s: %s",
        message.platform,
        command.name or "unknown",
        message.user_name or message.user_id,
        truncate(message.text, 60),
    )
    return {"handled": True, "reply": reply, "command": command.name, "success": success}


async def process_webhook(
    platform: str,
    *,
    headers: dict[str, str],
    body: bytes,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """入站 Webhook 总处理：验签 → 解析 → 执行 → 回复。

    返回 dict 含 ``status``（http 状态码建议）与 ``response``（回给平台的响应体）。
    """
    adapter = build_adapter(platform)
    if not adapter:
        return {"status": 404, "response": {"success": False, "message": f"不支持的平台: {platform}"}}

    ok, reason = adapter.verify(headers=headers, body=body, payload=payload)
    if not ok:
        logger.warning("ChatOps[%s] 验签失败: %s", platform, reason)
        return {"status": 401, "response": {"success": False, "message": f"验签失败: {reason}"}}

    message = adapter.parse(payload)

    # 平台的 URL 验证挑战：原样回 challenge
    if message.challenge is not None:
        return {"status": 200, "response": message.challenge}

    result = await handle_message(message)
    reply = str(result.get("reply") or "")

    replied = False
    if reply:
        try:
            replied = await adapter.reply(message, reply)
        except Exception as exc:  # pragma: no cover
            logger.warning("ChatOps[%s] 回复失败: %s", platform, exc)

    response: dict[str, Any] = {"success": True, "handled": bool(result.get("handled"))}
    # 钉钉支持在回调响应体里直接带上回复内容
    if reply and not replied:
        response["msgtype"] = "text"
        response["text"] = {"content": reply}
    return {"status": 200, "response": response, "reply": reply, "replied": replied}
