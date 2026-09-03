"""导航站一键接站 + 落地目录记忆 + 百度 bdstoken 诊断（全程离线）。

本文件回归 v1.21.0 修的三个用户可见问题：

1. **发现列表只能看不能加**：`/sites/discover` 抓回候选后，前端只渲染
   「已添加 / 未添加」状态，没有任何添加入口 —— 用户得自己抄下域名，
   再去「新增站点」把 provider / search_url / magnet_only 一个个猜对。
   而这些信息探测时本来就能拿到。
2. **落地目录每次都要重输**：`target_dir` 从来没被记住过，
   转存一次填一次；订阅巡检等无头入口更是完全没有默认值可用。
3. **「取不到 bdstoken」把两种病因说成一种**：实测无 Cookie 与
   垃圾 Cookie 都返回 `errno=-6` + `result=[]`，原来一律提示
   「Cookie 可能已过期」，让没登录的用户去重新登录（对）、
   让网络不通的用户也去重新登录（错，白忙）。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.providers.panstorage.baidu import BaiduPanStorage
from app.services import discovery as discovery_service
from app.services import pan_storage as pan_service


def run(coro):
    return asyncio.run(coro)


def _patch_http(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    def factory(timeout=None, headers=None, follow_redirects=True):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers=headers or {},
            follow_redirects=follow_redirects,
        )

    import app.providers.panstorage.baidu as mod
    import app.utils.http as uh

    monkeypatch.setattr(mod, "async_client", factory)
    monkeypatch.setattr(uh, "async_client", factory)


# ==================================================== ① 导航站一键接站
def test_probe_reuses_zhuiju_judgement(monkeypatch):
    """发现的候选与社区清单候选面临同一个问题，不该有第二套判据。

    **行为断言而非源码断言**：只 `inspect` 源码里有没有 "zhuiju" 是不够的 ——
    注入验证证实过，把 `probe_site` 调用整段删掉后 import 语句仍在源码里，
    源码断言照样绿。这里真正校验「probe_discovered 会把判定委托出去」。
    """
    from app.services import zhuiju as zhuiju_service

    called: dict[str, Any] = {}

    async def fake_probe(url, *, keywords=None, timeout=15.0):
        called["url"] = url
        return {"probe": "searchable", "probe_note": "「庆余年」搜到 磁力 3",
                "search_url": "https://example.com/?s={keyword}"}

    monkeypatch.setattr(zhuiju_service, "probe_site", fake_probe)
    result = run(discovery_service.probe_discovered("https://example.com"))

    assert called.get("url") == "https://example.com", (
        "probe_discovered 没把判定委托给 zhuiju.probe_site，"
        "等于自己写了第二套判据（会重踩 ADR-70：首页 200 ≠ 搜得到）"
    )
    assert result["probe"] == "searchable"
    assert result["options"]["search_url"] == "https://example.com/?s={keyword}"


def test_interactive_probe_is_bounded():
    """交互式探测必须收窄参数，否则按钮点下去像没反应。

    实测踩到过：照搬 zhuiju 的定时任务参数（3 关键词 × 5 种搜索形态 × 15s）
    最坏 225s，live 验证时真的把 HTTP 客户端拖到超时。
    """
    assert len(discovery_service.INTERACTIVE_KEYWORDS) == 1, (
        "交互式探测只该用 1 个关键词；probe_site 是 keywords × 形态 的双层循环"
    )
    assert discovery_service.PROBE_BUDGET_SECONDS <= 60


def test_probe_timeout_reports_unknown_not_blocked(monkeypatch):
    """超预算要报 unknown，**不能**报 blocked。

    差别很要紧：blocked 是「站点拒绝我们」，unknown 是「我们没探完」。
    把后者说成前者，等于替用户对别人的站下了一个我们没验证过的结论。
    """
    from app.services import zhuiju as zhuiju_service

    async def never_finishes(url, *, keywords=None, timeout=15.0):
        await asyncio.sleep(60)

    monkeypatch.setattr(zhuiju_service, "probe_site", never_finishes)
    result = run(discovery_service.probe_discovered("https://slow.example", budget=0.2))

    assert result["probe"] == "unknown", "超时被误报成 blocked/searchable"
    assert "未完成" in result["probe_note"] or "超过" in result["probe_note"]
    # 且不能因为超时就放行落库
    assert discovery_service.suggest_from_probe("慢站", "https://slow.example", result)["ok"] is False


def test_suggest_rejects_non_searchable():
    """只有真搜到资源的站才允许落库。

    `reachable_only` 是最危险的一档：页面打得开，看着像能用，
    加进搜索链路后每次搜索白等几秒却永远 0 条。
    """
    for probe_state in ("reachable_only", "blocked", "unknown"):
        result = discovery_service.suggest_from_probe(
            "某站", "https://example.com",
            {"probe": probe_state, "probe_note": "页面可访问但未搜到任何可下载链接"},
        )
        assert result["ok"] is False, f"{probe_state} 不该放行"
        assert result["reason"]


def test_suggest_accepts_searchable_and_carries_search_url():
    """放行时必须把探测拿到的 search_url 带进 options。

    这是「一键」的全部意义：search_url 是 html_generic 能不能搜的关键，
    丢了它用户还是得自己填，等于没省事。
    """
    result = discovery_service.suggest_from_probe(
        "磁力站", "https://example.com",
        {
            "probe": "searchable",
            "probe_note": "「庆余年」搜到 磁力 12",
            "provider": "html_generic",
            "kind": "indexer",
            "options": {"search_url": "https://example.com/?s={keyword}", "magnet_only": True},
        },
        source="硬核指南",
    )
    assert result["ok"] is True
    assert result["options"]["search_url"] == "https://example.com/?s={keyword}"
    assert result["options"]["magnet_only"] is True
    assert "硬核指南" in result["options"]["note"]


def test_kkso_domain_maps_to_pan_kind():
    """kkso 系是网盘搜而非磁力索引，kind 必须是 pan。

    与 zhuiju.suggest_site 保持一致 —— 同一个站在两处给出不同 provider
    会让用户以为是随机的。
    """
    provider, kind, _ = discovery_service._provider_guess("www.kkso.cc", "")
    assert (provider, kind) == ("kkso", "pan")


# ==================================================== ② 落地目录记忆
def test_suggested_dir_prefers_remembered_over_root():
    """记忆值优先于站点默认 root_path。"""

    class _Fake:
        config = {"options": {pan_service.LAST_DIR_KEY: "/影视/剧集"}}
        root_path = "/"

    assert pan_service.suggested_target_dir(_Fake()) == "/影视/剧集"


def test_suggested_dir_falls_back_to_root_path():
    """没有记忆时退回 root_path，而不是空字符串。"""

    class _Fake:
        config: dict[str, Any] = {"options": {}}
        root_path = "/来自：分享"

    assert pan_service.suggested_target_dir(_Fake()) == "/来自：分享"


def test_save_share_writes_back_remembered_dir(monkeypatch):
    """转存成功后必须**回写**记忆，否则下次仍要重输 —— 这正是用户报的问题。

    注入验证暴露过：只加 suggested_target_dir 而不回写，
    前面那些「记忆优先」的用例照样全绿，因为根本没人写过记忆值。
    """
    saved: dict[str, Any] = {}

    class _Storage:
        site_name = "夸克网盘"
        supports_save = True
        root_path = "/来自：分享"
        config = {"id": 7, "options": {}}

        async def save_share(self, share_url, *, password=None, target_dir=None):
            from app.providers.panstorage.base import SaveResult

            saved["target_dir"] = target_dir
            return SaveResult(success=True, saved_path=target_dir or "/", message="ok")

    recorded: dict[str, Any] = {}
    monkeypatch.setattr(pan_service, "get_storage", lambda _id: _Storage())
    monkeypatch.setattr(
        pan_service, "remember_target_dir",
        lambda site_id, target_dir: recorded.update(site_id=site_id, target_dir=target_dir),
    )
    monkeypatch.setattr(pan_service, "_record_save", lambda *a, **k: None, raising=False)

    run(pan_service.save_share("https://pan.quark.cn/s/x", site_id=7, target_dir="/影视/剧集"))
    assert recorded.get("target_dir") == "/影视/剧集", "转存成功却没回写落地目录记忆"
    assert recorded.get("site_id") == 7


def test_save_share_uses_remembered_dir_when_not_given(monkeypatch):
    """无头入口（订阅巡检/ChatOps）不传目录时，也要享受到记忆值。

    这是把记忆放在服务端而非 localStorage 的**全部理由**：
    这些入口没有浏览器。
    """
    used: dict[str, Any] = {}

    class _Storage:
        site_name = "夸克网盘"
        supports_save = True
        root_path = "/默认"
        config = {"id": 9, "options": {pan_service.LAST_DIR_KEY: "/影视/动漫"}}

        async def save_share(self, share_url, *, password=None, target_dir=None):
            from app.providers.panstorage.base import SaveResult

            used["target_dir"] = target_dir
            return SaveResult(success=True, saved_path=target_dir or "/", message="ok")

    monkeypatch.setattr(pan_service, "get_storage", lambda _id: _Storage())
    monkeypatch.setattr(pan_service, "remember_target_dir", lambda *a, **k: None)

    run(pan_service.save_share("https://pan.quark.cn/s/x", site_id=9))
    assert used.get("target_dir") == "/影视/动漫", "没传目录时应使用记忆值而不是 None"


def test_overview_ships_suggested_dir():
    """总览必须下发 suggested_target_dir，否则前端无法预填。

    钉住字段名本身：改掉它前端会静默拿到 undefined，
    输入框回到空白 —— 正是修复前的表现，且不会有任何报错。
    """
    import inspect

    source = inspect.getsource(pan_service.overview)
    assert '"suggested_target_dir": suggested_target_dir(storage)' in source, (
        "overview 没把建议目录下发给前端，转存弹窗会退回每次手输"
    )


def test_remember_ignores_blank_and_missing_site():
    """空目录/无 site_id 不该写库，否则会把记忆清成空值。"""
    # 不该抛异常，也不该有任何副作用
    pan_service.remember_target_dir(None, "/x")
    pan_service.remember_target_dir(1, "")
    pan_service.remember_target_dir(1, "   ")


# ==================================================== ③ 百度 bdstoken 诊断
def test_auth_hint_distinguishes_missing_cookie():
    """没配 Cookie 时要说「去登录」，不能说「登录过期」。"""
    store = BaiduPanStorage({})
    hint = store._auth_hint("转存")
    assert "未配置" in hint
    assert "过期" not in hint


def test_auth_hint_distinguishes_expired_cookie():
    """有 Cookie 但 errno=-6 —— 这才是真的失效，实测无 Cookie 也是 -6。"""
    store = BaiduPanStorage({"cookie": "stale"})
    store._last_errno = -6
    hint = store._auth_hint("转存")
    assert "errno=-6" in hint
    assert "重新扫码登录" in hint


def test_auth_hint_distinguishes_network_failure():
    """连不上时让用户查网络，而不是去重新登录（原来会误导成后者）。"""
    store = BaiduPanStorage({"cookie": "good"})
    store._last_errno = None
    hint = store._auth_hint("转存")
    assert "网络" in hint or "代理" in hint
    assert "重新扫码" not in hint


def test_token_falls_back_to_html(monkeypatch):
    """模板变量接口没给 bdstoken 时，要能从首页 HTML 里兜底捞到。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/gettemplatevariable":
            # errno=0 但 result 里没有 bdstoken —— 实测存在这种账号态
            return httpx.Response(200, json={"errno": 0, "result": {}})
        if request.url.path == "/disk/home":
            return httpx.Response(200, text='var context={"bdstoken":"' + "a" * 32 + '"};')
        return httpx.Response(404, json={"errno": 404})

    _patch_http(monkeypatch, handler)
    store = BaiduPanStorage({"cookie": "c"})
    assert run(store._token()) == "a" * 32


def test_token_empty_when_login_page(monkeypatch):
    """未登录时百度会 302 到登录页，那页没有 bdstoken —— 必须如实返回空。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/gettemplatevariable":
            return httpx.Response(200, json={"errno": -6, "result": []})
        return httpx.Response(200, text="<html>请先登录</html>")

    _patch_http(monkeypatch, handler)
    store = BaiduPanStorage({"cookie": "stale"})
    assert run(store._token()) == ""


def test_save_share_reports_actionable_reason(monkeypatch):
    """转存失败的消息必须能指向下一步动作，而不是一句「可能已过期」。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/gettemplatevariable":
            return httpx.Response(200, json={"errno": -6, "result": []})
        return httpx.Response(200, text="<html>请先登录</html>")

    _patch_http(monkeypatch, handler)
    store = BaiduPanStorage({"cookie": "stale"})
    result = run(store.save_share("https://pan.baidu.com/s/1abcdefg"))
    assert result.success is False
    assert "errno=-6" in result.message
    assert "重新扫码登录" in result.message
