"""v1.27.0 复盘：BT 覆盖面的站点侧失效、诊断口径与单点故障。

本轮四个真实缺陷（实测证据见 docs/08 v1.27.0）：

1. BT4G 全部 5 个镜像的 RSS 端点都上了 Cloudflare 人机验证（HTTP **200**
   + 一段 JS），项目错报成「不是合法 RSS（站点可能已改版）」——
   改版要改解析代码，人机验证改代码没有用，两者处置相反（ADR-82）。
2. ``health_check`` 里 ``_parse`` 抛异常会穿透成 HTTP 500，
   而契约是返回 ``(False, 原因)``。
3. Bitsearch 成了唯一可用的通用磁力源，却只取第 1 页且无镜像回退 ——
   单点故障等于 BT 搜索整体归零。
4. ``site_payload`` 忽略预设里的 ``enabled: False``，已知失效的源仍被启用。
"""

from __future__ import annotations

import asyncio

import pytest

from app.providers.indexer.bitsearch import FALLBACK_BASES, BitsearchIndexer
from app.providers.indexer.bt4g import Bt4gIndexer
from app.services import site_catalog
from app.utils.http import FetchError, is_cf_challenge

#: 实测抓到的真实拦截页（2026-09-04，https://bt4gprx.com/search/流浪地球/rss）。
#: 关键点：HTTP **200** —— 靠状态码完全分不出来。
CF_PAGE = (
    "<!DOCTYPE html><html><head>"
    "<title>Security Check</title>"
    '<script type="5e917545e24fa6d7b6bb4fd5-text/javascript">'
    'var cookieName = "cf_chl_out";'
    "</script></head><body><h1>Security Check</h1>"
    "<p>Please enable JavaScript to continue.</p></body></html>"
)


def _card(
    *,
    title: str = "某片 1080p",
    infohash: str = "52B40CF30576C173D2F0FBB4FF1C8E724959DF76",
    seeders: int = 11,
    leechers: int = 18,
    size: str = "4.52 GB",
) -> str:
    """造一张与实测站点结构一致的结果卡片。

    结构照抄 v1.26.0 已验证过的真实 HTML（含 ``&#x3D;`` / ``&amp;`` 转义）：
    自己简化会让解析测试测的是「我以为的页面」而不是站点真实输出。
    """
    return (
        '<div class="bg-white rounded-lg shadow-sm border border-gray-200 p-6 hover:shadow-md">'
        '<h3><a href="/torrent/6a988369dc4d69dbc52ee12e" class="hover:text-primary">'
        f"{title}</a></h3>"
        '<span class="inline-flex"><i class="fas fa-video text-blue-500"></i>'
        "<span>Other/Video</span></span>"
        '<span class="inline-flex"><i class="fas fa-download"></i>'
        f"<span>{size}</span></span>"
        '<span class="inline-flex"><i class="fas fa-calendar"></i><span>7/26/2025</span></span>'
        '<span class="inline-flex text-green-600"><i class="fas fa-arrow-up"></i>'
        f'<span class="font-medium">{seeders}</span><span>seeders</span></span>'
        '<span class="inline-flex text-red-600"><i class="fas fa-arrow-down"></i>'
        f'<span class="font-medium">{leechers}</span><span>leechers</span></span>'
        f'<a href="/download/torrent/{infohash}?title=x">Torrent</a>'
        f'<a href="magnet:?xt&#x3D;urn:btih:{infohash}'
        '&amp;dn&#x3D;%5BBitsearch.to%5D&amp;tr&#x3D;DHT">Magnet</a>'
        "</div>"
    )


NO_RESULT_PAGE = (
    '<div class="space-y-4" data-impression-ids="[]"></div>'
    '<h3 class="text-lg font-medium text-gray-900 mb-2">No results found</h3>'
)

class Test人机验证识别:
    """``is_cf_challenge`` 只识别不绕过（ADR-34：不逆向、不绕验证码）。"""

    def test_识别实测拦截页(self) -> None:
        assert is_cf_challenge(CF_PAGE) is True

    def test_正常RSS不误判(self) -> None:
        rss = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
        assert is_cf_challenge(rss) is False

    def test_空响应不误判(self) -> None:
        assert is_cf_challenge("") is False
        assert is_cf_challenge(None) is False

    def test_大页面即使含挑战路径也不误判(self) -> None:
        """长度闸门：正常大页面可能顺带引用 ``/cdn-cgi/`` 静态资源。

        假阳性比漏判更糟 —— 会把好站点标成坏的，用户跑去换域名换代理，
        而站点其实一直是好的。
        """
        big = "<html>" + ("x" * 9000) + "/cdn-cgi/challenge-platform/</html>"
        assert len(big) > 8192
        assert is_cf_challenge(big) is False

    def test_短拦截页各特征都能单独命中(self) -> None:
        """一个标记用在 N 处就要 N 个案例（约定 12）。"""
        for marker in (
            "cf_chl_out",
            "<title>Security Check</title>",
            "Just a moment",
            "cf-browser-verification",
            "/cdn-cgi/challenge-platform/",
        ):
            assert is_cf_challenge(f"<html>{marker}</html>") is True, marker


