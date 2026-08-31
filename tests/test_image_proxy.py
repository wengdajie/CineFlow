"""图片代理测试：SSRF 防护与白名单（离线）。"""

from __future__ import annotations

import httpx
import pytest

from app.api.routers import images as images_router
from app.api.routers.images import douban_candidates, resolve_referer


# ---------------------------------------------------------------- 白名单
def test_allows_known_image_hosts():
    assert resolve_referer("https://img3.doubanio.com/view/photo/x.jpg") is not None
    assert resolve_referer("https://i0.hdslb.com/bfs/archive/x.jpg") is not None
    assert resolve_referer("https://i.ytimg.com/vi/abc/hqdefault.jpg") is not None


def test_douban_uses_douban_referer():
    """必须带豆瓣 Referer，否则图床返回 418。"""
    assert resolve_referer("https://img3.doubanio.com/x.jpg") == "https://movie.douban.com/"


def test_rejects_unknown_host():
    assert resolve_referer("https://evil.example.com/x.jpg") is None


def test_rejects_suffix_confusion_attack():
    """``doubanio.com.attacker.net`` 不能被当成豆瓣放行。"""
    assert resolve_referer("https://doubanio.com.attacker.net/x.jpg") is None
    assert resolve_referer("https://evil-doubanio.com/x.jpg") is None


def test_rejects_internal_addresses():
    """SSRF 关键防线：内网地址一律拒绝。"""
    for url in (
        "http://127.0.0.1:6060/api/v1/system/settings",
        "http://localhost/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/",
        "http://[::1]/",
    ):
        assert resolve_referer(url) is None, url


def test_rejects_non_http_schemes():
    for url in ("file:///etc/passwd", "gopher://x/", "ftp://doubanio.com/x.jpg", "data:image/png;base64,AAA"):
        assert resolve_referer(url) is None, url


def test_rejects_empty_and_malformed():
    assert resolve_referer("") is None
    assert resolve_referer("not a url") is None


# ---------------------------------------------------------------- 端点
def test_proxy_rejects_non_whitelisted(client):
    """非白名单返回 400，且不需要登录（img 标签带不了 token）。"""
    response = client.get("/api/v1/images/proxy?url=https://evil.example.com/x.jpg")
    assert response.status_code == 400


def test_proxy_rejects_internal_url(client):
    response = client.get("/api/v1/images/proxy?url=http://127.0.0.1:6060/api/health")
    assert response.status_code == 400


def test_proxy_requires_url_param(client):
    assert client.get("/api/v1/images/proxy").status_code == 422


def test_proxy_is_anonymous(client):
    """确认该端点不挂认证：未登录时不该是 401（而是白名单校验的 400）。"""
    response = client.get("/api/v1/images/proxy?url=https://evil.example.com/x.jpg")
    assert response.status_code != 401


# --- 豆瓣坏镜像 img9 的回归用例 -------------------------------------------
# 背景：豆瓣把封面随机分到 img1/2/3/9，实测 img9 恒返回 200 + text/html 反爬页，
# 导致约 1/4 封面裂图。修复方式是「候选镜像轮换 + 入库前改写」，下面两组用例钉住它。


def test_douban_candidates_excludes_bad_mirror():
    """img9 必须被排除，且展开出多个可用镜像作为备选。"""
    url = "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p123.jpg"
    candidates = douban_candidates(url)
    assert len(candidates) > 1, "豆瓣地址应展开成多个候选镜像"
    assert all("img9." not in c for c in candidates), "坏镜像 img9 不得出现在候选里"
    # 路径必须保持原样，只换主机名（各镜像共享同一套路径）
    assert all(c.endswith("/view/photo/s_ratio_poster/public/p123.jpg") for c in candidates)


def test_douban_candidates_keeps_good_mirror_path():
    """好镜像地址也走轮换，保证单个镜像临时故障时仍有备选。"""
    url = "https://img3.doubanio.com/view/photo/l/public/p9.jpg"
    candidates = douban_candidates(url)
    assert url in candidates


