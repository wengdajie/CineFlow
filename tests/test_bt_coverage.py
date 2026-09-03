"""BT 覆盖面扩容与**跨语言别名**回归用例（v1.20.0）。

本文件守的是用户反馈「大部分内容无法返回 BT 资源」背后的**两个**原因，
其中第二个是纯静默缺陷（加站点也解决不了）：

## ① 装完只有 1 个 BT 源默认启用

v1.19.0 的 `DEFAULT_SITES` 里 8 个 indexer 只有「磁力熊」默认启用（且只收
电影/剧集），覆盖面最广的 `torznab` **必须先自建 Jackett**。
对「装完就想用」的用户等于零 BT 覆盖。
v1.20.0 补两个**公开免鉴权、零配置**的源并默认启用。

## ② 搜到了也会被自己丢掉（真缺陷，本轮的核心）

实测：`凡人修仙传` 在 AnimeTosho 取回 **74 条**真实种子，
经 `filter_and_rank()` 后剩 **0 条**。原因是这些种子叫
`A Record of a Mortal's Journey to Immortality`，而
`_title_variants("凡人修仙传")` 只产出 `['凡人修仙传']`，
每条都被判「标题与订阅名称不匹配」。

**这比「站点少」更要紧**：不修它，新加的国际站会被一样清空，
用户看到的仍然是「搜不到」——加站点的钱全白花。
"""

from __future__ import annotations

import pytest

from app.core.filters import FilterRule, filter_and_rank
from app.providers.indexer.animetosho import AnimeToshoIndexer
from app.providers.indexer.torrents_csv import (
    PAGE_SIZE,
    TorrentsCsvIndexer,
    build_magnet,
)
from app.providers.registry import get_provider_class
from app.schemas.enums import ProviderKind, ResourceKind
from app.services import site_catalog, title_alias


@pytest.fixture(scope="module", autouse=True)
def _providers_loaded():
    """确保内置 Provider 全部注册。

    直接 import 某个 Provider 模块只会注册它自己；``html_generic`` 之类
    要靠 ``load_builtin_providers()`` 才会进注册表。不做这一步，
    「预设指向的 provider 是否已注册」这条断言会因为**加载顺序**而假红。
    """
    from app.providers.registry import load_builtin_providers

    load_builtin_providers()

# ---------------- 1. 别名过滤：本轮的核心缺陷 ----------------


class TestAliasTitleFiltering:
    """别名必须参与标题匹配，否则国际站命中的英文标题会被整站丢掉。"""

    #: 真实取回的标题（AnimeTosho 对 q=凡人修仙传 的实际返回）
    REAL_TITLES = [
        "[FSP DN] A Record of a Mortal's Journey to Immortality - 176 (1080p)",
        "[GM-Team][国漫][凡人修仙传 慕兰之战][2026][08][AVC][GB][1080P]",
    ]

    def _payload(self, titles: list[str]) -> list[dict]:
        return [
            {
                "title": title,
                "link": f"magnet:?xt=urn:btih:{index:040d}",
                "kind": ResourceKind.MAGNET.value,
                "site": "AnimeTosho",
                "size": 10**9,
                "seeders": 50,
                "extra": {},
            }
            for index, title in enumerate(titles)
        ]

    def test_without_alias_the_english_title_is_dropped(self) -> None:
        """复现缺陷：只用中文名做白名单，英文标题的真种子被丢掉。

        这条用例**故意断言缺陷存在**，用来证明修复确实必要 ——
        若哪天 `title_match_variants` 在无别名时也放行英文，
        说明过滤变宽了（可能放进无关结果），这条会转红提醒复查。
        """
        rule = FilterRule()
        rule.title_keywords = title_alias.title_match_variants("凡人修仙传")
        kept = filter_and_rank(self._payload(self.REAL_TITLES), rule, None)
        titles = [item["title"] for item in kept]
        assert len(kept) == 1
        assert "GM-Team" in titles[0]

    def test_with_alias_both_are_kept(self) -> None:
        """修复后：别名进白名单，中英文标题的真种子都留得住。"""
        rule = FilterRule()
        rule.title_keywords = title_alias.title_match_variants(
            "凡人修仙传",
            aliases=["A Record of a Mortal's Journey to Immortality"],
        )
        kept = filter_and_rank(self._payload(self.REAL_TITLES), rule, None)
        assert len(kept) == 2, [item["title"] for item in kept]

    def test_alias_does_not_let_unrelated_titles_through(self) -> None:
        """别名放行的是**该作品**的译名，不能顺手把无关作品也放进来。

        没有这条，「扩大白名单」就可能退化成「关闭过滤」。
        """
        rule = FilterRule()
        rule.title_keywords = title_alias.title_match_variants(
            "凡人修仙传",
            aliases=["A Record of a Mortal's Journey to Immortality"],
        )
        unrelated = self._payload(["[Ironclad] Sousou no Frieren - S02 [WEB.1080p.AV1]"])
        assert filter_and_rank(unrelated, rule, None) == []

    def test_variants_are_regex_escaped(self) -> None:
        """别名里的正则元字符必须转义，否则 `match_keywords` 会把它当模式。"""
        variants = title_alias.title_match_variants("Dune", aliases=["Dune: Part Two (2024)"])
        assert any("\\(" in item or "\\:" in item for item in variants)

    def test_empty_title_yields_no_variants(self) -> None:
        assert title_alias.title_match_variants("") == []


