"""Jackett/Prowlarr 批量接入 + 流式搜索（v1.17.0）。

全程离线：Jackett 用 monkeypatch 桩，流式搜索用假 Provider。

这两件事的共同背景是「站点多了以后体验崩掉」：
- 站点一多，`asyncio.gather` 要等最慢的站，整页等待 = 最慢站耗时；
- 站点一多，手工拼 `indexers/<id>/results/torznab` 地址完全不可行。
"""

from __future__ import annotations

import asyncio

from app.providers.base import Resource, SearchProvider
from app.services import jackett
from app.services.search import search_stream


# ---------------- Jackett 地址拼接 ----------------
class TestTorznabUrl:
    def test_基础拼接(self):
        assert jackett.torznab_url("http://127.0.0.1:9117", "1337x") == (
            "http://127.0.0.1:9117/api/v2.0/indexers/1337x/results/torznab"
        )

    def test_补协议与去尾斜杠(self):
        """用户常漏协议或多带斜杠，都要能兜住。"""
        assert jackett.torznab_url("127.0.0.1:9117/", "abc") == (
            "http://127.0.0.1:9117/api/v2.0/indexers/abc/results/torznab"
        )

    def test_落库地址不带_api(self):
        """**关键回归**：落库地址必须不含结尾 /api。

        ``TorznabIndexer._endpoint()`` 会自己补 ``/api``。如果这里也带上，
        实际请求就变成 ``/torznab/api/api`` → 404，站点看着配好了却永远 0 条
        （本轮实测踩到的真坑）。
        """
        assert not jackett.torznab_url("http://h:9117", "x").endswith("/api")

    def test_caps_地址带_api(self):
        """直接请求 Torznab 时必须带 /api，否则真实 Jackett 上 404。"""
        assert jackett.caps_url("http://h:9117", "x").endswith("/results/torznab/api")


# ---------------- 索引器清单 ----------------
INDEXERS = [
    {
        "id": "1337x",
        "name": "1337x",
        "configured": True,
        "type": "public",
        "site_link": "https://1337x.to",
        "caps": [{"Name": "Movies"}, {"Name": "TV"}, {"Name": "Movies/HD"}],
    },
    {"id": "off", "name": "Not Configured", "configured": False, "caps": []},
]


def _patch_json(monkeypatch, payload):
    async def fake(url, **kwargs):
        return payload

    monkeypatch.setattr(jackett, "fetch_json", fake)


class TestListIndexers:
    def test_正常列出并拼好地址(self, monkeypatch):
        _patch_json(monkeypatch, INDEXERS)
        result = asyncio.run(jackett.list_indexers("http://h:9117", "k"))
        assert result["ok"] is True
        assert [i["id"] for i in result["items"]] == ["1337x"], "未配置的索引器应被排除"
        assert result["items"][0]["torznab_url"].endswith("/1337x/results/torznab")

    def test_只保留顶层分类(self, monkeypatch):
        """子类（Movies/HD）太碎，展示价值低。"""
        _patch_json(monkeypatch, INDEXERS)
        result = asyncio.run(jackett.list_indexers("http://h:9117", "k"))
        assert result["items"][0]["categories"] == ["Movies", "TV"]

    def test_缺少地址(self):
        result = asyncio.run(jackett.list_indexers("", "k"))
        assert result["ok"] is False and "地址" in result["message"]

    def test_缺少_api_key(self):
        result = asyncio.run(jackett.list_indexers("http://h:9117", ""))
        assert result["ok"] is False and "API Key" in result["message"]

    def test_连不上要提示_docker_网络(self, monkeypatch):
        """连不上是最常见的失败，且 Docker 里填 127.0.0.1 是头号原因。"""
        _patch_json(monkeypatch, None)
        result = asyncio.run(jackett.list_indexers("http://h:9117", "k"))
        assert result["ok"] is False
        assert "Docker" in result["message"]

    def test_key_错误时不能笼统报失败(self, monkeypatch):
        """Jackett 对错 key 返回 JSON 对象而非数组，要说清是 Key 的问题。"""
        _patch_json(monkeypatch, {"error": "Invalid API Key"})
        result = asyncio.run(jackett.list_indexers("http://h:9117", "bad"))
        assert result["ok"] is False
        assert "API Key" in result["message"]

    def test_一个站都没配时给出下一步(self, monkeypatch):
        """连通但空列表：要告诉用户去 Jackett 里 Add Indexer，而不是报错了事。"""
        _patch_json(monkeypatch, [])
        result = asyncio.run(jackett.list_indexers("http://h:9117", "k"))
        assert result["ok"] is False
        assert "Add Indexer" in result["message"]

    def test_字段缺失时默认当作已配置(self, monkeypatch):
        """Prowlarr 不返回 configured 字段，不能把用户真实的站悄悄藏掉。"""
        _patch_json(monkeypatch, [{"id": "p1", "name": "P1"}])
        result = asyncio.run(jackett.list_indexers("http://h:9117", "k"))
        assert [i["id"] for i in result["items"]] == ["p1"]


