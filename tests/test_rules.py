"""过滤规则组（``app.core.rules``）的单元测试。

这一层是纯函数无 IO，所以能完全离线穷举边界（ADR-10 的同一套思路）。
"""

from __future__ import annotations

from app.core import filters
from app.core.rules import (
    UNMATCHED_LEVEL,
    RuleGroup,
    RuleLevel,
    annotate,
    describe,
    level_matches,
    match_level,
)


def res(title: str, **kwargs):
    item = {"title": title, "kind": "torrent", "size": 4 * 1024**3, "seeders": 50}
    item.update(kwargs)
    return item


def meta_of(item):
    return filters.resource_meta(item)


def group_of(levels, accept_unmatched=True, name="测试组"):
    return RuleGroup(
        name=name,
        levels=[RuleLevel.from_dict(item) for item in levels],
        accept_unmatched=accept_unmatched,
    )


# ---------------- 单层命中 ----------------
def test_empty_condition_means_no_limit():
    """字段留空 = 不限制，这是"不逼用户填满一堆框"的关键语义。"""
    level = RuleLevel()
    item = res("随便什么 480p.mp4")
    assert level_matches(level, item, meta_of(item)) is True


def test_resolution_and_quality_match():
    level = RuleLevel(resolution="1080p", quality="BluRay|REMUX")
    ok = res("剧名 S01E01.1080p.BluRay.x264.mkv")
    bad = res("剧名 S01E01.1080p.WEB-DL.x264.mkv")
    worse = res("剧名 S01E01.2160p.BluRay.x265.mkv")
    assert level_matches(level, ok, meta_of(ok))
    assert not level_matches(level, bad, meta_of(bad))
    assert not level_matches(level, worse, meta_of(worse))


def test_multi_value_separators():
    """``|`` ``,`` ``、`` 都能分隔，且大小写不敏感。"""
    level = RuleLevel(resolution="2160P、1080p")
    for title in ("片 2160p.WEB-DL.mkv", "片 1080p.WEB-DL.mkv"):
        item = res(title)
        assert level_matches(level, item, meta_of(item)), title


def test_include_and_exclude_keywords():
    level = RuleLevel(include="中字|简繁", exclude="预告|抢先")
    ok = res("剧名 S01E01.1080p.中字.mkv")
    missing = res("剧名 S01E01.1080p.mkv")
    excluded = res("剧名 S01E01.1080p.中字.抢先版.mkv")
    assert level_matches(level, ok, meta_of(ok))
    assert not level_matches(level, missing, meta_of(missing))
    assert not level_matches(level, excluded, meta_of(excluded))


def test_seeders_and_size_bounds():
    level = RuleLevel(min_seeders=10, min_size_gb=2, max_size_gb=8)
    ok = res("片 1080p.mkv", seeders=20, size=4 * 1024**3)
    few = res("片 1080p.mkv", seeders=3, size=4 * 1024**3)
    big = res("片 1080p.mkv", seeders=20, size=20 * 1024**3)
    small = res("片 1080p.mkv", seeders=20, size=1 * 1024**3)
    assert level_matches(level, ok, meta_of(ok))
    assert not level_matches(level, few, meta_of(few))
    assert not level_matches(level, big, meta_of(big))
    assert not level_matches(level, small, meta_of(small))


def test_zero_size_is_not_used_to_reject():
    """站点没给体积时不能据此否掉资源，否则网盘资源会被全部滤掉。"""
    level = RuleLevel(min_size_gb=5)
    item = res("网盘资源 1080p", size=0)
    assert level_matches(level, item, meta_of(item))


# ---------------- 层级判定 ----------------
def test_match_level_returns_first_hit():
    group = group_of([
        {"name": "4K", "resolution": "2160p"},
        {"name": "1080P", "resolution": "1080p"},
    ])
    item = res("片 1080p.WEB-DL.mkv")
    index, label = match_level(group, item, meta_of(item))
    assert (index, label) == (1, "1080P")


def test_match_level_unmatched():
    group = group_of([{"resolution": "2160p"}])
    item = res("片 720p.HDTV.mp4")
    index, label = match_level(group, item, meta_of(item))
    assert index == UNMATCHED_LEVEL and label == ""


def test_level_label_falls_back_to_conditions():
    """用户没给层起名时，界面也得显示得出来。"""
    assert RuleLevel(resolution="1080p", include="中字").label == "1080p 中字"
    assert RuleLevel().label == "不限"


