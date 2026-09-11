"""v1.28.0：公开聚合磁力 JSON API 源（参考 magnet-finder 的选型）。

背景：BT4G 被 Cloudflare 人机验证拦截（v1.27.0 已默认禁用），Bitsearch
是唯一幸存者 —— 单点故障等于 BT 搜索整体归零。magnet-finder 的答案是
**不走 HTML 站，改走不挂 Cloudflare 的公开 JSON API**，且全部带真实做种数。

本模块三个源（2026-09-11 实测）：

| 源 | 接口 | 实测 |
|---|---|---|
| apibay（TPB 官方 API） | GET /q.php?q=&cat=0 | 「dune 2024」100 条，最高 947 做种 |
| therarbg | GET /get-posts/keywords:?format=json | 50 条/页，有 next 分页 |
| knaben（聚合几十站） | POST api.knaben.org/v1 | 「流浪地球」7 条，首条即目标 |

EZTV（只能按 imdb_id 查，不支持关键词）与 rargb（无 API，需逐条进详情页）
按 YAGNI 不做。
"""

from __future__ import annotations

import asyncio

import pytest

from app.providers.indexer import magnet_apis
from app.providers.indexer.magnet_apis import (
    KnabenIndexer,
    TherarbgIndexer,
    TpbIndexer,
)
from app.providers.registry import get_provider_class, load_builtin_providers
from app.utils.http import FetchError

#: 照抄 2026-09-11 实测响应结构（字段名/类型都不能自己简化：
#: apibay 的数字全是**字符串**，少转一次类型做种数就全错）。
APIBay_ROW = {
    "id": "75005578",
    "name": "Dune Part Two (2024) [1080p] [WEBRip]",
    "info_hash": "2770FE270845674966E184BE60ED1BE0FE494F3A",
    "leechers": "244",
    "seeders": "947",
    "size": "2968337547",
    "num_files": "12",
    "username": "Anonymous",
    "added": "1712378383",
    "status": "vip",
    "category": "207",
    "imdb": "tt15239678",
}

THERARBG_ROW = {
    "n": "Dune Part Two 2024 1080p AMZN WEB-DL DDP 5 1 H 264-OnlyWeb",
    "h": "C69467D71EF8E6B4AC2BE1A82941DF90A757D302",
    "se": 36,
    "le": 4,
    "s": 6456112869,
    "a": 1776777287,
    "i": "tt15239678",
    "pk": "8cf8de",
}

KNABEN_HIT = {
    "title": "[DBD-Raws][流浪地球/The.Wandering.Earth.][1080P][BDRip]",
    "hash": "2770FE270845674966E184BE60ED1BE0FE494F3A",
    "seeders": 9,
    "peers": 3,
    "bytes": 2963527434,
    "date": "2024-04-05T22:00:00+00:00",
    "tracker": "The Pirate Bay",
    "details": "https://knaben.org/detail/x",
    "magnetUrl": "magnet:?xt=urn:btih:2770fe270845674966e184be60ed1be0fe494f3a&tr=udp%3A%2F%2Ftracker.example%3A1337",
}


class TestApibay解析:
    def test_占位记录必须过滤(self) -> None:
        """apibay 没结果时返回一条 id=0 的占位记录，不过滤就会出一条假资源。"""
        payload = [{"id": "0", "name": "No results returned", "info_hash": "0" * 40, "seeders": "0", "size": "0"}]
        assert TpbIndexer({"name": "t"})._parse(payload) == []

    def test_全零infohash必须过滤(self) -> None:
        payload = [dict(APIBay_ROW, info_hash="0" * 40)]
        assert TpbIndexer({"name": "t"})._parse(payload) == []

    def test_字符串数字必须转成int(self) -> None:
        resources = TpbIndexer({"name": "t"})._parse([APIBay_ROW])
        assert len(resources) == 1
        r = resources[0]
        assert r.seeders == 947
        assert r.leechers == 244
        assert r.size == 2968337547
        assert r.link.startswith("magnet:?xt=urn:btih:2770fe270845674966e184be60ed1be0fe494f3a")
        assert r.extra["infohash"] == "2770fe270845674966e184be60ed1be0fe494f3a"
        assert r.extra["imdb"] == "tt15239678"
        assert r.publish_at is not None and r.publish_at.year == 2024
        assert r.page_url == "https://thepiratebay.org/description.php?id=75005578"

    def test_非列表响应当成报错而不是空结果(self) -> None:
        """限流/改版时 apibay 可能返回 dict，按 ADR-82 必须抛而不是静默归零。"""
        with pytest.raises(FetchError):
            TpbIndexer({"name": "t"})._parse({"error": "slow down"})


class TestTherarbg解析:
    def test_字段映射(self) -> None:
        resources = TherarbgIndexer({"name": "t"})._parse_rows([THERARBG_ROW])
        assert len(resources) == 1
        r = resources[0]
        assert r.seeders == 36
        assert r.size == 6456112869
        assert r.extra["infohash"] == "c69467d71ef8e6b4ac2be1a82941df90a757d302"
        assert r.page_url == "https://therarbg.com/post-detail/8cf8de/"

    def test_缺hash的行直接丢弃(self) -> None:
        assert TherarbgIndexer({"name": "t"})._parse_rows([dict(THERARBG_ROW, h="")]) == []

    def test_搜索地址空格必须是百分号20(self) -> None:
        """therarbg 多词查询用 + 会返回 0 条（magnet-finder 实测坑）。"""
        url = TherarbgIndexer({"name": "t"})._search_url("dune 2024")
        assert "dune%202024" in url
        assert "+" not in url.split("keywords:")[-1]