class TestBuildSitePayload:
    def test_生成可落库的站点配置(self):
        data = jackett.build_site_payload(
            "http://h:9117", "k", {"id": "1337x", "name": "1337x"}
        )
        assert data["provider"] == "torznab"
        assert data["name"] == "Jackett · 1337x"
        assert data["api_key"] == "k"
        assert data["options"]["jackett_indexer_id"] == "1337x"

    def test_站点名带前缀避免撞已有站点(self):
        """Jackett 里的站名可能和用户手工加的重名，会撞 unique 约束。"""
        data = jackett.build_site_payload(
            "http://h:9117", "k", {"id": "x", "name": "Nyaa"}, name_prefix="JK"
        )
        assert data["name"] == "JK · Nyaa"

    def test_默认启用(self):
        """从 Jackett 导入说明那些站在 Jackett 侧已经能用，不该再要求逐个启用。"""
        data = jackett.build_site_payload("http://h:9117", "k", {"id": "x", "name": "X"})
        assert data["enabled"] is True


# ---------------- 流式搜索 ----------------
class _FakeProvider(SearchProvider):
    """按指定延迟返回固定条数的假站点。"""

    name = "fake"
    kind = "indexer"

    def __init__(self, site: str, count: int, delay: float = 0.0):
        super().__init__({"name": site})
        self._site = site
        self._count = count
        self._delay = delay

    @property
    def site_name(self) -> str:
        return self._site

    async def search(self, keyword, *, media_type=None, season=None, episode=None, page=0):
        if self._delay:
            await asyncio.sleep(self._delay)
        return [
            Resource(
                title=f"{self._site} {keyword} S01E0{i} 1080p",
                link=f"magnet:?xt=urn:btih:{self._site}{i:037d}",
                site=self._site,
            )
            for i in range(self._count)
        ]


def _collect(providers, keyword="测试片"):
    async def run():
        events = []
        async for event in search_stream(keyword, providers=providers, save_history=False):
            events.append(event)
        return events

    return asyncio.run(run())


