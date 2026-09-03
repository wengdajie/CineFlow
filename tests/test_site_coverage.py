"""站点覆盖面与故障诊断回归用例（v1.19.0）。

本文件守的是三类**静默故障**——都不报错，只是结果悄悄变错/变少：

1. **把"服务已死"显示成"没有匹配结果"**。实测 Jackett 挂掉返回 502，
   而 ``fetch_text`` 一律返回 ``None``，上层 ``if not text: return []``
   于是与"这个站确实没这部片"完全同形。用户会去反复换关键词，
   而真正该做的是把 Jackett 拉起来。
2. **资源类型被站点配置串味**。``guess_kind(link, declared)`` 的 ``declared``
   来自站点的 ``kind`` 字段，那是 **ProviderKind**（indexer/pan）不是
   **ResourceKind**（magnet/torrent/pan），直接返回会把所有磁力标成
   ``kind="indexer"``，下载路由查不到而回退兜底。
3. **已死的站永远参与搜索**。硬失败（502）不是 timeout，原先完全不进
   熔断计数，于是每次聚合搜索都要为这个死站白付固定开销。
"""

from __future__ import annotations

import httpx
import pytest

from app.providers.indexer.generic_api import RESOURCE_KINDS, guess_kind
from app.schemas.enums import ProviderKind, ResourceKind
from app.services import site_catalog
from app.utils.http import FetchError, describe_http_error

# ---------------- 1. HTTP 失败原因必须可辨识 ----------------


class TestDescribeHttpError:
    """错误分类要能让用户知道**下一步做什么**，笼统报错等于让人瞎试。"""

    def _resp(self, code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "http://127.0.0.1:9117/api")
        response = httpx.Response(code, request=request)
        return httpx.HTTPStatusError("boom", request=request, response=response)

    def test_502_points_at_dead_service(self) -> None:
        """502 必须明确指向"服务没运行"——这正是 Jackett 挂掉时的实测现象。"""
        message, status, kind = describe_http_error(self._resp(502))
        assert status == 502
        assert kind == "http"
        assert "502" in message
        assert "没有运行" in message

    def test_401_and_403_are_distinguished(self) -> None:
        """认证失败与被反爬拒绝的处置完全不同，不能都报"请求失败"。"""
        unauth, _, _ = describe_http_error(self._resp(401))
        forbidden, _, _ = describe_http_error(self._resp(403))
        assert unauth != forbidden
        assert "API Key" in unauth or "认证" in unauth
        assert "拒绝" in forbidden or "反爬" in forbidden

    def test_404_mentions_path(self) -> None:
        message, status, _ = describe_http_error(self._resp(404))
        assert status == 404
        assert "地址" in message or "路径" in message

    def test_connect_error_is_not_reported_as_http(self) -> None:
        """连不上没有状态码，kind 必须是 error 而不是 http。"""
        message, status, kind = describe_http_error(
            httpx.ConnectError("All connection attempts failed")
        )
        assert status is None
        assert kind == "error"
        assert "连接" in message

    def test_timeouts_are_split(self) -> None:
        """连接超时（地址不通）与读取超时（站点慢）原因不同。"""
        connect, _, _ = describe_http_error(httpx.ConnectTimeout("t"))
        read, _, _ = describe_http_error(httpx.ReadTimeout("t"))
        assert connect != read

    def test_dns_failure_hints_domain_change(self) -> None:
        """资源站换域名极频繁，DNS 失败要直接点出来。"""
        message, _, _ = describe_http_error(
            httpx.ConnectError("[Errno -2] Name or service not known")
        )
        assert "域名" in message


class TestFetchError:
    def test_carries_status_and_message(self) -> None:
        exc = FetchError("HTTP 502：网关错误", status=502, kind="http")
        assert exc.status == 502
        assert exc.kind == "http"
        assert "502" in str(exc)

    def test_defaults_to_error_kind(self) -> None:
        assert FetchError("boom").kind == "error"
        assert FetchError("boom").status is None


# ---------------- 2. 资源类型不能被 ProviderKind 串味 ----------------


