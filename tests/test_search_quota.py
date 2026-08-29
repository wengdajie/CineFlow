"""搜索聚合层：单站配额与站点诊断（v1.6.0 任务 4 回归）。

背景：v1.5.0 的聚合层直接 ``extend`` 各站结果后做全局截断，于是返回量大的
站点会把小站整体挤出结果（实测盘搜 214 条把影视站 90 条压到只剩 25 条露头），
而失败站点又被 ``except: continue`` 静默吞掉，用户完全无法判断问题在哪。
"""

import asyncio

from app.providers.base import Resource
from app.services.search import SiteOutcome, apply_site_quota


def _res(site: str, index: int) -> Resource:
    """造一条可区分来源的资源。"""
    return Resource(title=f"{site}-{index}", link=f"magnet:?xt=urn:btih:{site}{index:04d}", site=site)


def _pair(site: str, count: int) -> tuple[list[Resource], SiteOutcome]:
    return [_res(site, i) for i in range(count)], SiteOutcome(site=site, raw=count)


class TestApplySiteQuota:
    def test_小站不再被大站整体挤出(self):
        """核心回归：大站返回 200 条、小站 5 条，小站也必须排在最前面。"""
        merged = apply_site_quota([_pair("大站", 200), _pair("小站", 5)], 0)
        # 取前 10 条，小站应当占到约一半（轮转交错）
        head = [r.site for r in merged[:10]]
        assert head.count("小站") == 5, head
        assert head.count("大站") == 5, head

    def test_不砍量_总数完整保留(self):
        """公平性靠交错，不靠砍量：quota=0 时一条都不能少。"""
        merged = apply_site_quota([_pair("A", 214), _pair("B", 90)], 0)
        assert len(merged) == 304

    def test_轮转交错顺序(self):
        merged = apply_site_quota([_pair("A", 3), _pair("B", 2)], 0)
        assert [r.site for r in merged] == ["A", "B", "A", "B", "A"]

    def test_安全阀生效(self):
        merged = apply_site_quota([_pair("A", 500), _pair("B", 10)], 100)
        assert sum(1 for r in merged if r.site == "A") == 100
        assert sum(1 for r in merged if r.site == "B") == 10

    def test_安全阀回写_kept(self):
        pairs = [_pair("A", 500)]
        apply_site_quota(pairs, 100)
        assert pairs[0][1].raw == 500
        assert pairs[0][1].kept == 100

    def test_空站点不参与交错(self):
        merged = apply_site_quota([_pair("A", 2), _pair("空", 0)], 0)
        assert [r.site for r in merged] == ["A", "A"]

    def test_全空返回空列表(self):
        assert apply_site_quota([_pair("A", 0)], 0) == []

    def test_无站点返回空列表(self):
        assert apply_site_quota([], 0) == []


class TestSiteOutcome:
    def test_默认状态为ok(self):
        assert SiteOutcome(site="X").status == "ok"

    def test_可序列化给前端(self):
        data = SiteOutcome(site="X", status="timeout", message="超时").to_dict()
        assert data["site"] == "X"
        assert data["status"] == "timeout"
        assert data["message"] == "超时"


class TestSearchDetailed:
    """``search_detailed`` 必须把每个站点的成败都报出来。"""

    def test_区分空结果与异常(self, client):
        from app.providers.base import SearchProvider
        from app.services import search as search_service

        class 空站(SearchProvider):
            name = "t_empty"

            @property
            def site_name(self) -> str:
                return "空站"

            async def search(self, keyword, **kwargs):
                return []

        class 炸站(SearchProvider):
            name = "t_boom"

            @property
            def site_name(self) -> str:
                return "炸站"

            async def search(self, keyword, **kwargs):
                raise RuntimeError("连接被拒绝")

        class 好站(SearchProvider):
            name = "t_ok"

            @property
            def site_name(self) -> str:
                return "好站"

            async def search(self, keyword, **kwargs):
                # 标题必须含关键词，否则会被标题相关性过滤剔除（那是另一层的正确行为）
                return [Resource(title="测试关键词 1080p", link="magnet:?xt=urn:btih:ok01", site="好站")]

        results, outcomes = asyncio.run(
            search_service.search_detailed(
                "测试关键词",
                providers=[空站({}), 炸站({}), 好站({})],
                save_history=False,
            )
        )
        by_site = {o.site: o for o in outcomes}
        assert by_site["空站"].status == "empty"
        # 异常不能被当成"没有结果"：必须留下可诊断的原因
        assert by_site["炸站"].status == "error"
        assert "连接被拒绝" in by_site["炸站"].message
        assert by_site["好站"].status == "ok"
        assert by_site["好站"].raw == 1
        assert len(results) == 1

    def test_无关键词时返回空的二元组(self, client):
        from app.services import search as search_service

        results, outcomes = asyncio.run(
            search_service.search_detailed("   ", save_history=False)
        )
        assert results == []
        assert outcomes == []

    def test_无可用站点时返回空的二元组(self, client):
        from app.services import search as search_service

        results, outcomes = asyncio.run(
            search_service.search_detailed("测试", providers=[], save_history=False)
        )
        assert results == []
        assert outcomes == []

    def test_search_保持返回列表(self, client):
        """老调用点（订阅/洗版/ChatOps）依赖 search() 返回列表，不能破坏。"""
        from app.services import search as search_service

        results = asyncio.run(
            search_service.search("测试", providers=[], save_history=False)
        )
        assert isinstance(results, list)
