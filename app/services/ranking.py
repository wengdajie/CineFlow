"""榜单自动订阅：把「热门榜/高分榜」直接变成订阅，实现真正的自动追新。

为什么需要它：
用户真正的诉求往往不是"我知道要看哪部所以去订阅"，而是"最近有什么好剧我别错过"。
MoviePilot 的「订阅日历/榜单订阅」、autobangumi 的「番剧表自动追」都在解决这件事。

两条铁律（否则会失控）：
1. **单次有上限**（``CF_RANKING_MAX_PER_RUN``，默认 5）：不加限制的话一次巡检
   就会刷进上百个订阅，把下载器和媒体库直接淹掉；
2. **记住处理过的 ID**（``handled_ids``）：用户主动删掉的订阅**不能**被下一轮
   自动加回来——那会变成"删不掉的订阅"，是最让人恼火的自动化。

数据来源：
- ``tmdb_trending`` / ``tmdb_popular`` / ``tmdb_top_rated``：TMDB 官方榜（需 API Key）
- ``local_trending``：本地资源热度榜（无需外网，走 ``trending.resource_ranking``）
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.logger import get_logger
from app.db.base import utcnow
from app.db.models import RankingRule, Subscribe
from app.db.session import session_scope
from app.schemas.enums import EventType, MediaType, NotifyLevel
from app.utils.strings import match_keywords

logger = get_logger(__name__)

#: 可选的榜单来源 → 界面显示名
SOURCES: dict[str, str] = {
    "tmdb_trending": "TMDB 本周趋势",
    "tmdb_popular": "TMDB 热门",
    "tmdb_top_rated": "TMDB 高分",
    "local_trending": "本地资源热度榜（无需 TMDB）",
}


def _to_dict(record: RankingRule) -> dict[str, Any]:
    return {
        "id": record.id,
        "name": record.name,
        "source": record.source,
        "source_label": SOURCES.get(record.source, record.source),
        "media_type": record.media_type,
        "limit": record.limit,
        "min_vote": record.min_vote,
        "min_year": record.min_year,
        "include": record.include,
        "exclude": record.exclude,
        "subscribe_defaults": record.subscribe_defaults or {},
        "enabled": bool(record.enabled),
        "handled_count": len(record.handled_ids or []),
        "created_count": record.created_count,
        "last_run_at": record.last_run_at.isoformat() if record.last_run_at else None,
        "last_result": record.last_result,
    }


def list_rules() -> list[dict[str, Any]]:
    with session_scope() as session:
        rows = session.execute(select(RankingRule).order_by(RankingRule.id.asc())).scalars()
        return [_to_dict(item) for item in rows]


def get_rule(rule_id: int) -> dict[str, Any] | None:
    with session_scope() as session:
        record = session.get(RankingRule, rule_id)
        return _to_dict(record) if record else None


def create(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("规则名称不能为空")
    source = str(payload.get("source") or "tmdb_trending")
    if source not in SOURCES:
        raise ValueError(f"未知榜单来源：{source}（可选 {'/'.join(SOURCES)}）")

    with session_scope() as session:
        exists = session.execute(
            select(RankingRule).where(RankingRule.name == name)
        ).scalar_one_or_none()
        if exists:
            raise ValueError(f"榜单规则已存在：{name}")
        record = RankingRule(
            name=name,
            source=source,
            media_type=str(payload.get("media_type") or MediaType.TV.value),
            limit=max(1, min(int(payload.get("limit") or 10), 100)),
            min_vote=float(payload.get("min_vote") or 0),
            min_year=int(payload["min_year"]) if payload.get("min_year") else None,
            include=payload.get("include"),
            exclude=payload.get("exclude"),
            subscribe_defaults=payload.get("subscribe_defaults") or {},
            enabled=bool(payload.get("enabled", True)),
            handled_ids=[],
        )
        session.add(record)
        session.flush()
        data = _to_dict(record)
    logger.info("新增榜单订阅规则：%s（%s）", name, SOURCES[source])
    return data


def update(rule_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    with session_scope() as session:
        record = session.get(RankingRule, rule_id)
        if not record:
            return None
        if "name" in payload:
            name = str(payload["name"] or "").strip()
            if not name:
                raise ValueError("规则名称不能为空")
            record.name = name
        if payload.get("source"):
            if payload["source"] not in SOURCES:
                raise ValueError(f"未知榜单来源：{payload['source']}")
            record.source = payload["source"]
        for key in ("media_type", "include", "exclude"):
            if key in payload:
                setattr(record, key, payload[key])
        if payload.get("limit"):
            record.limit = max(1, min(int(payload["limit"]), 100))
        if "min_vote" in payload and payload["min_vote"] is not None:
            record.min_vote = float(payload["min_vote"])
        if "min_year" in payload:
            record.min_year = int(payload["min_year"]) if payload["min_year"] else None
        if payload.get("subscribe_defaults") is not None:
            record.subscribe_defaults = payload["subscribe_defaults"]
        if "enabled" in payload:
            record.enabled = bool(payload["enabled"])
        if payload.get("reset_handled"):
            # 清空"已处理"记录：用户明确想让这条规则重新扫一遍全榜
            record.handled_ids = []
        session.flush()
        return _to_dict(record)


def delete(rule_id: int) -> bool:
    with session_scope() as session:
        record = session.get(RankingRule, rule_id)
        if not record:
            return False
        session.delete(record)
    logger.info("已删除榜单订阅规则 #%s", rule_id)
    return True


async def fetch_candidates(rule: dict[str, Any]) -> list[dict[str, Any]]:
    """按规则取榜单候选（尚未过滤）。"""
    source = rule.get("source") or "tmdb_trending"
    media_type = rule.get("media_type") or MediaType.TV.value
    limit = int(rule.get("limit") or 10)

    if source == "local_trending":
        from app.services import trending as trending_service

        ranking = trending_service.resource_ranking(limit=limit, media_type=media_type)
        return [
            {
                "title": item.get("title"),
                "year": None,
                "media_type": item.get("media_type") or media_type,
                "tmdb_id": None,
                "vote_average": 0.0,
                "overview": f"本地热度 {item.get('heat')}（{item.get('site_count')} 个站点）",
                "heat": item.get("heat"),
            }
            for item in ranking.get("items", [])
        ]

    from app.providers.metadata.tmdb import tmdb

    if not tmdb.available:
        return []
    return await tmdb.ranking(source, media_type=media_type, limit=limit)


def filter_candidates(
    rule: dict[str, Any], candidates: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按评分/年份/关键词过滤，返回 ``(通过, 被拒(带原因))``。纯函数，便于单测。"""
    min_vote = float(rule.get("min_vote") or 0)
    min_year = rule.get("min_year")
    include = rule.get("include") or ""
    exclude = rule.get("exclude") or ""

    passed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in candidates:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        reason = ""
        if min_vote and float(item.get("vote_average") or 0) < min_vote:
            reason = f"评分 {item.get('vote_average')} < {min_vote}"
        elif min_year and int(item.get("year") or 0) < int(min_year):
            reason = f"年份 {item.get('year') or '未知'} < {min_year}"
        elif include and not match_keywords(title, include, mode="any"):
            reason = f"标题未包含 {include}"
        elif exclude and match_keywords(title, exclude, mode="any"):
            reason = f"标题命中排除词 {exclude}"
        if reason:
            rejected.append({**item, "reason": reason})
        else:
            passed.append(item)
    return passed, rejected