class TestKnaben解析:
    def test_优先用站点给的magnetUrl(self) -> None:
        """knaben 的 magnetUrl 自带 tracker 列表，比自己拼的裸磁力更容易连上 peer。"""
        resources = KnabenIndexer({"name": "k"})._parse({"hits": [KNABEN_HIT]})
        assert len(resources) == 1
        assert resources[0].link == KNABEN_HIT["magnetUrl"]
        assert resources[0].extra["tracker"] == "The Pirate Bay"

    def test_没有magnetUrl时退化为hash拼磁力(self) -> None:
        hit = {k: v for k, v in KNABEN_HIT.items() if k != "magnetUrl"}
        resources = KnabenIndexer({"name": "k"})._parse({"hits": [hit]})
        assert resources[0].link.startswith("magnet:?xt=urn:btih:2770fe")

    def test_空标题丢弃(self) -> None:
        assert KnabenIndexer({"name": "k"})._parse({"hits": [dict(KNABEN_HIT, title="")]}) == []


class Test搜索与故障语义:
    def test_空关键词直接返回空(self) -> None:
        assert asyncio.run(TpbIndexer({"name": "t"}).search("")) == []
        assert asyncio.run(KnabenIndexer({"name": "k"}).search("  ")) == []

    def test_接口坏了必须抛FetchError(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """坏了/没有必须分开（ADR-82）：返回 None 会被上层当成「这片没资源」。"""
        async def fake(*args: object, **kwargs: object) -> str:
            raise FetchError("HTTP 502", status=502)

        monkeypatch.setattr(magnet_apis, "fetch_text_result", fake)
        with pytest.raises(FetchError):
            asyncio.run(TpbIndexer({"name": "t"}).search("dune"))

    def test_返回不是JSON必须抛(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake(*args: object, **kwargs: object) -> str:
            return "<html>Attention Required! | Cloudflare</html>"

        monkeypatch.setattr(magnet_apis, "fetch_text_result", fake)
        with pytest.raises(FetchError) as caught:
            asyncio.run(KnabenIndexer({"name": "k"}).search("dune"))
        assert "JSON" in str(caught.value)


class Test健康检查必须返回而不是抛出:
    def test_失败返回False(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake(*args: object, **kwargs: object) -> str:
            raise FetchError("HTTP 502", status=502)

        monkeypatch.setattr(magnet_apis, "fetch_text_result", fake)
        for cls in (TpbIndexer, TherarbgIndexer, KnabenIndexer):
            ok, message = asyncio.run(cls({"name": "x"}).health_check())
            assert ok is False
            assert message

    def test_正常返回True(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import json

        async def fake(url: str, **kwargs: object) -> str:
            if "knaben" in url:
                return json.dumps({"hits": [KNABEN_HIT]})
            if "therarbg" in url:
                return json.dumps({"results": [THERARBG_ROW]})
            return json.dumps([APIBay_ROW])

        monkeypatch.setattr(magnet_apis, "fetch_text_result", fake)
        for cls in (TpbIndexer, TherarbgIndexer, KnabenIndexer):
            ok, message = asyncio.run(cls({"name": "x"}).health_check())
            assert ok is True, message


    def test_合法零结果也必须判健康(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """上游对某个关键词短时返回 0 条 ≠ 站点故障。

        实测 2026-09-11：apibay 对「1080p」返回占位记录、对「test」返回 100 条，
        接口本身完全正常。健康检查验的是**结构**：改版/被墙会拿到非 JSON，
        那时 ``_fetch_json`` 已抛 FetchError。
        """
        import json

        async def fake(url: str, **kwargs: object) -> str:
            if "knaben" in url:
                return json.dumps({"hits": []})
            if "therarbg" in url:
                return json.dumps({"results": [], "links": {}})
            # apibay 的占位记录：id=0 / 全零 hash
            return json.dumps([{"id": "0", "name": "No results returned",
                                "info_hash": "0" * 40, "seeders": "0", "size": "0"}])

        monkeypatch.setattr(magnet_apis, "fetch_text_result", fake)
        for cls in (TpbIndexer, TherarbgIndexer, KnabenIndexer):
            ok, message = asyncio.run(cls({"name": "x"}).health_check())
            assert ok is True, message

    def test_结构漂移必须判失败(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """反向担保：返回结构变了（list/dict 之外的形状）仍要报红。"""
        import json

        async def fake(url: str, **kwargs: object) -> str:
            return json.dumps({"unexpected": "shape"})

        monkeypatch.setattr(magnet_apis, "fetch_text_result", fake)
        for cls in (TpbIndexer, TherarbgIndexer, KnabenIndexer):
            ok, message = asyncio.run(cls({"name": "x"}).health_check())
            assert ok is False
            assert message


class Test注册与预设:
    def test_三个源都进注册表(self) -> None:
        load_builtin_providers()
        for name in ("tpb", "therarbg", "knaben"):
            assert get_provider_class(name) is not None

    def test_预设清单带实测凭据(self) -> None:
        from app.services import site_catalog

        presets = {p["id"]: p for p in site_catalog.list_bt_presets()}
        for pid in ("tpb", "therarbg", "knaben"):
            preset = presets[pid]
            assert preset["verified"] is True
            assert preset.get("measured"), "没有实测凭据的预设不许进清单（项目约定）"
            assert preset.get("caveat")
