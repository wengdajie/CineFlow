"""搜索聚合层：站点名额保护与站点级超时预算（v1.13.0 缺陷回归）。

背景（两个用硬证据复现的真缺陷）：

1. ``apply_site_quota()`` 的轮转交错只在**排序之前**成立，
   :func:`app.core.filters.filter_and_rank` 紧接着做一次全局重排，
   交错顺序当场被打散，再按 ``SEARCH_MAX_RESULTS`` 一刀切
   → 评分天然偏低的站点（网盘没做种数、网页视频没分辨率标签）被**整站抹掉**。
   线上实测：各站 kept 合计 632 条只返回 200 条，
   Nyaa(75) / Bilibili(20) / YouTube(20) 三个站**一条都没进结果**，
   用户观感就是「明明开了 6 个站，结果只有一两个站的东西」。

2. ``_search_one()`` 以前对**每个关键词**各套一次 ``SEARCH_TIMEOUT``，
   而 ``build_keywords`` 带季集时产 3 个关键词 → 一个卡死站 = 3×25 = 75s，
   且 ``asyncio.gather`` 要等最慢的那个 → 整个聚合搜索被单一废站拖死。
"""

import asyncio
import time

import pytest

from app.core.config import settings
from app.providers.base import Resource, SearchProvider
from app.services.search import _search_one, enforce_site_share


def _item(site: str, index: int) -> dict:
    return {"site": site, "title": f"{site}-{index}", "link": f"magnet:?xt=urn:btih:{site}{index:04d}"}


def _ranked(spec: list[tuple[str, int]]) -> list[dict]:
    """按 (站点, 条数) 造出**已按分数排好序**的列表。

    刻意让站点连续成段（而不是交错），复刻 ``filter_and_rank`` 全局重排后的
    真实形态：高分站扎堆在前面，低分站全被压到尾部。
    """
    out: list[dict] = []
    for site, count in spec:
        out.extend(_item(site, i) for i in range(count))
    return out


def _sites(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item["site"]] = counts.get(item["site"], 0) + 1
    return counts


class TestEnforceSiteShare:
    def test_线上比例下没有站点被整体抹掉(self):
        """核心回归：复刻线上比例，5 个站点在截断后都必须还在。"""
        ranked = _ranked([("PanSou", 537), ("Mukaku", 217), ("Nyaa", 75), ("Bilibili", 20), ("YouTube", 20)])
        result = enforce_site_share(ranked, 200)
        counts = _sites(result)
        assert len(result) == 200
        # 修复前这里是 {"PanSou": 142, "Mukaku": 58}，另外三个站为 0
        assert set(counts) == {"PanSou", "Mukaku", "Nyaa", "Bilibili", "YouTube"}, counts
        assert min(counts.values()) > 0, counts

    def test_每个站点都有靠前的曝光位(self):
        """不只是"进了结果"，小站必须能出现在前排，否则等于没有。"""
        ranked = _ranked([("大站", 500), ("小站", 3)])
        head = [item["site"] for item in enforce_site_share(ranked, 200)[:6]]
        assert head.count("小站") == 3, head

    def test_长度不超过上限(self):
        ranked = _ranked([("A", 300), ("B", 300)])
        assert len(enforce_site_share(ranked, 200)) == 200

    def test_桶内保持原有分数序(self):
        """公平不能靠打乱站内顺序换：同一站点内部必须还是高分在前。"""
        ranked = _ranked([("A", 50), ("B", 50)])
        result = enforce_site_share(ranked, 20)
        a_titles = [item["title"] for item in result if item["site"] == "A"]
        assert a_titles == [f"A-{i}" for i in range(len(a_titles))]

    def test_盘搜的分组后缀按主站名归并(self):
        """PanSou 会拆成 ``PanSou·quark`` 等多个分组，不归并就等于给它变相加权。"""
        ranked = _ranked([
            ("PanSou·quark", 100),
            ("PanSou·baidu", 100),
            ("PanSou·aliyun", 100),
            ("Mukaku", 100),
        ])
        counts = _sites(enforce_site_share(ranked, 100))
        pan = sum(value for key, value in counts.items() if key.startswith("PanSou"))
        assert counts["Mukaku"] == 50, counts
        assert pan == 50, counts

    def test_单站点退化为直接截断(self):
        ranked = _ranked([("A", 300)])
        result = enforce_site_share(ranked, 100)
        assert len(result) == 100
        assert [item["title"] for item in result] == [f"A-{i}" for i in range(100)]

    def test_上限为零表示不限制(self):
        ranked = _ranked([("A", 30), ("B", 30)])
        assert enforce_site_share(ranked, 0) == ranked
        assert enforce_site_share(ranked, -1) == ranked

    def test_未超上限时原样返回(self):
        ranked = _ranked([("A", 10), ("B", 10)])
        assert enforce_site_share(ranked, 200) is ranked

    def test_空列表安全(self):
        assert enforce_site_share([], 200) == []

    def test_缺失站点字段不炸(self):
        """站点字段缺失/为 None 的脏数据不能让整次搜索崩掉。"""
        ranked = [{"title": "x"}, {"title": "y", "site": None}, _item("A", 0), _item("A", 1)]
        result = enforce_site_share(ranked, 2)
        assert len(result) == 2


