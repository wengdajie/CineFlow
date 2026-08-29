"""图片代理测试：SSRF 防护与白名单（离线）。"""

from __future__ import annotations

from app.api.routers.images import resolve_referer


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