def _identity(item: dict[str, Any]) -> Any:
    """候选的去重标识：有 tmdb_id 用它，否则用标题（本地榜没有 ID）。"""
    return item.get("tmdb_id") or f"title:{item.get('title')}"


def _already_subscribed(session: Any, item: dict[str, Any], media_type: str) -> bool:
    stmt = select(Subscribe.id)
    if item.get("tmdb_id"):
        stmt = stmt.where(Subscribe.tmdb_id == int(item["tmdb_id"]))
    else:
        stmt = stmt.where(
            Subscribe.title == item.get("title"),
            Subscribe.media_type == (item.get("media_type") or media_type),
        )
    return session.execute(stmt.limit(1)).scalar_one_or_none() is not None


async def run_rule(rule_id: int, *, dry_run: bool = False) -> dict[str, Any]:
    """执行单条榜单规则。

    ``dry_run=True`` 时只返回"会订阅哪些"，不真的建订阅——
    自动化功能必须让用户能先看清结果再放手。
    """
    rule = get_rule(rule_id)
    if rule is None:
        return {"success": False, "message": "榜单规则不存在"}
    if not rule["enabled"] and not dry_run:
        return {"success": True, "message": "规则已禁用", "created": 0, "items": []}

    candidates = await fetch_candidates(rule)
    if not candidates:
        from app.providers.metadata.tmdb import tmdb

        message = (
            "未配置 TMDB_API_KEY，TMDB 榜单不可用（可改用「本地资源热度榜」来源）"
            if rule["source"].startswith("tmdb") and not tmdb.available
            else "榜单暂无数据"
        )
        _finish(rule_id, 0, message, [])
        return {"success": True, "message": message, "created": 0, "items": []}

    passed, rejected = filter_candidates(rule, candidates)

    with session_scope() as session:
        record = session.get(RankingRule, rule_id)
        handled = set(record.handled_ids or []) if record else set()

    from app.core.config import settings

    cap = max(1, int(settings.RANKING_MAX_PER_RUN))
    media_type = rule["media_type"]

    picked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with session_scope() as session:
        for item in passed:
            key = _identity(item)
            if key in handled:
                skipped.append({"title": item.get("title"), "reason": "此前已处理过"})
                continue
            if _already_subscribed(session, item, media_type):
                skipped.append({"title": item.get("title"), "reason": "已有订阅"})
                handled.add(key)
                continue
            picked.append(item)
            if len(picked) >= cap:
                break

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "message": f"将新增 {len(picked)} 个订阅（上限 {cap}）",
            "created": 0,
            "items": [
                {"title": item.get("title"), "year": item.get("year"),
                 "vote_average": item.get("vote_average")}
                for item in picked
            ],
            "skipped": skipped,
            "rejected": [
                {"title": item.get("title"), "reason": item.get("reason")}
                for item in rejected
            ],
        }

    from app.services import notify as notify_service
    from app.services import subscribe as subscribe_service

    created: list[dict[str, Any]] = []
    errors: list[str] = []
    defaults = rule.get("subscribe_defaults") or {}
    for item in picked:
        payload = {
            **defaults,
            "title": item.get("title"),
            "year": item.get("year"),
            "media_type": item.get("media_type") or media_type,
            "tmdb_id": item.get("tmdb_id"),
        }
        try:
            subscribe = await subscribe_service.create_subscribe(payload)
        except Exception as exc:  # 单条失败不能影响整轮
            errors.append(f"{item.get('title')}: {exc}")
            # 失败也记进 handled：多半是"已存在/识别不了"，反复重试只会刷日志
            handled.add(_identity(item))
            continue
        handled.add(_identity(item))
        created.append({"id": subscribe.id, "title": subscribe.title, "season": subscribe.season})

    message = f"新增 {len(created)} 个订阅" + (f"，{len(errors)} 个失败" if errors else "")
    _finish(rule_id, len(created), message, sorted(handled, key=str))

    if created:
        await notify_service.send(
            f"榜单自动订阅：{rule['name']}",
            f"来自「{rule['source_label']}」，新增 {len(created)} 个订阅\n"
            + "\n".join(item["title"] for item in created[:5]),
            level=NotifyLevel.SUCCESS.value,
            event=EventType.RANKING_SUBSCRIBED.value,
            payload={"rule_id": rule_id, "count": len(created)},
        )
    logger.info("榜单规则「%s」执行完成：%s", rule["name"], message)
    return {
        "success": True,
        "message": message,
        "created": len(created),
        "items": created,
        "skipped": skipped,
        "errors": errors,
    }