class TestAliasUsefulness:
    """别名筛选：同语种的近似别名不该拿去搜（白多一次站点请求）。"""

    def test_same_language_near_duplicate_is_rejected(self) -> None:
        assert title_alias._is_useful("凡人修仙传 第二季", "凡人修仙传") is False

    def test_cross_language_alias_is_accepted(self) -> None:
        assert title_alias._is_useful("A Record of a Mortal's Journey", "凡人修仙传") is True

    def test_case_only_difference_is_rejected(self) -> None:
        assert title_alias._is_useful("dune", "Dune") is False

    def test_too_short_alias_is_rejected(self) -> None:
        """1~2 字符的缩写搜出来全是噪音。"""
        assert title_alias._is_useful("D", "Dune") is False

    def test_cjk_detection(self) -> None:
        assert title_alias.has_cjk("凡人修仙传") is True
        assert title_alias.has_cjk("Dune") is False
        assert title_alias.has_latin("Dune") is True


class TestAliasDegradesSafely:
    """没有 TMDB Key 时必须安全降级为空，而不是抛错让整个搜索失败。"""

    @pytest.fixture(autouse=True)
    def _clear(self):
        title_alias.clear_cache()
        yield
        title_alias.clear_cache()

    @pytest.mark.asyncio
    async def test_no_tmdb_key_returns_empty(self) -> None:
        """conftest 把 CF_TMDB_API_KEY 设为空，因此这里走的就是降级路径。"""
        assert await title_alias.aliases_for("凡人修仙传", media_type="tv") == []

    @pytest.mark.asyncio
    async def test_original_title_is_used_without_tmdb(self) -> None:
        """调用方已有 original_title 时，零请求也该产出别名。"""
        aliases = await title_alias.aliases_for(
            "凡人修仙传",
            media_type="tv",
            original_title="A Record of a Mortal's Journey to Immortality",
        )
        assert aliases == ["A Record of a Mortal's Journey to Immortality"]

    @pytest.mark.asyncio
    async def test_tmdb_failure_does_not_raise(self, monkeypatch) -> None:
        """TMDB 抽风只能降级，不能让搜索报错。"""
        from app.providers.metadata import tmdb as tmdb_module

        monkeypatch.setattr(tmdb_module.tmdb, "api_key", "fake-key")

        async def boom(*args, **kwargs):
            raise RuntimeError("tmdb down")

        monkeypatch.setattr(tmdb_module.tmdb, "recognize", boom)
        assert await title_alias.aliases_for("沙丘", media_type="movie") == []

    @pytest.mark.asyncio
    async def test_result_is_cached(self, monkeypatch) -> None:
        """订阅巡检每 30 分钟一次，别名却几乎不变——必须缓存。"""
        calls = {"n": 0}

        original = title_alias._is_useful

        def counting(alias: str, base: str) -> bool:
            calls["n"] += 1
            return original(alias, base)

        monkeypatch.setattr(title_alias, "_is_useful", counting)
        await title_alias.aliases_for("三体", media_type="tv", original_title="Three-Body")
        first = calls["n"]
        await title_alias.aliases_for("三体", media_type="tv", original_title="Three-Body")
        assert calls["n"] == first, "第二次调用应命中缓存，不再重新计算"

    @pytest.mark.asyncio
    async def test_limit_is_respected(self) -> None:
        """别名太多会让一次巡检变成几十次站点请求。"""
        aliases = await title_alias.aliases_for(
            "沙丘", media_type="movie", original_title="Dune", limit=1
        )
        assert len(aliases) <= 1