class TestBt4g识别人机验证:
    def _provider(self) -> Bt4gIndexer:
        return Bt4gIndexer({"name": "bt4g"})

    def test_解析拦截页报人机验证而非改版(self) -> None:
        """错报成「改版」会让人去改解析代码，而那毫无用处。"""
        with pytest.raises(FetchError) as caught:
            self._provider()._parse(CF_PAGE)
        message = str(caught.value)
        assert "人机验证" in message
        # 反向担保：不能再说成「改版」/「不是合法 RSS」
        assert "改版" not in message
        assert "不是合法 RSS" not in message

    def test_真改版仍报改版(self) -> None:
        """守卫不能把所有解析失败都吞成「人机验证」（约定 5）。"""
        with pytest.raises(FetchError) as caught:
            self._provider()._parse("<html><body>Moved Permanently</body></html>")
        assert "人机验证" not in str(caught.value)

    def test_合法RSS仍能解析(self) -> None:
        """本来会成功的后端必须依然成功。"""
        infohash = "a" * 40
        rss = (
            '<?xml version="1.0"?><rss version="2.0"><channel><item>'
            "<title>测试片 2160p</title>"
            f"<link>magnet:?xt=urn:btih:{infohash}</link>"
            f"<description><![CDATA[测试片<br>2.32GB<br>Movie<br>{infohash}]]></description>"
            "</item></channel></rss>"
        )
        resources = self._provider()._parse(rss)
        assert len(resources) == 1
        assert resources[0].size == int(2.32 * 1024**3)

