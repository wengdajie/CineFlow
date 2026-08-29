"""图片代理测试：SSRF 防护与白名单（离线）。"""

from __future__ import annotations

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