class TestSearchStream:
    def test_事件顺序_start_site_done(self):
        events = _collect([_FakeProvider("A", 2)])
        assert events[0]["type"] == "start"
        assert events[-1]["type"] == "done"
        assert [e["type"] for e in events].count("site") == 1

    def test_start_先告知要查哪些站(self):
        """前端要靠它先画出「正在查 N 个站」，否则只能干看转圈。"""
        events = _collect([_FakeProvider("A", 1), _FakeProvider("B", 1)])
        assert events[0]["total_sites"] == 2
        assert set(events[0]["sites"]) == {"A", "B"}

    def test_每站单独成批下发(self):
        """核心价值：快站不必等慢站。"""
        events = _collect([_FakeProvider("快", 2), _FakeProvider("慢", 2, delay=0.25)])
        site_events = [e for e in events if e["type"] == "site"]
        assert len(site_events) == 2
        assert site_events[0]["site"]["site"] == "快", "先返回的站必须先下发"

    def test_快站结果不被慢站阻塞(self):
        """用时间证明：快站的事件必须在慢站完成之前就到达。"""

        async def run():
            arrived = []
            loop = asyncio.get_event_loop()
            start = loop.time()
            async for event in search_stream(
                "片", providers=[_FakeProvider("快", 1), _FakeProvider("慢", 1, delay=0.5)],
                save_history=False,
            ):
                if event["type"] == "site":
                    arrived.append((event["site"]["site"], loop.time() - start))
            return arrived

        arrived = asyncio.run(run())
        fast = next(t for name, t in arrived if name == "快")
        slow = next(t for name, t in arrived if name == "慢")
        assert fast < 0.3, f"快站等了 {fast:.2f}s，说明被慢站阻塞了"
        assert slow >= 0.4

    def test_累计计数随进度增长(self):
        events = [e for e in _collect([_FakeProvider("A", 3), _FakeProvider("B", 2)])
                  if e["type"] == "site"]
        totals = [e["running_total"] for e in events]
        assert totals == sorted(totals), "累计数不能倒退"
        assert totals[-1] == 5

    def test_进度字段可用于进度条(self):
        events = [e for e in _collect([_FakeProvider("A", 1), _FakeProvider("B", 1)])
                  if e["type"] == "site"]
        assert [e["received"] for e in events] == [1, 2]
        assert all(e["total_sites"] == 2 for e in events)

    def test_跨站去重(self):
        """两个站给出同一个磁力，只能算一条。"""

        class Dup(_FakeProvider):
            async def search(self, keyword, **kwargs):
                # 标题必须含关键词，否则会先被「标题相关性过滤」剔掉，
                # 那就测不到去重了（第一次就是这么写错的）
                return [
                    Resource(
                        title=f"{keyword} 1080p WEB-DL",
                        link="magnet:?xt=urn:btih:" + "d" * 40,
                        site=self._site,
                    )
                ]

        events = _collect([Dup("A", 1), Dup("B", 1)])
        done = events[-1]
        assert done["total"] == 1, "重复资源没有被去掉"

    def test_没有站点时也要给_done(self):
        """前端等的是 done 事件；不给它就会一直显示「搜索中」。"""
        events = _collect([])
        assert len(events) == 1
        assert events[0]["type"] == "done" and events[0]["total"] == 0

    def test_单站异常不影响其它站(self):
        class Boom(_FakeProvider):
            async def search(self, keyword, **kwargs):
                raise RuntimeError("站点炸了")

        events = _collect([Boom("坏站", 0), _FakeProvider("好站", 2)])
        done = events[-1]
        assert done["total"] == 2
        statuses = {s["site"]: s["status"] for s in done["sites"]}
        assert statuses["坏站"] == "error"
        assert statuses["好站"] == "ok"

    def test_任务本身抛异常也要有名有姓(self):
        """``as_completed`` 的兜底 except 分支。

        ``_search_one`` 内部已经捕获了 Provider 的异常，所以这条兜底平时跑不到
        —— 注入缺陷验证发现把它改窄成 ``except ZeroDivisionError`` 测试**不会转红**，
        属于没有覆盖的防御代码。这里直接让待办任务抛异常来覆盖它：
        如果这条分支失效，整个流会中断，用户拿不到任何结果也看不到原因（ADR-20）。
        """

        async def run():
            async def boom():
                raise RuntimeError("任务级异常")

            # 手工构造一个必然抛异常的任务，绕过 _search_one 的内部捕获
            import app.services.search as module

            original = module._search_one
            module._search_one = lambda *a, **k: boom()
            try:
                events = []
                async for event in search_stream(
                    "片名", providers=[_FakeProvider("A", 1)], save_history=False
                ):
                    events.append(event)
                return events
            finally:
                module._search_one = original

        events = asyncio.run(run())
        assert events[-1]["type"] == "done", "异常把整条流打断了"
        sites = events[-1]["sites"]
        assert len(sites) == 1
        assert sites[0]["status"] == "error"
        assert "RuntimeError" in sites[0]["message"]

    def test_诊断在_done_里齐全(self):
        """站点诊断不能因为流式就丢掉（ADR-20：结果变少必须有据可查）。"""
        events = _collect([_FakeProvider("A", 1), _FakeProvider("B", 0)])
        done = events[-1]
        assert len(done["sites"]) == 2
        assert {s["status"] for s in done["sites"]} == {"ok", "empty"}


class TestSearchStreamApi:
    def test_流式接口返回_ndjson(self, client, auth_headers):
        """端到端：确认 content-type 与逐行 JSON 结构。"""
        import json

        with client.stream(
            "POST", "/api/v1/search/stream",
            headers=auth_headers, json={"keyword": "不存在的片名zzzq"},
        ) as response:
            assert response.status_code == 200
            assert "ndjson" in response.headers["content-type"]
            events = [json.loads(line) for line in response.iter_lines() if line.strip()]

        assert events, "流里一个事件都没有"
        assert events[-1]["type"] == "done"

    def test_关闭反代缓冲(self, client, auth_headers):
        """Nginx 默认会攒够一块才转发，流式在用户那边会退化成一次性出结果。"""
        with client.stream(
            "POST", "/api/v1/search/stream",
            headers=auth_headers, json={"keyword": "x"},
        ) as response:
            assert response.headers.get("x-accel-buffering") == "no"

    def test_需要登录(self, client):
        response = client.post("/api/v1/search/stream", json={"keyword": "x"})
        assert response.status_code in (401, 403)