# ---------------- 2. torrents-csv Provider ----------------


class TestTorrentsCsvProvider:
    def test_registered(self) -> None:
        assert get_provider_class("torrents_csv") is TorrentsCsvIndexer
        assert TorrentsCsvIndexer.kind == ProviderKind.INDEXER.value

    def test_magnet_carries_name_and_trackers(self) -> None:
        """裸磁力（无 dn / 无 tr）会让标题退化且起步慢，两者都必须带。"""
        magnet = build_magnet("2770fe270845674966e184be60ed1be0fe494f3a", "Dune Part Two")
        assert magnet.startswith("magnet:?xt=urn:btih:2770fe270845674966e184be60ed1be0fe494f3a")
        assert "dn=" in magnet
        assert magnet.count("tr=") >= 2

    def test_infohash_is_lowercased(self) -> None:
        magnet = build_magnet("ABCDEF0123456789ABCDEF0123456789ABCDEF01", "x")
        assert "btih:abcdef0123456789abcdef0123456789abcdef01" in magnet

    def _provider(self) -> TorrentsCsvIndexer:
        return TorrentsCsvIndexer({"name": "Torrents-CSV", "priority": 18})

    def test_parse_real_payload(self) -> None:
        """用真实响应结构解析，字段必须逐个对上。"""
        payload = {
            "torrents": [
                {
                    "infohash": "2770fe270845674966e184be60ed1be0fe494f3a",
                    "name": "Dune Part Two (2024) [1080p] [WEBRip]",
                    "size_bytes": 2968337547,
                    "created_unix": 1712378383,
                    "seeders": 205,
                    "leechers": 209,
                    "completed": 25404,
                }
            ],
            "next": 40211,
        }
        parsed = self._provider()._parse(payload)
        assert len(parsed) == 1
        resource, _ = parsed[0]
        assert resource.kind == ResourceKind.MAGNET.value
        assert resource.size == 2968337547
        assert resource.seeders == 205
        assert resource.leechers == 209
        assert resource.grabs == 25404
        assert resource.publish_at is not None
        assert resource.extra["infohash"] == "2770fe270845674966e184be60ed1be0fe494f3a"
        assert "download" in resource.actions

    def test_entries_without_valid_infohash_are_dropped(self) -> None:
        """留一条点了没反应的结果，比不给这条更糟。"""
        payload = {
            "torrents": [
                {"infohash": "", "name": "no hash"},
                {"infohash": "tooshort", "name": "bad hash"},
                {"infohash": "a" * 40, "name": ""},
                {"infohash": "b" * 40, "name": "good"},
            ]
        }
        parsed = self._provider()._parse(payload)
        assert [resource.title for resource, _ in parsed] == ["good"]

    def test_page_size_matches_server_clamp(self) -> None:
        """服务端把 size 钳在 25；写错会让翻页数算错。"""
        assert PAGE_SIZE == 25

    def test_fetch_latest_is_honest_about_missing_endpoint(self) -> None:
        """接口没有「最新」端点，如实返回空而不是伪造一批。"""
        import asyncio

        assert asyncio.run(self._provider().fetch_latest()) == []