class 慢站(SearchProvider):
    """一个永远不返回的站点（模拟连接挂住）。"""

    name = "t_slow"

    @property
    def site_name(self) -> str:
        return "慢站"

    async def search(self, keyword, **kwargs):
        await asyncio.sleep(3600)
        return []


class 空站(SearchProvider):
    """立刻返回空结果，需要继续试下一个关键词的正常路径。"""

    name = "t_empty"

    def __init__(self, config=None):
        super().__init__(config or {})
        self.tried: list[str] = []

    @property
    def site_name(self) -> str:
        return "空站"

    async def search(self, keyword, **kwargs):
        self.tried.append(keyword)
        return []


def _run_one(provider, keywords):
    async def _go():
        return await _search_one(
            provider,
            keywords,
            media_type=None,
            season=None,
            episode=None,
            semaphore=asyncio.Semaphore(4),
        )

    started = time.perf_counter()
    results, outcome = asyncio.run(_go())
    return results, outcome, time.perf_counter() - started


class TestSiteTimeoutBudget:
    """``SEARCH_TIMEOUT`` 必须是**整个站点**的预算，不是每个关键词各给一份。"""

    @pytest.fixture(autouse=True)
    def _fast_timeout(self, monkeypatch):
        # 用 2s 预算跑，真实默认是 25s；比例关系一致，测试不用等 75s
        monkeypatch.setattr(settings, "SEARCH_TIMEOUT", 2)

    def test_卡死站不按关键词数量翻倍(self):
        """3 个关键词的卡死站以前要花 3×timeout，现在必须 ≈1×timeout。"""
        keywords = ["剧名 S02E05", "剧名 S02", "剧名"]
        _, outcome, elapsed = _run_one(慢站({}), keywords)
        assert outcome.status == "timeout"
        # 修复前 ≈6s（3×2s）；留出调度余量，只要明显小于 2 倍预算即可
        assert elapsed < settings.SEARCH_TIMEOUT * 1.6, elapsed
        assert elapsed >= settings.SEARCH_TIMEOUT * 0.8, elapsed

    def test_超时原因写清楚是站点预算(self):
        _, outcome, _ = _run_one(慢站({}), ["剧名 S02E05", "剧名"])
        assert "预算" in outcome.message, outcome.message

    def test_快速返回空的站点仍会试完所有关键词(self):
        """别把「站点很快返回空、需要试下一个关键词」这个正常路径一起砍掉。"""
        provider = 空站({})
        keywords = ["剧名 S02E05", "剧名 S02", "剧名"]
        results, outcome, elapsed = _run_one(provider, keywords)
        assert provider.tried == keywords, provider.tried
        assert results == []
        assert outcome.status == "empty"
        assert elapsed < 1

    def test_命中即止不浪费预算(self):
        class 好站(空站):
            name = "t_hit"

            @property
            def site_name(self) -> str:
                return "好站"

            async def search(self, keyword, **kwargs):
                self.tried.append(keyword)
                return [Resource(title="剧名 1080p", link="magnet:?xt=urn:btih:hit01", site="好站")]

        provider = 好站({})
        results, outcome, _ = _run_one(provider, ["剧名 S02E05", "剧名"])
        assert provider.tried == ["剧名 S02E05"]
        assert len(results) == 1
        assert outcome.status == "ok"
        assert outcome.keyword == "剧名 S02E05"

    def test_多站点整体不被单一卡死站拖累(self):
        """gather 要等最慢的：卡死站的上限就是整体的上限。"""
        from app.services import search as search_service

        provider = 空站({})
        started = time.perf_counter()
        results, outcomes = asyncio.run(
            search_service.search_detailed(
                "剧名",
                media_type="tv",
                season=2,
                episode=5,
                providers=[慢站({}), provider],
                save_history=False,
            )
        )
        elapsed = time.perf_counter() - started
        by_site = {o.site: o.status for o in outcomes}
        assert by_site["慢站"] == "timeout"
        assert by_site["空站"] == "empty"
        assert results == []
        # 修复前 ≈3×2s=6s（build_keywords 对 S02E05 产 3 个关键词）
        assert elapsed < settings.SEARCH_TIMEOUT * 1.6, elapsed