# ---------------- annotate 排序 ----------------
def test_annotate_prefers_layer_over_score():
    """核心价值：宁可要 1080p 中字，也不要没字幕的 4K。"""
    items = [
        res("剧名 S01E01.2160p.WEB-DL.H265.mkv"),
        res("剧名 S01E01.1080p.WEB-DL.中字.mkv"),
    ]
    for item in items:
        filters.score_resource(item)
    # 不带规则组：4K 评分更高，排前面
    plain = sorted(items, key=lambda x: -x["score"])
    assert "2160p" in plain[0]["title"]

    group = group_of([
        {"name": "1080p 中字", "resolution": "1080p", "include": "中字"},
        {"name": "其它", "resolution": ""},
    ])
    ranked = annotate(group, list(items), meta_of)
    assert "1080p" in ranked[0]["title"], "带规则组时 1080p 中字必须排第一"
    assert ranked[0]["rule_level"] == 0
    assert ranked[0]["rule_level_name"] == "1080p 中字"
    assert ranked[0]["rule_group"] == "测试组"


def test_annotate_sorts_by_score_within_a_layer():
    items = [res("片 1080p.WEB-DL.mkv"), res("片 1080p.BluRay.mkv")]
    for item in items:
        filters.score_resource(item)
    group = group_of([{"resolution": "1080p"}])
    ranked = annotate(group, items, meta_of)
    assert all(item["rule_level"] == 0 for item in ranked)
    assert ranked[0]["score"] >= ranked[1]["score"]


def test_annotate_drops_unmatched_when_not_accepted():
    items = [res("片 1080p.WEB-DL.mkv"), res("片 480p.TC.mp4")]
    for item in items:
        filters.score_resource(item)
    group = group_of([{"resolution": "1080p"}], accept_unmatched=False)
    ranked = annotate(group, items, meta_of)
    assert len(ranked) == 1
    assert "1080p" in ranked[0]["title"]
    # 被剔除的资源要留下人能看懂的原因
    assert "未命中" in items[1]["filter_reason"]


def test_annotate_keeps_unmatched_at_the_end_when_accepted():
    items = [res("片 480p.mp4"), res("片 1080p.mkv")]
    for item in items:
        filters.score_resource(item)
    group = group_of([{"resolution": "1080p"}])
    ranked = annotate(group, items, meta_of)
    assert len(ranked) == 2
    assert ranked[0]["rule_level"] == 0
    assert ranked[-1]["rule_level"] == UNMATCHED_LEVEL


def test_annotate_noop_for_empty_group():
    """空组/None 必须原样返回，保证 v1.4.0 的行为不被改变。"""
    items = [res("片 1080p.mkv")]
    assert annotate(None, items, meta_of) is items
    assert annotate(RuleGroup(name="空"), items, meta_of) is items


def test_filter_and_rank_accepts_group():
    """规则组要能直接接进既有的 filter_and_rank（订阅/搜索共用这条路）。"""
    resources = [
        res("剧名 S01E01.2160p.WEB-DL.mkv"),
        res("剧名 S01E01.1080p.WEB-DL.中字.mkv"),
    ]
    group = group_of([
        {"name": "1080p 中字", "resolution": "1080p", "include": "中字"},
        {"name": "兜底", "resolution": ""},
    ])
    ranked = filters.filter_and_rank(resources, None, group)
    assert "1080p" in ranked[0]["title"]
    # 不给 group 时保持纯评分行为
    plain = filters.filter_and_rank(
        [res("剧名 S01E01.2160p.WEB-DL.mkv"), res("剧名 S01E01.1080p.WEB-DL.中字.mkv")]
    )
    assert "2160p" in plain[0]["title"]


# ---------------- 序列化与说明 ----------------
def test_from_dict_is_tolerant():
    level = RuleLevel.from_dict(
        {"name": " 4K ", "resolution": "2160p", "min_seeders": "abc",
         "min_size_gb": None, "unknown": "x"}
    )
    assert level.name == "4K"
    assert level.min_seeders == 0 and level.min_size_gb == 0.0


def test_round_trip_dict():
    original = RuleLevel(name="A", resolution="1080p", include="中字", min_seeders=5)
    assert RuleLevel.from_dict(original.to_dict()) == original


def test_from_record_supports_dict_and_object():
    payload = {"name": "组", "levels": [{"resolution": "1080p"}], "accept_unmatched": False}
    group = RuleGroup.from_record(payload)
    assert group.name == "组" and len(group.levels) == 1
    assert group.accept_unmatched is False
    assert group.is_empty is False

    class Fake:
        name = "对象组"
        levels = [{"resolution": "2160p"}, "坏数据"]
        accept_unmatched = True

    other = RuleGroup.from_record(Fake())
    assert other.name == "对象组"
    assert len(other.levels) == 1, "非 dict 的脏数据要被忽略而不是报错"


def test_describe_is_human_readable():
    group = group_of([
        {"name": "4K", "resolution": "2160p", "quality": "REMUX", "min_size_gb": 20},
        {"include": "中字", "exclude": "预告", "min_seeders": 5, "max_size_gb": 8},
    ], accept_unmatched=False)
    lines = describe(group)
    assert len(lines) == 3
    assert "4K" in lines[0] and "2160p" in lines[0] and "20GB" in lines[0]
    assert "中字" in lines[1] and "预告" in lines[1]
    assert "不接受未命中资源" in lines[-1]