def _finish(rule_id: int, created: int, message: str, handled: list[Any]) -> None:
    """回写执行结果（handled_ids / created_count / last_run_at）。"""
    with session_scope() as session:
        record = session.get(RankingRule, rule_id)
        if not record:
            return
        if handled:
            # handled_ids 只存整数 tmdb_id 与 "title:xxx" 两种形态，
            # JSON 列都能容纳；截断到 500 条避免无限增长
            record.handled_ids = handled[-500:]
        record.created_count = (record.created_count or 0) + created
        record.last_run_at = utcnow()
        record.last_result = message[:500]


async def run() -> dict[str, Any]:
    """调度入口：跑所有启用的榜单规则。"""
    with session_scope() as session:
        ids = list(
            session.execute(
                select(RankingRule.id).where(RankingRule.enabled.is_(True))
            ).scalars()
        )
    if not ids:
        return {"success": True, "message": "没有启用的榜单规则", "rules": 0, "created": 0}

    total = 0
    results = []
    for rule_id in ids:
        result = await run_rule(rule_id)
        total += int(result.get("created") or 0)
        results.append({"rule_id": rule_id, **{k: result.get(k) for k in ("message", "created")}})
    logger.info("榜单订阅巡检完成：%d 条规则，新增 %d 个订阅", len(ids), total)
    return {"success": True, "rules": len(ids), "created": total, "results": results}