class TestGuessKind:
    """这是本轮抓到的**真缺陷**：站点 kind 字段会覆盖链接推断。"""

    def test_provider_kind_must_not_override_magnet(self) -> None:
        """站点 kind="indexer" 时，磁力必须仍判成 magnet。

        回归的是：``if declared: return declared`` 会让所有磁力变成
        ``kind="indexer"``，而 ResourceKind 里没有 indexer，
        下载路由 ``route_of`` 查不到就回退到 torrent 兜底。
        """
        magnet = "magnet:?xt=urn:btih:" + "a" * 40
        assert guess_kind(magnet, ProviderKind.INDEXER.value) == ResourceKind.MAGNET.value

    def test_provider_kind_indexer_still_detects_pan(self) -> None:
        assert (
            guess_kind("https://pan.baidu.com/s/1abc", ProviderKind.INDEXER.value)
            == ResourceKind.PAN.value
        )

    def test_legit_resource_kind_is_respected(self) -> None:
        """合法的 ResourceKind 仍要被尊重，不能一律推断覆盖用户显式声明。"""
        assert guess_kind("https://example.com/x", ResourceKind.PAN.value) == (
            ResourceKind.PAN.value
        )
        assert guess_kind("https://example.com/x", ResourceKind.WEBVIDEO.value) == (
            ResourceKind.WEBVIDEO.value
        )

    def test_declared_whitelist_matches_enum(self) -> None:
        """白名单必须与 ResourceKind 枚举完全一致，否则漏一个就又能串味。"""
        assert {kind.value for kind in ResourceKind} == RESOURCE_KINDS

    def test_magnet_detected_without_declaration(self) -> None:
        magnet = "magnet:?xt=urn:btih:" + "b" * 40
        assert guess_kind(magnet, None) == ResourceKind.MAGNET.value


# ---------------- 3. 实测站点清单的自洽性 ----------------


class TestSiteCatalog:
    """清单里每条都声称"实测可用"，那么这些声明本身必须是自洽的。"""

    def test_bt_presets_are_not_empty(self) -> None:
        assert site_catalog.list_bt_presets()

    def test_every_bt_preset_has_evidence(self) -> None:
        """``measured`` 是收录凭据。缺了它，"实测可用"就成了一句空话。"""
        for preset in site_catalog.list_bt_presets():
            assert preset.get("measured"), f"{preset['id']} 缺少实测凭据"
            assert preset.get("verified") is True
            assert preset.get("provider")
            assert str(preset.get("url", "")).startswith("http")

    def test_bt_presets_build_valid_site_payload(self) -> None:
        for preset in site_catalog.list_bt_presets():
            payload = site_catalog.site_payload(preset)
            assert payload["kind"] == ProviderKind.INDEXER.value
            assert payload["name"] and payload["url"]
            assert payload["options"]["preset_id"] == preset["id"]
            # 实测数据要落进 note，用户在站点详情里看得到
            assert preset["measured"] in payload["options"]["note"]

    def test_bt_preset_ids_unique(self) -> None:
        ids = [p["id"] for p in site_catalog.list_bt_presets()]
        assert len(ids) == len(set(ids))

    def test_html_presets_declare_search_url(self) -> None:
        """html_generic 没有 search_url 就搜不出任何东西，且不会报错。"""
        for preset in site_catalog.list_bt_presets():
            if preset["provider"] == "html_generic":
                assert preset["options"].get("search_url")

    def test_post_search_presets_carry_payload(self) -> None:
        """POST 搜索站漏了 search_data 会静默返回首页（cilixiong 实测坑）。"""
        for preset in site_catalog.list_bt_presets():
            options = preset["options"]
            if str(options.get("search_method", "")).upper() == "POST":
                assert options.get("search_data"), f"{preset['id']} POST 缺 search_data"
                assert "{keyword}" in str(options["search_data"])

    def test_two_stage_presets_declare_detail_pattern(self) -> None:
        """只开 magnet_only 而没有 detail_link_field 时，搜索页本身没有磁力 → 恒空。"""
        for preset in site_catalog.list_bt_presets():
            options = preset["options"]
            if options.get("magnet_only") and not options.get("row_pattern"):
                assert options.get("detail_link_field"), f"{preset['id']} 缺详情页正则"

    def test_presets_with_caveats_state_them(self) -> None:
        """size 恒为 0 这类缺陷必须写进 caveat，否则用户会以为是自己配错了。"""
        bdflixs = site_catalog.get_bt_preset("bdflixs")
        assert bdflixs is not None
        assert "size" in bdflixs["caveat"] or "体积" in bdflixs["caveat"]

    def test_get_bt_preset_unknown_returns_none(self) -> None:
        assert site_catalog.get_bt_preset("nope-does-not-exist") is None


