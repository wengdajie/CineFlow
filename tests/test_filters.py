"""过滤与打分测试。"""

from __future__ import annotations

from app.core.filters import FilterRule, apply_rules, filter_and_rank, score_resource
from app.schemas.enums import ResourceKind


def make(title: str, **kwargs) -> dict:
    """构造资源字典。"""
    base = {
        "title": title,
        "link": "magnet:?xt=urn:btih:" + title[:8],
        "site": kwargs.pop("site", "TestSite"),
        "kind": kwargs.pop("kind", ResourceKind.TORRENT.value),
        "size": kwargs.pop("size", 5 * 1024**3),
        "seeders": kwargs.pop("seeders", 20),
    }
    base.update(kwargs)
    return base


def test_resolution_filter():
    """分辨率不符应被拒绝。"""
    rule = FilterRule(resolutions=["2160p"])
    ok, reason = apply_rules(make("Show.S01E01.1080p.WEB-DL"), rule)
    assert ok is False
    assert "分辨率" in reason

    ok, _ = apply_rules(make("Show.S01E01.2160p.WEB-DL"), rule)
    assert ok is True


def test_exclude_keywords():
    """命中排除词应被拒绝（默认排除枪版）。"""
    ok, reason = apply_rules(make("某电影.2024.CAM.1080p"), FilterRule())
    assert ok is False
    assert "排除" in reason


def test_include_keywords():
    """必含关键词生效。"""
    rule = FilterRule(include="中字")
    assert apply_rules(make("Show.S01E01.1080p"), rule)[0] is False
    assert apply_rules(make("Show.S01E01.1080p.中字"), rule)[0] is True


def test_min_seeders():
    """做种数下限只作用于 BT 资源。"""
    rule = FilterRule(min_seeders=10)
    assert apply_rules(make("Show.1080p", seeders=2), rule)[0] is False
    assert apply_rules(make("Show.1080p", seeders=50), rule)[0] is True
    # 网盘资源不受做种数限制
    pan = make("Show.1080p", seeders=0, kind=ResourceKind.PAN.value)
    rule_pan = FilterRule(min_seeders=10, allow_kinds=[ResourceKind.PAN.value])
    assert apply_rules(pan, rule_pan)[0] is True


def test_episode_matching():
    """只接受包含缺失集的资源。"""
    rule = FilterRule(episodes=[5, 6])
    assert apply_rules(make("Show.S01E03.1080p"), rule)[0] is False
    assert apply_rules(make("Show.S01E05.1080p"), rule)[0] is True
    # 整季合集可用于补齐
    assert apply_rules(make("Show.S01.Complete.1080p"), rule)[0] is True


def test_season_mismatch():
    """季号不符应被拒绝。"""
    rule = FilterRule(season=2)
    assert apply_rules(make("Show.S01E05.1080p"), rule)[0] is False
    assert apply_rules(make("Show.S02E05.1080p"), rule)[0] is True


def test_kind_restriction():
    """资源类型白名单生效。"""
    rule = FilterRule(allow_kinds=[ResourceKind.TORRENT.value])
    pan = make("Show.1080p", kind=ResourceKind.PAN.value)
    assert apply_rules(pan, rule)[0] is False


def test_score_prefers_higher_quality():
    """4K REMUX 应比 720p HDTV 分数更高。"""
    good = make("Show.S01E01.2160p.BluRay.REMUX.HDR.TrueHD")
    poor = make("Show.S01E01.720p.HDTV.x264")
    assert score_resource(good) > score_resource(poor)


def test_score_respects_preference_order():
    """用户偏好顺序应主导分辨率打分。"""
    rule = FilterRule(resolutions=["1080p", "2160p"])
    p1080 = make("Show.S01E01.1080p.WEB-DL")
    p2160 = make("Show.S01E01.2160p.WEB-DL")
    assert score_resource(p1080, rule) > score_resource(p2160, rule)


def test_filter_and_rank_sorted():
    """结果按分数降序，且过滤不合规项。"""
    resources = [
        make("Show.S01E01.720p.HDTV"),
        make("Show.S01E01.2160p.BluRay.REMUX.中字"),
        make("Show.S01E01.CAM"),
        make("Show.S01E01.1080p.WEB-DL"),
    ]
    ranked = filter_and_rank(resources, FilterRule())
    titles = [item["title"] for item in ranked]
    assert len(ranked) == 3  # CAM 被排除
    assert "2160p" in titles[0]
    assert ranked[0]["score"] >= ranked[-1]["score"]


def test_pan_bonus():
    """同名资源下网盘因免下载获得加成。"""
    torrent = make("Show.S01E01.1080p.WEB-DL")
    pan = make("Show.S01E01.1080p.WEB-DL", kind=ResourceKind.PAN.value, seeders=0)
    assert score_resource(pan) > score_resource(torrent) - 100
