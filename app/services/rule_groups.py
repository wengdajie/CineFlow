"""过滤规则组服务：规则组的 CRUD、默认组解析与试算。

规则组本体逻辑在 ``app/core/rules.py``（纯函数、无 IO）；
本模块只负责数据库读写与「拿到一个可用的规则组」这件事。

**默认组**：``is_default=True`` 的那一条。同一时刻只允许一条，
写入时会把其它组的标记清掉——否则"默认"就没有意义了。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.logger import get_logger
from app.core.rules import RuleGroup, describe
from app.db.models import FilterRuleGroup
from app.db.session import session_scope

logger = get_logger(__name__)


def _to_dict(record: FilterRuleGroup) -> dict[str, Any]:
    group = RuleGroup.from_record(record)
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "levels": record.levels or [],
        "level_count": len(record.levels or []),
        "accept_unmatched": bool(record.accept_unmatched),
        "enabled": bool(record.enabled),
        "is_default": bool(record.is_default),
        "summary": describe(group),
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


def _normalize_levels(raw: Any) -> list[dict[str, Any]]:
    """把前端提交的层级列表规范化：丢掉非法项，字段统一为字符串/数字。"""
    from app.core.rules import RuleLevel

    if not isinstance(raw, list):
        return []
    return [
        RuleLevel.from_dict(item).to_dict() for item in raw if isinstance(item, dict)
    ]


def list_groups(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    with session_scope() as session:
        stmt = select(FilterRuleGroup).order_by(
            FilterRuleGroup.is_default.desc(), FilterRuleGroup.id.asc()
        )
        if enabled_only:
            stmt = stmt.where(FilterRuleGroup.enabled.is_(True))
        return [_to_dict(item) for item in session.execute(stmt).scalars()]


def get_group(group_id: int) -> dict[str, Any] | None:
    with session_scope() as session:
        record = session.get(FilterRuleGroup, group_id)
        return _to_dict(record) if record else None


def _clear_other_defaults(session: Any, keep_id: int | None) -> None:
    for row in session.execute(
        select(FilterRuleGroup).where(FilterRuleGroup.is_default.is_(True))
    ).scalars():
        if keep_id is None or row.id != keep_id:
            row.is_default = False


def create(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("规则组名称不能为空")
    levels = _normalize_levels(payload.get("levels"))
    if not levels:
        raise ValueError("至少需要一个层级，否则规则组没有任何作用")

    with session_scope() as session:
        exists = session.execute(
            select(FilterRuleGroup).where(FilterRuleGroup.name == name)
        ).scalar_one_or_none()
        if exists:
            raise ValueError(f"规则组已存在：{name}")
        record = FilterRuleGroup(
            name=name,
            description=payload.get("description"),
            levels=levels,
            accept_unmatched=bool(payload.get("accept_unmatched", True)),
            enabled=bool(payload.get("enabled", True)),
            is_default=bool(payload.get("is_default", False)),
        )
        session.add(record)
        session.flush()
        if record.is_default:
            _clear_other_defaults(session, record.id)
        session.flush()
        data = _to_dict(record)
    logger.info("新增过滤规则组：%s（%d 层）", name, len(levels))
    return data


def update(group_id: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    with session_scope() as session:
        record = session.get(FilterRuleGroup, group_id)
        if not record:
            return None
        if "name" in payload:
            name = str(payload["name"] or "").strip()
            if not name:
                raise ValueError("规则组名称不能为空")
            clash = session.execute(
                select(FilterRuleGroup).where(
                    FilterRuleGroup.name == name, FilterRuleGroup.id != group_id
                )
            ).scalar_one_or_none()
            if clash:
                raise ValueError(f"规则组已存在：{name}")
            record.name = name
        if "description" in payload:
            record.description = payload["description"]
        if "levels" in payload:
            levels = _normalize_levels(payload["levels"])
            if not levels:
                raise ValueError("至少需要一个层级")
            record.levels = levels
        if "accept_unmatched" in payload:
            record.accept_unmatched = bool(payload["accept_unmatched"])
        if "enabled" in payload:
            record.enabled = bool(payload["enabled"])
        if payload.get("is_default"):
            record.is_default = True
            _clear_other_defaults(session, group_id)
        elif "is_default" in payload and not payload["is_default"]:
            record.is_default = False
        session.flush()
        return _to_dict(record)


def delete(group_id: int) -> bool:
    """删除规则组，并把引用它的订阅解绑。

    不做"禁止删除被引用的组"：用户想删就应该能删，
    残留 ``rule_group_id`` 指向不存在的组会让订阅静默失去过滤能力，比报错更糟。
    """
    from app.db.models import Subscribe

    with session_scope() as session:
        record = session.get(FilterRuleGroup, group_id)
        if not record:
            return False
        for sub in session.execute(
            select(Subscribe).where(Subscribe.rule_group_id == group_id)
        ).scalars():
            sub.rule_group_id = None
        session.delete(record)
    logger.info("已删除过滤规则组 #%s", group_id)
    return True


def default_group() -> RuleGroup | None:
    """当前默认规则组（没有默认组时返回 ``None``，即不改变既有行为）。"""
    with session_scope() as session:
        record = session.execute(
            select(FilterRuleGroup).where(
                FilterRuleGroup.is_default.is_(True),
                FilterRuleGroup.enabled.is_(True),
            )
        ).scalar_one_or_none()
        return RuleGroup.from_record(record) if record else None


def load_group(group_id: int | None) -> RuleGroup | None:
    """按 ID 取规则组；``group_id`` 为空或不存在时退回默认组。

    这是订阅/搜索侧唯一需要调用的入口——把"用哪个组"的决策收在一处，
    避免每个调用点各写一遍 fallback 逻辑。
    """
    if group_id:
        with session_scope() as session:
            record = session.get(FilterRuleGroup, int(group_id))
            if record and record.enabled:
                return RuleGroup.from_record(record)
    return default_group()


def preview(group_id: int, resources: list[dict[str, Any]]) -> dict[str, Any]:
    """试算：给一批资源标注命中层级，让用户先看效果再保存。"""
    from app.core import filters, rules

    group = load_group(group_id)
    if group is None:
        return {"success": False, "message": "规则组不存在", "items": []}

    scored = [dict(item) for item in resources]
    for item in scored:
        filters.score_resource(item)
    annotated = rules.annotate(group, scored, filters.resource_meta)
    return {
        "success": True,
        "group": group.name,
        "summary": rules.describe(group),
        "total": len(annotated),
        "dropped": len(scored) - len(annotated),
        "items": [
            {
                "title": item.get("title"),
                "score": item.get("score"),
                "rule_level": item.get("rule_level"),
                "rule_level_name": item.get("rule_level_name"),
            }
            for item in annotated
        ],
    }