class TestRssPresets:
    def test_rss_presets_are_not_empty(self) -> None:
        assert site_catalog.list_rss_presets()

    def test_adult_source_excluded_by_default(self) -> None:
        """成人向源不该在「一键添加推荐源」时被顺手带进去。"""
        default_ids = {p["id"] for p in site_catalog.list_rss_presets()}
        all_ids = {p["id"] for p in site_catalog.list_rss_presets(include_adult=True)}
        assert "sukebei" in all_ids
        assert "sukebei" not in default_ids
        assert default_ids < all_ids

    def test_every_rss_preset_declares_known_dialect(self) -> None:
        """方言写错会让预览页显示的解析方式与实际不符（kisssub 实测踩到）。"""
        for preset in site_catalog.list_rss_presets(include_adult=True):
            assert preset["dialect"] in rss_dialect_names(), preset["id"]

    def test_rss_presets_build_valid_payload(self) -> None:
        for preset in site_catalog.list_rss_presets(include_adult=True):
            payload = site_catalog.feed_payload(preset)
            assert payload["name"] and payload["url"].startswith("http")
            assert payload["dialect"] in rss_dialect_names()

    def test_rss_preset_urls_unique(self) -> None:
        urls = [p["url"] for p in site_catalog.list_rss_presets(include_adult=True)]
        assert len(urls) == len(set(urls))

    def test_kisssub_dialect_is_generic_as_measured(self) -> None:
        """实测该站 feed 自述「爱恋动漫」，方言层判定 generic 而非 acgnx。

        这条用例把"实测结论"钉住：如果有人按站点系列想当然改成 acgnx，
        预览页显示的方言就会与实际解析方式不符。
        """
        preset = site_catalog.get_rss_preset("kisssub")
        assert preset is not None
        assert preset["dialect"] == "generic"

    def test_get_rss_preset_unknown_returns_none(self) -> None:
        assert site_catalog.get_rss_preset("nope-does-not-exist") is None


def rss_dialect_names() -> tuple[str, ...]:
    from app.core.rss_dialects import DIALECTS

    return DIALECTS


# ---------------- 4. 熔断要覆盖「硬失败」 ----------------


class TestBreakerHardFailure:
    """已死的站必须被熔断，否则每次搜索都为它白付固定开销。"""

    @pytest.fixture(autouse=True)
    def _clean(self):
        from app.services import search_breaker

        search_breaker.reset()
        yield
        search_breaker.reset()

    def test_record_failure_trips_after_threshold(self) -> None:
        from app.core.config import settings
        from app.services import search_breaker

        site = "站点-硬失败"
        threshold = max(1, int(settings.SEARCH_BREAKER_THRESHOLD))
        for _ in range(threshold - 1):
            assert search_breaker.record_failure(site, 3400, "HTTP 502") is False
            assert search_breaker.is_open(site) is False
        assert search_breaker.record_failure(site, 3400, "HTTP 502") is True
        assert search_breaker.is_open(site) is True

    def test_skip_reason_carries_real_cause(self) -> None:
        """跳过原因要带上真实原因（502），不能只说"连续超时"——那是误导。"""
        from app.core.config import settings
        from app.services import search_breaker

        site = "站点-原因"
        for _ in range(max(1, int(settings.SEARCH_BREAKER_THRESHOLD))):
            search_breaker.record_failure(site, 3400, "HTTP 502：网关错误")
        reason = search_breaker.skip_reason(site)
        assert "502" in reason
        assert "自动重试" in reason

    def test_success_clears_hard_failure_strikes(self) -> None:
        """站点恢复后必须立刻清零，不能让它继续背着历史包袱。"""
        from app.services import search_breaker

        site = "站点-恢复"
        search_breaker.record_failure(site, 3400, "HTTP 502")
        search_breaker.record_success(site)
        snapshot = {row["site"]: row for row in search_breaker.snapshot()}
        assert snapshot[site]["strikes"] == 0
        assert snapshot[site]["open"] is False
