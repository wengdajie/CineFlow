"""发现榜测试（豆瓣分类榜 / B 站排行榜 / 本地片源标注）。

全程离线：网络层用 monkeypatch 打掉，样本取自实测抓取后裁剪的真实响应。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.providers.indexer import bili_chart
from app.providers.metadata import douban_chart
from app.services import discover


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean():
    """每个用例前后都清缓存，避免用例间互相污染。"""
    douban_chart.reset_state()
    bili_chart.reset_state()
    yield
    douban_chart.reset_state()
    bili_chart.reset_state()


# ---------------- 豆瓣分类榜 ----------------
#: 真实 search_subjects 响应片段
DOUBAN_SAMPLE = {
    "subjects": [
        {
            "episodes_info": "更新至10集",
            "rate": "8.3",
            "title": "早春晴朗",
            "url": "https://movie.douban.com/subject/36660844/",
            "cover": "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2932319485.jpg",
            "id": "36660844",
            "is_new": True,
            "playable": True,
        },
        {
            # 评分为空串：未上映或无人评分，绝不能渲染成 0 分
            "episodes_info": "36集全",
            "rate": "",
            "title": "花开锦绣",
            "url": "https://movie.douban.com/subject/36000001/",
            "cover": "https://img3.doubanio.com/view/photo/s_ratio_poster/public/p29.jpg",
            "id": "36000001",
            "is_new": False,
            "playable": False,
        },
        {"rate": "7.0", "title": "", "id": "1"},  # 无标题应被丢弃
    ]
}


def test_douban_chart_parses_real_shape(monkeypatch):
    async def fake(url, **kwargs):
        assert "search_subjects" in url
        return DOUBAN_SAMPLE

    monkeypatch.setattr(douban_chart, "fetch_json", fake)
    rows = run(douban_chart.chart("tv", limit=10))
    assert len(rows) == 2  # 无标题那条被丢掉
    assert rows[0]["title"] == "早春晴朗"
    assert rows[0]["rating"] == 8.3
    assert rows[0]["episodes_info"] == "更新至10集"
    assert rows[0]["source"] == "douban"
    assert rows[0]["media_type"] == "tv"


def test_douban_empty_rating_is_none_not_zero(monkeypatch):
    """空评分必须是 None —— 把"没有数据"显示成"0 分"是在制造错误信息。"""

    async def fake(url, **kwargs):
        return DOUBAN_SAMPLE

    monkeypatch.setattr(douban_chart, "fetch_json", fake)
    rows = run(douban_chart.chart("tv", limit=10))
    assert rows[1]["title"] == "花开锦绣"
    assert rows[1]["rating"] is None


def test_douban_all_four_categories_are_configured():
    """电影/电视剧/动漫/综艺四类都要有映射，且动漫不能用实测返回 0 条的 tag。"""
    assert set(douban_chart.CATEGORIES) == {"movie", "tv", "anime", "show"}
    assert douban_chart.CATEGORIES["anime"]["tag"] == "日本动画"
    assert douban_chart.CATEGORIES["show"]["tag"] == "综艺"


def test_douban_unknown_category_returns_empty():
    assert run(douban_chart.chart("nope", limit=5)) == []


def test_douban_uses_cache(monkeypatch):
    calls = {"n": 0}

    async def fake(url, **kwargs):
        calls["n"] += 1
        return DOUBAN_SAMPLE

    monkeypatch.setattr(douban_chart, "fetch_json", fake)
    run(douban_chart.chart("movie", limit=5))
    run(douban_chart.chart("movie", limit=5))
    assert calls["n"] == 1  # 第二次命中缓存


def test_douban_failure_marks_rate_limited(monkeypatch):
    async def fake(url, **kwargs):
        return None

    monkeypatch.setattr(douban_chart, "fetch_json", fake)
    assert run(douban_chart.chart("movie", limit=5)) == []
    assert douban_chart.is_rate_limited() is True
    # 退避期内不再发请求
    run(douban_chart.chart("tv", limit=5))


# ---------------- B 站榜单 ----------------
BILI_UGC = {
    "code": 0,
    "data": {
        "list": [
            {
                "bvid": "BV1N1tF6SE2S",
                "title": "测试视频标题",
                "pic": "http://i2.hdslb.com/bfs/archive/abc.jpg",
                "duration": 728,
                "desc": "简介",
                "owner": {"name": "某 UP 主"},
                "stat": {"view": 5065276, "like": 119640},
            },
            {"title": "没有 bvid 的条目", "pic": "x"},  # 应被丢弃
        ]
    },
}

BILI_PGC = {
    "code": 0,
    "data": {
        "list": [
            {
                "title": "凡人修仙传",
                "cover": "https://i0.hdslb.com/bfs/bangumi/image/abc.jpg",
                "rating": "9.7分",
                "stat": {"view": 7209865248, "follow": 100},
                "new_ep": {"index_show": "更新至第49话"},
                "url": "https://www.bilibili.com/bangumi/play/ss1",
            }
        ]
    },
}


class _FakeResponse:
    def __init__(self, payload: Any):
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """假的 async_client：记录请求过的 URL，按 URL 返回对应样本。"""

    def __init__(self, payload: Any, seen: list[str]):
        self._payload = payload
        self._seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, **kwargs):
        self._seen.append(url)
        return _FakeResponse(self._payload)


def test_bili_ugc_chart_parses(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        bili_chart, "async_client", lambda **kw: _FakeClient(BILI_UGC, seen)
    )
    rows = run(bili_chart.chart("all", limit=10))
    assert len(rows) == 1  # 无 bvid 的被丢掉
    assert rows[0]["title"] == "测试视频标题"
    assert rows[0]["heat"] == 5065276
    assert rows[0]["uploader"] == "某 UP 主"
    # 封面 http:// 必须升级成 https://，否则页面混合内容会被浏览器拦
    assert rows[0]["poster"].startswith("https://")
    # 必须先预热首页拿 buvid3，否则 ranking/v2 返回 -352
    assert any(u == bili_chart.HOME_URL for u in seen)


def test_bili_pgc_uses_separate_endpoint(monkeypatch):
    """番剧/国创是 PGC，必须走 pgc/season/rank，走 ranking/v2 会返回 -400。"""
    seen: list[str] = []
    monkeypatch.setattr(
        bili_chart, "async_client", lambda **kw: _FakeClient(BILI_PGC, seen)
    )
    rows = run(bili_chart.chart("bangumi", limit=10))
    assert len(rows) == 1
    assert rows[0]["title"] == "凡人修仙传"
    assert rows[0]["rating"] == 9.7  # "9.7分" → 9.7
    assert rows[0]["media_type"] == "tv"
    assert rows[0]["episodes_info"] == "更新至第49话"
    assert any("pgc/season/rank" in u for u in seen)


def test_bili_risk_control_triggers_backoff(monkeypatch):
    """code=-352 是风控，要退避而不是继续撞墙。"""
    seen: list[str] = []
    monkeypatch.setattr(
        bili_chart,
        "async_client",
        lambda **kw: _FakeClient({"code": -352, "data": None}, seen),
    )
    assert run(bili_chart.chart("all", limit=5)) == []
    assert bili_chart.is_rate_limited() is True


def test_bili_unknown_category():
    assert run(bili_chart.chart("nope", limit=5)) == []


def test_bili_partitions_cover_video_kinds():
    keys = set(bili_chart.CATEGORIES)
    for expected in ("all", "bangumi", "guochuang", "movie", "teleplay"):
        assert expected in keys


# ---------------- 发现服务 ----------------
def test_discover_categories_are_five():
    """电影/电视剧/动漫/综艺/Bilibili 五个页签。"""
    cats = discover.categories()
    assert [c["key"] for c in cats] == ["movie", "tv", "anime", "show", "bilibili"]
    assert [c["label"] for c in cats] == ["电影", "电视剧", "动漫", "综艺", "Bilibili"]


def test_discover_chart_annotates_local(monkeypatch):
    """榜单条目要标注本地有没有片源——这是相对纯榜单站的价值。"""

    async def fake_chart(category, *, limit=20, offset=0):
        return [{"title": "凡人修仙传", "poster": None, "rating": 9.0}]

    monkeypatch.setattr(douban_chart, "chart", fake_chart)
    monkeypatch.setattr(
        discover,
        "_local_titles",
        lambda: {"凡人修仙传": {"count": 7, "sites": {"站点A", "站点B"}}},
    )
    data = run(discover.chart("movie", limit=5))
    assert data["count"] == 1
    assert data["items"][0]["local_count"] == 7
    assert data["items"][0]["local_sites"] == ["站点A", "站点B"]
    assert data["items"][0]["rank"] == 1


def test_discover_chart_no_local_match(monkeypatch):
    async def fake_chart(category, *, limit=20, offset=0):
        return [{"title": "某冷门片", "poster": None}]

    monkeypatch.setattr(douban_chart, "chart", fake_chart)
    monkeypatch.setattr(discover, "_local_titles", lambda: {})
    data = run(discover.chart("movie", limit=5))
    assert data["items"][0]["local_count"] == 0
    assert data["items"][0]["local_sites"] == []


def test_discover_unknown_category_is_not_error():
    """未知分类返回空 items + 可读 message，而不是抛异常/404。"""
    data = run(discover.chart("nope", limit=5))
    assert data["items"] == []
    assert "未知分类" in data["message"]


def test_discover_empty_gives_actionable_message(monkeypatch):
    """拿不到数据时要说明原因（限流 vs 网络），而不是干巴巴的"暂无"。"""

    async def fake_chart(category, *, limit=20, offset=0):
        return []

    monkeypatch.setattr(douban_chart, "chart", fake_chart)
    monkeypatch.setattr(douban_chart, "is_rate_limited", lambda: True)
    monkeypatch.setattr(discover, "_local_titles", lambda: {})
    data = run(discover.chart("movie", limit=5))
    assert "限流" in data["message"]


def test_discover_bilibili_routes_to_bili(monkeypatch):
    """Bilibili 页签必须走 B 站源而不是豆瓣。"""
    called = {"bili": False}

    async def fake_bili(category, *, limit=20, offset=0):
        called["bili"] = True
        return [{"title": "视频", "heat": 100}]

    monkeypatch.setattr(bili_chart, "chart", fake_bili)
    monkeypatch.setattr(discover, "_local_titles", lambda: {})
    data = run(discover.chart("bilibili", limit=5))
    assert called["bili"] is True
    assert data["source"] == "bilibili"


def test_discover_overview_returns_all_charts(monkeypatch):
    async def fake_douban(category, *, limit=20, offset=0):
        return [{"title": "片 " + category}]

    async def fake_bili(category, *, limit=20, offset=0):
        return [{"title": "视频"}]

    monkeypatch.setattr(douban_chart, "chart", fake_douban)
    monkeypatch.setattr(bili_chart, "chart", fake_bili)
    monkeypatch.setattr(discover, "_local_titles", lambda: {})
    data = run(discover.overview(limit=3))
    assert len(data["charts"]) == 5
    assert len(data["categories"]) == 5


def test_discover_overview_survives_one_source_failing(monkeypatch):
    """一个来源炸了不能让整页 500。"""

    async def boom(category, *, limit=20):
        raise RuntimeError("豆瓣挂了")

    async def fake_bili(category, *, limit=20, offset=0):
        return [{"title": "视频"}]

    monkeypatch.setattr(douban_chart, "chart", boom)
    monkeypatch.setattr(bili_chart, "chart", fake_bili)
    monkeypatch.setattr(discover, "_local_titles", lambda: {})
    data = run(discover.overview(limit=3))
    assert len(data["charts"]) == 5
    bili = next(c for c in data["charts"] if c["category"] == "bilibili")
    assert bili["count"] == 1


def test_discover_bili_partition_chart(monkeypatch):
    async def fake_bili(category, *, limit=20, offset=0):
        return [{"title": "番剧条目"}]

    monkeypatch.setattr(bili_chart, "chart", fake_bili)
    monkeypatch.setattr(discover, "_local_titles", lambda: {})
    data = run(discover.bili_categories_chart("bangumi", limit=5))
    assert data["label"] == "番剧"
    assert data["count"] == 1


def test_discover_bili_partition_unknown():
    data = run(discover.bili_categories_chart("nope", limit=5))
    assert data["items"] == []


def test_local_titles_reads_real_columns():
    """回归：ResourceRecord 的列名是 site（不是 site_name），且无 parsed_title。

    首版实现照抄了猜测的列名导致整个标注功能静默失效（返回空索引），
    这个用例锁住"至少能跑通不抛异常"。
    """
    index = discover._local_titles()
    assert isinstance(index, dict)


# --- 下拉加载更多（offset 分页）的回归用例 ---------------------------------
# 背景：热度排行改为「首屏 30 条 + 下拉追加」，两个来源的分页机制完全不同：
#   豆瓣 page_start 是真分页；B 站排行榜没有分页参数，只能服务端切片。
# 这几条用例钉住「名次跨页连续」与「has_more 到底为 False」两个前端依赖的契约。


def test_discover_chart_offset_continues_rank(monkeypatch):
    """第二页的名次必须接着第一页，不能每页都从 1 重新开始。"""

    async def fake_chart(category, *, limit=20, offset=0):
        # 模拟真分页：按 offset 返回不同批次
        return [{"title": f"片{offset + i}"} for i in range(limit)]

    monkeypatch.setattr(discover.douban_chart, "chart", fake_chart)
    data = asyncio.run(discover.chart("movie", limit=30, offset=30))
    assert data["offset"] == 30
    assert [r["rank"] for r in data["items"]][:3] == [31, 32, 33]
    assert data["items"][-1]["rank"] == 60


def test_discover_chart_has_more_false_on_short_page(monkeypatch):
    """取不满一页说明到底了，has_more 必须为 False，否则前端会无限点。"""

    async def fake_chart(category, *, limit=20, offset=0):
        return [{"title": "只剩三条"}] * 3

    monkeypatch.setattr(discover.douban_chart, "chart", fake_chart)
    data = asyncio.run(discover.chart("movie", limit=30))
    assert data["has_more"] is False
    assert data["count"] == 3


def test_discover_chart_has_more_true_on_full_page(monkeypatch):
    """取满一页就认为还有下一页。"""

    async def fake_chart(category, *, limit=20, offset=0):
        return [{"title": f"片{i}"} for i in range(limit)]

    monkeypatch.setattr(discover.douban_chart, "chart", fake_chart)
    data = asyncio.run(discover.chart("movie", limit=30))
    assert data["has_more"] is True


def test_discover_chart_offset_does_not_mask_empty_message(monkeypatch):
    """翻页翻到空不该再报「限流」——那条提示只在第一页有意义。"""

    async def fake_chart(category, *, limit=20, offset=0):
        return []

    monkeypatch.setattr(discover.douban_chart, "chart", fake_chart)
    first = asyncio.run(discover.chart("movie", limit=30, offset=0))
    later = asyncio.run(discover.chart("movie", limit=30, offset=60))
    assert first["message"], "第一页为空要给可读原因"
    assert not later["message"], "翻页到底不该再弹限流提示"


def test_discover_bili_partition_offset(monkeypatch):
    """B 站分区榜同样支持 offset，且名次连续。"""

    async def fake_bili(category, *, limit=20, offset=0):
        return [{"title": f"番{offset + i}"} for i in range(limit)]

    monkeypatch.setattr(discover.bili_chart, "chart", fake_bili)
    data = asyncio.run(discover.bili_categories_chart("bangumi", limit=30, offset=30))
    assert data["offset"] == 30
    assert data["items"][0]["rank"] == 31
    assert data["has_more"] is True
