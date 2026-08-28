"""资源过滤与优先级打分。

订阅/搜索得到的候选资源需要经过两步处理：

1. ``apply_rules``：硬性过滤（分辨率、关键词、体积、做种数…）
2. ``score_resource``：按偏好打分，用于挑选最优版本
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.meta import MetaInfo, parse
from app.schemas.enums import ResourceKind
from app.utils.strings import match_keywords, parse_size, split_keywords

# 打分权重表：值越大越优先
RESOLUTION_SCORE = {"2160p": 100, "1080p": 80, "720p": 50, "576p": 30, "480p": 20}
QUALITY_SCORE = {
    "UHDBluRay": 100,
    "REMUX": 95,
    "BluRay": 85,
    "WEB-DL": 75,
    "HDTV": 50,
    "DVD": 30,
    "TC": 5,
    "CAM": 1,
}
EFFECT_SCORE = {
    "Dolby Vision": 30,
    "HDR10+": 25,
    "HDR10": 20,
    "HDR": 15,
    "SDR": 5,
    "3D": 0,
}
CODEC_SCORE = {"AV1": 20, "H265": 18, "H264": 12, "VC-1": 6, "MPEG2": 2}
AUDIO_SCORE = {
    "TrueHD Atmos": 30,
    "DTS-X": 28,
    "TrueHD": 24,
    "DTS-HD MA": 22,
    "FLAC": 18,
    "DTS": 16,
    "DDP": 12,
    "AC3": 8,
    "AAC": 6,
    "MP3": 2,
}
LOCALIZED_KEYWORDS = ["中字", "简繁", "国语", "双语", "中文字幕", "官中"]


@dataclass
class FilterRule:
    """过滤规则（订阅与手动搜索共用）。"""

    resolutions: list[str] = field(default_factory=list)
    qualities: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    include: str | None = None
    exclude: str | None = None
    min_seeders: int = 0
    min_size_mb: int = 0
    max_size_mb: int = 0
    allow_kinds: list[str] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    title_keywords: list[str] = field(default_factory=list)

    @classmethod
    def from_subscribe(cls, subscribe: Any) -> FilterRule:
        """由订阅记录构建规则。"""
        allow: list[str] = []
        if getattr(subscribe, "allow_torrent", True):
            allow += [ResourceKind.TORRENT.value, ResourceKind.MAGNET.value]
        if getattr(subscribe, "allow_pan", True):
            allow += [ResourceKind.PAN.value, ResourceKind.DIRECT.value]
        return cls(
            resolutions=split_keywords(getattr(subscribe, "resolution", None)),
            qualities=split_keywords(getattr(subscribe, "quality", None)),
            effects=split_keywords(getattr(subscribe, "effect", None)),
            include=getattr(subscribe, "include", None),
            exclude=getattr(subscribe, "exclude", None),
            min_seeders=getattr(subscribe, "min_seeders", 0) or 0,
            allow_kinds=allow,
            sites=list(getattr(subscribe, "sources", []) or []),
            season=getattr(subscribe, "season", None),
        )


def _resource_text(resource: dict[str, Any]) -> str:
    """拼出用于关键词匹配的文本。"""
    return " ".join(
        str(resource.get(key) or "")
        for key in ("title", "description", "site", "release_group")
    )


def resource_meta(resource: dict[str, Any]) -> MetaInfo:
    """取得（或惰性解析并缓存）资源元数据。"""
    cached = resource.get("_meta")
    if isinstance(cached, MetaInfo):
        return cached
    info = parse(resource.get("title", ""))
    resource["_meta"] = info
    return info


def apply_rules(
    resource: dict[str, Any], rule: FilterRule | None = None
) -> tuple[bool, str]:
    """判断资源是否满足规则。

    Returns:
        ``(是否通过, 原因)``；未通过时原因说明被拒绝的条件。
    """
    rule = rule or FilterRule()
    info = resource_meta(resource)
    text = _resource_text(resource)

    kind = str(resource.get("kind") or ResourceKind.TORRENT.value)
    if rule.allow_kinds and kind not in rule.allow_kinds:
        return False, f"资源类型 {kind} 不在允许范围"

    if rule.sites and str(resource.get("site") or "") not in rule.sites:
        return False, f"站点 {resource.get('site')} 不在指定范围"

    if rule.resolutions and (info.resolution or "") not in rule.resolutions:
        return False, f"分辨率 {info.resolution or '未知'} 不匹配"

    if rule.qualities and (info.quality or "") not in rule.qualities:
        return False, f"质量 {info.quality or '未知'} 不匹配"

    if rule.effects and (info.effect or "") not in rule.effects:
        return False, f"特效 {info.effect or '未知'} 不匹配"

    exclude = rule.exclude or ",".join(settings.EXCLUDE_KEYWORDS)
    if exclude and match_keywords(text, exclude, mode="any"):
        return False, "命中排除关键词"

    include = rule.include or ",".join(settings.INCLUDE_KEYWORDS)
    if include and not match_keywords(text, include, mode="all"):
        return False, "未命中必需关键词"

    if rule.title_keywords and not match_keywords(text, rule.title_keywords, mode="any"):
        return False, "标题与订阅名称不匹配"

    seeders = int(resource.get("seeders") or 0)
    min_seeders = max(rule.min_seeders, settings.MIN_SEEDERS)
    is_bt = kind in (ResourceKind.TORRENT.value, ResourceKind.MAGNET.value)
    if is_bt and seeders < min_seeders:
        return False, f"做种数 {seeders} 低于下限 {min_seeders}"

    size = parse_size(resource.get("size"))
    if rule.min_size_mb and size and size < rule.min_size_mb * 1024**2:
        return False, "体积过小"
    if rule.max_size_mb and size > rule.max_size_mb * 1024**2:
        return False, "体积过大"

    if rule.season is not None and info.season is not None and info.season != rule.season:
        return False, f"季 {info.season} 与订阅季 {rule.season} 不符"

    if rule.episodes:
        if info.episodes and not set(info.episodes) & set(rule.episodes):
            return False, "不包含缺失的集数"
        if not info.episodes and not info.is_season_pack:
            return False, "无法识别集数"

    return True, "通过"


def score_resource(resource: dict[str, Any], rule: FilterRule | None = None) -> float:
    """为资源打分，用于最优版本排序。"""
    rule = rule or FilterRule()
    info = resource_meta(resource)
    score = 0.0

    # 分辨率：优先跟随用户偏好顺序
    preferred = rule.resolutions or settings.PREFER_RESOLUTIONS
    if info.resolution:
        if info.resolution in preferred:
            score += 1000 - preferred.index(info.resolution) * 120
        else:
            score += RESOLUTION_SCORE.get(info.resolution, 0)

    score += QUALITY_SCORE.get(info.quality or "", 0) * 3
    score += EFFECT_SCORE.get(info.effect or "", 0) * 2
    score += CODEC_SCORE.get(info.video_codec or "", 0)
    score += AUDIO_SCORE.get(info.audio_codec or "", 0)

    # 做种数（开方增益，避免热门大站单纯以人气压制小站好资源）
    seeders = int(resource.get("seeders") or 0)
    score += min(seeders, 1000) ** 0.5 * 4

    # 体积（同分辨率下略偏好更高码率，但设上限）
    size_gb = parse_size(resource.get("size")) / 1024**3
    score += min(size_gb, 80) * 1.5

    # 站点优先级（数字越小越优先）
    priority = int(resource.get("priority") or 50)
    score += (100 - min(priority, 100)) * 0.5

    # 网盘资源免下载、可秒转存，给少量加成
    if str(resource.get("kind")) == ResourceKind.PAN.value:
        score += 40

    # 季包在补全整季时更高效
    if info.is_season_pack and len(rule.episodes) != 1:
        score += 25

    # 中字/国语等本地化偏好
    if match_keywords(_resource_text(resource), LOCALIZED_KEYWORDS, mode="any"):
        score += 30

    resource["score"] = round(score, 2)
    return resource["score"]


def filter_and_rank(
    resources: list[dict[str, Any]], rule: FilterRule | None = None
) -> list[dict[str, Any]]:
    """过滤 + 打分 + 排序，返回通过筛选的资源（分数降序）。"""
    passed: list[dict[str, Any]] = []
    for resource in resources:
        ok, reason = apply_rules(resource, rule)
        resource["filter_reason"] = reason
        if not ok:
            continue
        score_resource(resource, rule)
        passed.append(resource)
    passed.sort(key=lambda item: item.get("score", 0), reverse=True)
    return passed
