"""自定义过滤规则组：把「有序偏好」表达成可执行的分层规则。

为什么需要它（全局评分不够用）：
全局评分只能表达"4K 比 1080p 好"这种**单调偏好**，无法表达
"宁可要 1080p 中字，也不要没字幕的 4K"这类**分层择优**——
后者是中文用户的真实需求，也是 MoviePilot / nexus-media 里
「规则组 / 优先级规则」解决的问题。

设计要点：
- 本模块是 ``core``：**纯函数、无 IO**，只吃 dict 与已解析的 ``MetaInfo``，
  因此可以完全离线单测（ADR-10 / ADR-11 的同一套思路）。
- 一个规则组是**有序**的层级列表，命中靠前层的资源整体优于靠后层，
  层内再用既有的 ``score_resource`` 排序 → 既有偏好层级又保留细粒度打分。
- 层级条件全部**可留空**，留空即"不限制"，避免用户被迫填满一堆字段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.meta import MetaInfo
from app.utils.strings import match_keywords, parse_size

#: 未命中任何层级时使用的层号。取一个足够大的数，
#: 保证「兜底接受的资源」永远排在任何命中层之后。
UNMATCHED_LEVEL = 9999


@dataclass(frozen=True)
class RuleLevel:
    """规则组里的一层（一组"与"条件）。"""

    name: str = ""
    #: 允许的分辨率，多个用 ``|`` 或 ``,`` 分隔；空 = 不限
    resolution: str = ""
    #: 允许的质量（REMUX/BluRay/WEB-DL…）
    quality: str = ""
    #: 允许的特效（HDR/Dolby Vision…）
    effect: str = ""
    #: 允许的编码
    video_codec: str = ""
    #: 必须包含的关键词（任一命中即可，便于写 ``中字|简繁``）
    include: str = ""
    #: 命中即排除
    exclude: str = ""
    min_seeders: int = 0
    min_size_gb: float = 0.0
    max_size_gb: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleLevel:
        """从数据库/前端的 dict 构建，未知字段忽略、类型宽松。"""

        def _num(key: str, default: float = 0.0) -> float:
            raw = data.get(key)
            if raw in (None, ""):
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        return cls(
            name=str(data.get("name") or "").strip(),
            resolution=str(data.get("resolution") or "").strip(),
            quality=str(data.get("quality") or "").strip(),
            effect=str(data.get("effect") or "").strip(),
            video_codec=str(data.get("video_codec") or "").strip(),
            include=str(data.get("include") or "").strip(),
            exclude=str(data.get("exclude") or "").strip(),
            min_seeders=int(_num("min_seeders")),
            min_size_gb=_num("min_size_gb"),
            max_size_gb=_num("max_size_gb"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "resolution": self.resolution,
            "quality": self.quality,
            "effect": self.effect,
            "video_codec": self.video_codec,
            "include": self.include,
            "exclude": self.exclude,
            "min_seeders": self.min_seeders,
            "min_size_gb": self.min_size_gb,
            "max_size_gb": self.max_size_gb,
        }

    @property
    def label(self) -> str:
        """给界面显示的名字（用户没起名时用条件拼一个）。"""
        if self.name:
            return self.name
        parts = [self.resolution, self.quality, self.effect, self.include]
        return " ".join(part for part in parts if part) or "不限"


@dataclass
class RuleGroup:
    """有序规则组。"""

    name: str = ""
    levels: list[RuleLevel] = field(default_factory=list)
    #: 任何层都不命中时是否仍然接受该资源
    accept_unmatched: bool = True

    @classmethod
    def from_record(cls, record: Any) -> RuleGroup:
        """从 ORM 记录（或同结构 dict）构建。"""
        get = record.get if isinstance(record, dict) else lambda k, d=None: getattr(record, k, d)
        raw_levels = get("levels", []) or []
        return cls(
            name=str(get("name", "") or ""),
            levels=[RuleLevel.from_dict(item) for item in raw_levels if isinstance(item, dict)],
            accept_unmatched=bool(get("accept_unmatched", True)),
        )

    @property
    def is_empty(self) -> bool:
        return not self.levels


def _matches_any(value: str | None, expression: str) -> bool:
    """``value`` 是否命中 ``expression`` 里任一项（``|`` 或 ``,`` 分隔）。

    留空表示不限制，直接通过——这是"字段可留空"语义的关键。
    """
    if not expression.strip():
        return True
    if not value:
        return False
    wanted = [
        item.strip().lower()
        for item in expression.replace("|", ",").replace("、", ",").split(",")
        if item.strip()
    ]
    return str(value).strip().lower() in wanted


def level_matches(
    level: RuleLevel, resource: dict[str, Any], info: MetaInfo
) -> bool:
    """判断资源是否落在这一层。"""
    if not _matches_any(info.resolution, level.resolution):
        return False
    if not _matches_any(info.quality, level.quality):
        return False
    if not _matches_any(info.effect, level.effect):
        return False
    if not _matches_any(info.video_codec, level.video_codec):
        return False

    text = " ".join(
        str(resource.get(key) or "") for key in ("title", "description", "release_group")
    )
    if level.include and not match_keywords(text, level.include.replace("|", ","), mode="any"):
        return False
    if level.exclude and match_keywords(text, level.exclude.replace("|", ","), mode="any"):
        return False

    if level.min_seeders and int(resource.get("seeders") or 0) < level.min_seeders:
        return False

    size_gb = parse_size(resource.get("size")) / 1024**3
    # 体积为 0 通常代表站点没给大小，不能据此否掉资源（否则会把网盘资源全滤掉）
    if level.min_size_gb and size_gb and size_gb < level.min_size_gb:
        return False
    # 上限用 not(...) 收尾：与上面的逐条 return False 等价，但不触发 SIM103
    return not (level.max_size_gb and size_gb and size_gb > level.max_size_gb)


def match_level(
    group: RuleGroup, resource: dict[str, Any], info: MetaInfo
) -> tuple[int, str]:
    """返回资源命中的层号（0 起）与层名。

    没命中任何层时返回 ``(UNMATCHED_LEVEL, "")``。
    """
    for index, level in enumerate(group.levels):
        if level_matches(level, resource, info):
            return index, level.label
    return UNMATCHED_LEVEL, ""


def annotate(
    group: RuleGroup | None,
    resources: list[dict[str, Any]],
    meta_of,
) -> list[dict[str, Any]]:
    """给每个资源标注命中的层级，并按「层级优先、层内按分」排序。

    Args:
        group: 规则组；``None`` 或空组时原样返回（不改变既有行为）。
        resources: 已经过硬过滤与打分的资源列表（含 ``score``）。
        meta_of: 取资源 ``MetaInfo`` 的函数（由调用方注入，保持本模块无 IO）。

    Returns:
        排序后的列表。``accept_unmatched=False`` 时未命中的资源会被剔除。
    """
    if group is None or group.is_empty:
        return resources

    kept: list[dict[str, Any]] = []
    for resource in resources:
        index, label = match_level(group, resource, meta_of(resource))
        resource["rule_level"] = index
        resource["rule_level_name"] = label
        resource["rule_group"] = group.name
        if index == UNMATCHED_LEVEL and not group.accept_unmatched:
            resource["filter_reason"] = f"未命中规则组「{group.name}」的任何层级"
            continue
        kept.append(resource)

    # 先按层级升序（越靠前的层越优先），层内按既有评分降序
    kept.sort(key=lambda item: (item.get("rule_level", UNMATCHED_LEVEL), -float(item.get("score") or 0)))
    return kept


def describe(group: RuleGroup) -> list[str]:
    """把规则组渲染成人类可读的说明（界面与试算报告用）。"""
    lines = []
    for index, level in enumerate(group.levels, 1):
        conditions = []
        if level.resolution:
            conditions.append(f"分辨率∈{level.resolution}")
        if level.quality:
            conditions.append(f"质量∈{level.quality}")
        if level.effect:
            conditions.append(f"特效∈{level.effect}")
        if level.video_codec:
            conditions.append(f"编码∈{level.video_codec}")
        if level.include:
            conditions.append(f"含{level.include}")
        if level.exclude:
            conditions.append(f"不含{level.exclude}")
        if level.min_seeders:
            conditions.append(f"做种≥{level.min_seeders}")
        if level.min_size_gb:
            conditions.append(f"≥{level.min_size_gb:g}GB")
        if level.max_size_gb:
            conditions.append(f"≤{level.max_size_gb:g}GB")
        lines.append(f"{index}. {level.label}：" + ("、".join(conditions) or "不限"))
    lines.append("兜底：" + ("接受其它资源" if group.accept_unmatched else "不接受未命中资源"))
    return lines