class TestAnimeToshoProvider:
    def test_registered(self) -> None:
        assert get_provider_class("animetosho") is AnimeToshoIndexer

    def _provider(self) -> AnimeToshoIndexer:
        return AnimeToshoIndexer({"name": "AnimeTosho", "priority": 22})

    def test_prefers_torrent_url_over_magnet(self) -> None:
        """.torrent 直链不依赖 DHT，拿到即可开始下载。"""
        payload = [
            {
                "title": "[9volt] Sousou no Frieren - S02",
                "torrent_url": "https://storage.animetosho.org/torrent/abc/x.torrent",
                "info_hash": "a" * 40,
                "status": "complete",
                "timestamp": 1778215728,
                "total_size": 6770000000,
                "link": "https://animetosho.org/view/x",
            }
        ]
        parsed = self._provider()._parse(payload)
        assert len(parsed) == 1
        assert parsed[0].kind == ResourceKind.TORRENT.value
        assert parsed[0].link.endswith(".torrent")
        assert parsed[0].page_url == "https://animetosho.org/view/x"

    def test_falls_back_to_magnet_when_no_torrent_url(self) -> None:
        payload = [{"title": "x", "info_hash": "b" * 40, "status": "complete"}]
        parsed = self._provider()._parse(payload)
        assert parsed[0].kind == ResourceKind.MAGNET.value
        assert parsed[0].link.startswith("magnet:")

    def test_incomplete_entries_are_skipped(self) -> None:
        """status != complete 时 torrent_url 可能还没落地。"""
        payload = [
            {"title": "processing", "info_hash": "c" * 40, "status": "processing"},
            {"title": "done", "info_hash": "d" * 40, "status": "complete"},
        ]
        parsed = self._provider()._parse(payload)
        assert [item.title for item in parsed] == ["done"]

    def test_local_filter_defaults_off(self) -> None:
        """中文关键词只能匹配罗马音标题，按字面过滤会全误杀 —— 故默认关。"""
        assert self._provider().option("local_filter", False) is False


# ---------------- 3. 清单与默认启用 ----------------


class TestCatalogGrowth:
    def test_new_bt_presets_present_with_evidence(self) -> None:
        """收录标准是「当场搜出真实资源」，凭据写在 measured 里。"""
        ids = {preset["id"] for preset in site_catalog.list_bt_presets()}
        assert {"torrents_csv", "animetosho"} <= ids
        for preset_id in ("torrents_csv", "animetosho"):
            preset = site_catalog.get_bt_preset(preset_id)
            assert preset is not None
            assert preset["measured"], f"{preset_id} 缺实测凭据"
            assert preset["caveat"], f"{preset_id} 必须如实写明能力边界"
            assert preset["verified"] is True

    def test_presets_reference_registered_providers(self) -> None:
        """预设指向没注册的 provider 会让「一键添加」建出永远不工作的站点。"""
        for preset in site_catalog.list_bt_presets():
            assert get_provider_class(preset["provider"]) is not None, preset["id"]

    def test_payload_carries_measured_into_note(self) -> None:
        """实测数据与缺陷要出现在站点详情，用户才看得到能力边界。"""
        preset = site_catalog.get_bt_preset("animetosho")
        note = site_catalog.site_payload(preset)["options"]["note"]
        assert "实测" in note
        assert "动漫" in note

    def test_new_rss_presets_have_known_dialects(self) -> None:
        from app.core.rss_dialects import DIALECTS

        ids = {preset["id"] for preset in site_catalog.list_rss_presets()}
        assert {"miobt", "comicat", "animetosho_rss"} <= ids
        for preset in site_catalog.list_rss_presets(include_adult=True):
            assert preset["dialect"] in DIALECTS, preset["id"]

    def test_default_enabled_bt_sources_need_no_configuration(self) -> None:
        """默认启用的前提是**不需要用户填任何东西**。

        若哪天有人把需要 api_key/cookie 的站设成默认启用，
        用户一进来就会看到一片搜索报错 —— 这条把该前提钉住。
        """
        from app.db.init_db import DEFAULT_SITES

        enabled_indexers = [
            site
            for site in DEFAULT_SITES
            if site["kind"] == ProviderKind.INDEXER.value and site.get("enabled")
        ]
        names = {site["name"] for site in enabled_indexers}
        assert {"Torrents-CSV 公开搜索", "AnimeTosho 动漫"} <= names
        for site in enabled_indexers:
            assert not site.get("api_key"), site["name"]
            assert not site.get("cookie"), site["name"]
            assert not site.get("username"), site["name"]
            assert site["url"].startswith("http"), site["name"]

    def test_default_enabled_sites_have_notes(self) -> None:
        """默认启用的站点必须写明它是什么、边界在哪。"""
        from app.db.init_db import DEFAULT_SITES

        for site in DEFAULT_SITES:
            if site["kind"] == ProviderKind.INDEXER.value and site.get("enabled"):
                assert (site.get("options") or {}).get("note"), site["name"]