class Test健康检查必须返回而不是抛出:
    """契约是返回 ``(bool, str)``。

    抛出去会让 ``POST /sites/{id}/test`` 变成 HTTP 500，
    用户看到「服务器错误」而不是「这个站上了人机验证」（约定 10：契约两端都要测）。
    """

    def test_bt4g遇拦截页返回False(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.providers.indexer.bt4g as mod

        async def fake(*args: object, **kwargs: object) -> str:
            return CF_PAGE

        monkeypatch.setattr(mod, "fetch_text", fake)
        ok, message = asyncio.run(Bt4gIndexer({"name": "bt4g"}).health_check())
        assert ok is False
        assert "人机验证" in message

    def test_bt4g正常时仍返回True(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """反向担保：守卫没把正常路径一起判死（约定 5）。"""
        import app.providers.indexer.bt4g as mod

        infohash = "c" * 40
        rss = (
            '<?xml version="1.0"?><rss version="2.0"><channel><item>'
            "<title>某片 1080p</title>"
            f"<link>magnet:?xt=urn:btih:{infohash}</link>"
            f"<description><![CDATA[某片<br>1.5GB<br>Movie<br>{infohash}]]></description>"
            "</item></channel></rss>"
        )

        async def fake(*args: object, **kwargs: object) -> str:
            return rss

        monkeypatch.setattr(mod, "fetch_text", fake)
        ok, message = asyncio.run(Bt4gIndexer({"name": "bt4g"}).health_check())
        assert ok is True
        assert "1" in message

    def test_bitsearch遇拦截页返回False(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.providers.indexer.bitsearch as mod

        async def fake(*args: object, **kwargs: object) -> str:
            return CF_PAGE

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        ok, message = asyncio.run(BitsearchIndexer({"name": "bs"}).health_check())
        assert ok is False
        assert "人机验证" in message

    def test_bitsearch正常时仍返回True(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.providers.indexer.bitsearch as mod

        async def fake(*args: object, **kwargs: object) -> str:
            return _card()

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        ok, message = asyncio.run(BitsearchIndexer({"name": "bs"}).health_check())
        assert ok is True
        assert "连通正常" in message

    def test_bitsearch改版仍报改版(self) -> None:
        with pytest.raises(FetchError) as caught:
            BitsearchIndexer({"name": "bs"})._parse("<html><body>whatever</body></html>")
        assert "人机验证" not in str(caught.value)
        assert "改版" in str(caught.value)

class TestBitsearch翻页:
    """只取第 1 页会漏掉近一半结果。

    实测（2026-09-04，「喜剧之王单口季」）：第 1 页 20 条、第 2 页 15 条，
    去重后 35 条 —— 是单页的 1.75 倍。
    """

    def _provider(self, **options: object) -> BitsearchIndexer:
        return BitsearchIndexer({"name": "bs", "options": options})

    def test_默认翻两页并合并(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.providers.indexer.bitsearch as mod

        pages: list[int] = []

        async def fake(url: str, **kwargs: object) -> str:
            page = 2 if "page=2" in url else 1
            pages.append(page)
            if page == 1:
                return _card(title="第一页", infohash="a" * 40)
            return _card(title="第二页", infohash="b" * 40)

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        resources = asyncio.run(self._provider().search("x"))
        assert pages == [1, 2], pages
        assert [r.title for r in resources] == ["第一页", "第二页"]

    def test_跨页重复按infohash去重(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """标题里带站点域名前缀（实测会变），只能按 infohash 去重。"""
        import app.providers.indexer.bitsearch as mod

        async def fake(url: str, **kwargs: object) -> str:
            title = "【www.5-123.com】同一个种" if "page=2" not in url else "【www.BPHDTV.com】同一个种"
            return _card(title=title, infohash="d" * 40)

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        resources = asyncio.run(self._provider().search("x"))
        assert len(resources) == 1

    def test_第二页失败保留第一页结果(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """实测第 3 页起会读超时/500。

        把「部分成功」降级成「彻底失败」比不翻页更糟。
        """
        import app.providers.indexer.bitsearch as mod

        async def fake(url: str, **kwargs: object) -> str:
            if "page=2" in url:
                raise FetchError("HTTP 500：站点内部错误", status=500)
            return _card(title="第一页", infohash="a" * 40)

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        resources = asyncio.run(self._provider().search("x"))
        assert [r.title for r in resources] == ["第一页"]

    def test_第一页失败必须抛错(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """第 1 页失败是真故障，不能吞成「没有结果」（ADR-82）。"""
        import app.providers.indexer.bitsearch as mod

        async def fake(url: str, **kwargs: object) -> str:
            raise FetchError("HTTP 500：站点内部错误", status=500)

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        with pytest.raises(FetchError) as caught:
            asyncio.run(self._provider().search("x"))
        assert caught.value.status == 500

    def test_零结果页不再继续翻页(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.providers.indexer.bitsearch as mod

        calls: list[str] = []

        async def fake(url: str, **kwargs: object) -> str:
            calls.append(url)
            return NO_RESULT_PAGE

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        assert asyncio.run(self._provider().search("zzz")) == []
        assert len(calls) == 1, calls

    def test_max_pages可配(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.providers.indexer.bitsearch as mod

        calls: list[str] = []

        async def fake(url: str, **kwargs: object) -> str:
            calls.append(url)
            return _card(title=f"第{len(calls)}页", infohash=str(len(calls)) * 40)

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        asyncio.run(self._provider(max_pages=3).search("x"))
        assert len(calls) == 3, calls

class TestBitsearch镜像回退:
    """BT4G 失效后 Bitsearch 是唯一的通用磁力源，单点故障 = BT 搜索归零。"""

    def test_主域名不可达时换备用域名(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.providers.indexer.bitsearch as mod

        tried: list[str] = []

        async def fake(url: str, **kwargs: object) -> str:
            tried.append(url)
            if "bad.invalid" in url:
                raise FetchError("无法建立连接（服务未运行 / 端口不通）")
            return _card(title="回退命中", infohash="e" * 40)

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        provider = BitsearchIndexer({"name": "bs", "url": "https://bad.invalid"})
        resources = asyncio.run(provider.search("x"))
        assert [r.title for r in resources] == ["回退命中"]
        assert "bad.invalid" in tried[0]
        assert any(FALLBACK_BASES[0] in u for u in tried), tried

    def test_全部域名失败才抛错(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.providers.indexer.bitsearch as mod

        tried: list[str] = []

        async def fake(url: str, **kwargs: object) -> str:
            tried.append(url)
            raise FetchError("无法建立连接（服务未运行 / 端口不通）")

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        with pytest.raises(FetchError):
            asyncio.run(BitsearchIndexer({"name": "bs"}).search("x"))
        # 默认域名 + 全部备用域名都要试过，不能只试一个就放弃
        assert len(tried) == 1 + len(FALLBACK_BASES), tried

    def test_主域名可用时不打备用域名(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """回退只在失败时发生，正常情况下不该额外增加对镜像站的请求。"""
        import app.providers.indexer.bitsearch as mod

        tried: list[str] = []

        async def fake(url: str, **kwargs: object) -> str:
            tried.append(url)
            return NO_RESULT_PAGE

        monkeypatch.setattr(mod, "fetch_text_result", fake)
        asyncio.run(BitsearchIndexer({"name": "bs"}).search("x"))
        assert len(tried) == 1
        for base in FALLBACK_BASES:
            assert base not in tried[0]

    def test_备用域名不重复主域名(self) -> None:
        provider = BitsearchIndexer({"name": "bs", "url": FALLBACK_BASES[0]})
        bases = provider._bases()
        assert len(bases) == len(set(bases)), bases


class Test已失效预设不被启用:
    """``site_payload`` 必须尊重预设里的 ``enabled: False``。

    否则每次搜索都要多等一个必然失败的站点，并在诊断里刷一条错误。
    """

    def _preset(self, preset_id: str) -> dict:
        for item in site_catalog.list_bt_presets():
            if item["id"] == preset_id:
                return item
        raise AssertionError(f"预设不存在：{preset_id}")

    def test_bt4g预设标记为未验证且默认关闭(self) -> None:
        preset = self._preset("bt4g")
        assert preset.get("verified") is False
        assert preset.get("enabled") is False

    def test_请求启用也不会启用已失效的源(self) -> None:
        payload = site_catalog.site_payload(self._preset("bt4g"), enabled=True)
        assert payload["enabled"] is False

    def test_可用的源仍按请求启用(self) -> None:
        """反向担保：别把所有站点都关掉。"""
        payload = site_catalog.site_payload(self._preset("bitsearch"), enabled=True)
        assert payload["enabled"] is True

    def test_可用的源也能被显式关闭(self) -> None:
        payload = site_catalog.site_payload(self._preset("bitsearch"), enabled=False)
        assert payload["enabled"] is False

    def test_bt4g缺陷写进note让用户看得到(self) -> None:
        note = site_catalog.site_payload(self._preset("bt4g"))["options"]["note"]
        assert "人机验证" in note


# ---------------------------------------------- v1.28.0：搜索侧与过滤侧的季号写法必须一致
def test_filter_whitelist_covers_season_spelling_variants():
    """搜索用「第3季」写法搜到的结果，不能被只认「第三季」的白名单拒掉。

    这是一个**跨层自相矛盾**的缺陷：``search.build_keywords()`` 刻意生成
    「第三季」「第3季」两种关键词（资源站两种写法都存在），但
    ``title_match_variants()`` 以前只按用户的原始写法建白名单。
    于是「用一种写法搜到、用另一种写法判不匹配」，搜索层辛苦捞回来的结果
    在过滤层被自己扔掉。

    实测（关键词「喜剧之王单口季 第三季」）：PanSou 返回 1 条 → kept 0 条，
    界面显示「返回 1 条但都不匹配」，用户以为没资源。修复后 total 1 → 23。
    """
    from app.core.filters import match_keywords
    from app.services.title_alias import title_match_variants

    whitelist = title_match_variants("喜剧之王单口季 第三季")
    # 这三种都是资源站上真实存在的写法，必须全部认得
    for real_title in (
        "喜剧之王单口季 第三季 (2026) [4K/1080P] [国语中字]",
        "喜剧之王单口季第3季 4K SDR",
        "喜剧之王单口季3 喜单3(2026)【4K.SDR】",
    ):
        assert match_keywords(real_title, whitelist, mode="any"), (
            f"白名单漏了这种写法，搜到也会被过滤掉：{real_title}"
        )


def test_filter_whitelist_still_rejects_other_seasons():
    """放宽写法不等于放宽季号：第二季的资源仍然必须被拒。

    这是上一个测试的**反向约束**。如果为了让「第3季」能匹配就把季号整个
    去掉，第二季、第一季的资源会一起混进来 —— 那是另一个更难发现的 bug
    （用户订阅第三季，下回来一堆第二季）。
    """
    from app.core.filters import match_keywords
    from app.services.title_alias import title_match_variants

    whitelist = title_match_variants("喜剧之王单口季 第三季")
    for wrong_season in (
        "喜剧之王单口季.第二季[全10集].King.of.Comedy.S02.2024.2160p",
        "喜剧之王单口季第1季",
    ):
        assert not match_keywords(wrong_season, whitelist, mode="any"), (
            f"季号被放宽了，别的季会混进结果：{wrong_season}"
        )


def test_season_rewrites_handles_teens_and_parts():
    """两位数季号与「第N部」写法也要覆盖（十进制换算不能只对个位数生效）。"""
    from app.services.title_alias import _season_rewrites

    got = _season_rewrites("某剧 第12季")
    assert any("第十二季" in item for item in got), got
    parts = _season_rewrites("某片 第二部")
    assert any("第2部" in item for item in parts), parts