def test_douban_candidates_passthrough_for_other_hosts():
    """非豆瓣图床没有镜像可换，必须原样返回单个候选，避免误改地址。"""
    for url in (
        "https://i0.hdslb.com/bfs/archive/x.jpg",
        "https://i.ytimg.com/vi/abc/hqdefault.jpg",
    ):
        assert douban_candidates(url) == [url]


def test_normalize_cover_rewrites_bad_mirror():
    """入库前就把 img9 改写掉，避免坏地址落库后长期裂图。"""
    from app.providers.metadata.douban_chart import _normalize_cover

    assert _normalize_cover(
        "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p1.jpg"
    ) == "https://img3.doubanio.com/view/photo/s_ratio_poster/public/p1.jpg"
    # 好镜像与空值不受影响
    assert _normalize_cover("https://img1.doubanio.com/x.jpg") == "https://img1.doubanio.com/x.jpg"
    assert _normalize_cover("") is None
    assert _normalize_cover(None) is None


# --- 连接层抖动必须重试（bgm.tv 的 TLS 会被间歇掐断）-----------------------
# 背景：实测同一个 bgm.tv 封面地址连续请求三次，结果是 EXC / EXC / 200，
# 报错固定为 SSL: UNEXPECTED_EOF_WHILE_READING。镜像轮换只对豆瓣有效，
# 其它图床只有 1 个候选，一次抖动就 502 → 前端退占位 → 封面随机裂图。


class _FakeResponse:
    def __init__(self, content=b"\xff\xd8\xff-jpeg-bytes", status_code=200,
                 content_type="image/jpeg"):
        self.content = content
        self.status_code = status_code
        self.headers = {"content-type": content_type}


class _FakeClient:
    """按脚本依次返回结果的假客户端；元素是异常类就抛，否则当响应返回。"""

    def __init__(self, script):
        self.script = script

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        outcome = self.script.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def fake_upstream(monkeypatch):
    """替掉 async_client，让我们能精确编排「第几次成功」。"""

    def _install(script):
        calls = {"n": 0}

        def _factory(*args, **kwargs):
            calls["n"] += 1
            return _FakeClient(script)

        monkeypatch.setattr(images_router, "async_client", _factory)
        return calls

    return _install


BGM_URL = "https://lain.bgm.tv/pic/cover/c/16/a8/412144_39HJH.jpg"


def test_proxy_retries_after_tls_drop(client, fake_upstream):
    """前两次 TLS 被掐断、第三次成功 —— 用户必须拿到图片而不是 502。"""
    tls_error = httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred")
    calls = fake_upstream([tls_error, tls_error, _FakeResponse()])
    response = client.get("/api/v1/images/proxy", params={"url": BGM_URL})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("image/")
    assert calls["n"] == 3, "应当重试到第三次"


def test_proxy_gives_up_after_max_attempts(client, fake_upstream):
    """一直连不上就得如实报 502，不能无限重试把请求挂死。"""
    tls_error = httpx.ConnectError("EOF occurred in violation of protocol")
    calls = fake_upstream([tls_error] * 10)
    response = client.get("/api/v1/images/proxy", params={"url": BGM_URL})
    assert response.status_code == 502
    assert calls["n"] == images_router.MAX_ATTEMPTS_PER_CANDIDATE


def test_proxy_does_not_retry_http_errors(client, fake_upstream):
    """403/404 重试多少次结果都一样，白等而已——必须只试一次。"""
    calls = fake_upstream([_FakeResponse(status_code=403)] * 5)
    response = client.get("/api/v1/images/proxy", params={"url": BGM_URL})
    assert response.status_code == 502
    assert calls["n"] == 1, "HTTP 状态码错误不该重试"


def test_proxy_succeeds_first_try_without_retry(client, fake_upstream):
    """正常情况必须只发一次请求，别把重试变成常态开销。"""
    calls = fake_upstream([_FakeResponse()])
    assert client.get("/api/v1/images/proxy", params={"url": BGM_URL}).status_code == 200
    assert calls["n"] == 1


def test_bgm_host_is_whitelisted_with_own_referer():
    """bgm.tv 必须在白名单里且用自己的 Referer（豆瓣的 Referer 换不来 Bangumi 的图）。"""
    referer = resolve_referer(BGM_URL)
    assert referer is not None
    assert "bangumi" in referer
