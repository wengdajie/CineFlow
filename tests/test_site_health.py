"""站点健康探测的单元测试（不联网，用假 Provider）。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.models import SiteConfig, SiteHealthRecord
from app.db.session import session_scope
from app.providers.base import SearchProvider
from app.schemas.enums import ProviderKind, SiteHealthStatus
from app.services import site_health


class FakeSearch(SearchProvider):
    """可编程的搜索站点：想返回几条、想不想超时都能指定。"""

    kind = ProviderKind.INDEXER.value

    def __init__(self, count=1, error=None, delay=0.0):
        super().__init__({"name": "fake", "url": "http://x"})
        self._count = count
        self._error = error
        self._delay = delay

    async def search(self, keyword, **kwargs):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return [object()] * self._count

    async def health_check(self):
        return True, "ok"


class FakeDownloader:
    """非搜索类 Provider：只有 health_check。"""

    def __init__(self, ok=True, message="连通正常"):
        self._ok = ok
        self._message = message

    async def health_check(self):
        return self._ok, self._message


@pytest.fixture(scope="module")
def probe_site(client):
    """建一个专用探测站点（sites.name 唯一，所以做成 module 级）。"""
    with session_scope() as session:
        existing = session.execute(
            select(SiteConfig).where(SiteConfig.name == "健康探测测试站")
        ).scalar_one_or_none()
        if existing:
            site_id = existing.id
        else:
            site = SiteConfig(
                name="健康探测测试站",
                kind=ProviderKind.INDEXER.value,
                provider="torznab",
                url="http://127.0.0.1:1/torznab",
                enabled=True,
                priority=99,
            )
            session.add(site)
            session.flush()
            site_id = site.id
    return site_id


@pytest.fixture(autouse=True)
def _clean_records(client):
    yield
    with session_scope() as session:
        session.query(SiteHealthRecord).delete()


def run(coro):
    return asyncio.run(coro)


def patch_provider(monkeypatch, provider):
    """让 create_provider 返回我们的假实现。"""
    import app.providers.registry as registry

    monkeypatch.setattr(registry, "create_provider", lambda name, config: provider)


# ---------------- 三档状态判定 ----------------
def test_ok_when_search_returns_results(monkeypatch, probe_site):
    patch_provider(monkeypatch, FakeSearch(count=3))
    result = run(site_health.check_site(probe_site, notify=False))
    assert result["status"] == SiteHealthStatus.OK.value
    assert result["result_count"] == 3


def test_zero_results_is_degraded_not_ok(monkeypatch, probe_site):
    """0 结果是 Cookie 过期最典型的信号，绝不能判成 ok。"""
    patch_provider(monkeypatch, FakeSearch(count=0))
    result = run(site_health.check_site(probe_site, notify=False))
    assert result["status"] == SiteHealthStatus.DEGRADED.value
    assert "Cookie" in result["message"]


def test_exception_is_down(monkeypatch, probe_site):
    patch_provider(monkeypatch, FakeSearch(error=RuntimeError("连接被拒绝")))
    result = run(site_health.check_site(probe_site, notify=False))
    assert result["status"] == SiteHealthStatus.DOWN.value
    assert "连接被拒绝" in result["message"]


def test_timeout_is_down(monkeypatch, probe_site):
    monkeypatch.setattr(site_health, "PROBE_TIMEOUT", 0.01)
    patch_provider(monkeypatch, FakeSearch(count=1, delay=0.2))
    result = run(site_health.check_site(probe_site, notify=False))
    assert result["status"] == SiteHealthStatus.DOWN.value
    assert "超时" in result["message"]


def test_slow_but_working_is_degraded(monkeypatch, probe_site):
    monkeypatch.setattr(site_health, "SLOW_MS", 1)
    patch_provider(monkeypatch, FakeSearch(count=2, delay=0.02))
    result = run(site_health.check_site(probe_site, notify=False))
    assert result["status"] == SiteHealthStatus.DEGRADED.value
    assert "过慢" in result["message"]


def test_non_search_provider_uses_health_check(monkeypatch, probe_site):
    patch_provider(monkeypatch, FakeDownloader(ok=False, message="401 未授权"))
    result = run(site_health.check_site(probe_site, notify=False))
    assert result["status"] == SiteHealthStatus.DOWN.value
    assert result["message"] == "401 未授权"


def test_unknown_provider_is_down(monkeypatch, probe_site):
    patch_provider(monkeypatch, None)
    result = run(site_health.check_site(probe_site, notify=False))
    assert result["status"] == SiteHealthStatus.DOWN.value
    assert "未知 provider" in result["message"]


def test_missing_site_returns_message():
    result = run(site_health.check_site(99999, notify=False))
    assert result["success"] is False
    assert result["message"] == "站点不存在"


# ---------------- 告警节流与状态翻转 ----------------
def test_alert_only_after_threshold(monkeypatch, probe_site):
    """单次波动不打扰用户：连续达到阈值才告警。"""
    monkeypatch.setattr(settings, "SITE_HEALTH_FAIL_THRESHOLD", 3)
    patch_provider(monkeypatch, FakeSearch(count=0))
    alerts = [run(site_health.check_site(probe_site, notify=False))["alert"] for _ in range(3)]
    assert alerts == [None, None, "unhealthy"]


def test_recovery_alert_on_flip(monkeypatch, probe_site):
    monkeypatch.setattr(settings, "SITE_HEALTH_FAIL_THRESHOLD", 1)
    patch_provider(monkeypatch, FakeSearch(count=0))
    assert run(site_health.check_site(probe_site, notify=False))["alert"] == "unhealthy"
    patch_provider(monkeypatch, FakeSearch(count=5))
    assert run(site_health.check_site(probe_site, notify=False))["alert"] == "recovered"
    # 已经恢复后再成功一次不该重复通知
    assert run(site_health.check_site(probe_site, notify=False))["alert"] is None


def test_auto_disable_when_enabled(monkeypatch, probe_site):
    monkeypatch.setattr(settings, "SITE_HEALTH_FAIL_THRESHOLD", 1)
    monkeypatch.setattr(settings, "SITE_AUTO_DISABLE", True)
    patch_provider(monkeypatch, FakeSearch(error=RuntimeError("boom")))
    run(site_health.check_site(probe_site, notify=False))
    with session_scope() as session:
        site = session.get(SiteConfig, probe_site)
        assert site.enabled is False
        site.enabled = True   # 还原，别影响后面的用例


def test_auto_disable_off_by_default(monkeypatch, probe_site):
    monkeypatch.setattr(settings, "SITE_HEALTH_FAIL_THRESHOLD", 1)
    monkeypatch.setattr(settings, "SITE_AUTO_DISABLE", False)
    patch_provider(monkeypatch, FakeSearch(error=RuntimeError("boom")))
    run(site_health.check_site(probe_site, notify=False))
    with session_scope() as session:
        assert session.get(SiteConfig, probe_site).enabled is True


# ---------------- 记录与查询 ----------------
def test_history_is_trimmed(monkeypatch, probe_site):
    monkeypatch.setattr(site_health, "KEEP_PER_SITE", 3)
    patch_provider(monkeypatch, FakeSearch(count=1))
    for _ in range(6):
        run(site_health.check_site(probe_site, notify=False))
    records = site_health.list_records(site_name="健康探测测试站", limit=100)
    assert len(records) == 3, "历史记录必须裁剪，否则会无限膨胀"


def test_overview_and_unhealthy_sites(monkeypatch, probe_site):
    patch_provider(monkeypatch, FakeSearch(count=0))
    run(site_health.check_site(probe_site, notify=False))
    data = site_health.overview()
    assert data["success"] is True
    assert data["counts"]["degraded"] >= 1
    row = next(item for item in data["items"] if item["site_id"] == probe_site)
    assert row["status"] == SiteHealthStatus.DEGRADED.value
    assert "健康探测测试站" in site_health.unhealthy_sites()


def test_overview_marks_never_probed_as_unknown(client):
    """从没探测过的站点必须显示"未探测"，而不是假装正常。"""
    data = site_health.overview()
    assert data["counts"].get("unknown", 0) >= 1
    assert any(item["message"] == "尚未探测" for item in data["items"])


def test_downloader_health_map(client):
    health = site_health.downloader_health()
    assert isinstance(health, dict)
    assert all(value in ("ok", "degraded", "down", "unknown") for value in health.values())


def test_check_all_skips_disabled_sites(monkeypatch, client):
    """禁用站点不参与业务，探它纯属浪费请求。"""
    patch_provider(monkeypatch, FakeSearch(count=1))
    result = run(site_health.check_all(notify=False))
    with session_scope() as session:
        enabled = session.execute(
            select(SiteConfig).where(SiteConfig.enabled.is_(True))
        ).scalars().all()
        expected = len(enabled)
    assert result["checked"] == expected


# ---------------- API ----------------
def test_site_health_api(client, auth_headers, probe_site):
    response = client.get("/api/v1/site-health", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert "counts" in body and "items" in body
    assert body["fail_threshold"] >= 1

    records = client.get("/api/v1/site-health/records?limit=5", headers=auth_headers)
    assert records.status_code == 200

    missing = client.post("/api/v1/site-health/check/99999", headers=auth_headers)
    assert missing.status_code == 404


def test_site_health_requires_auth(client):
    assert client.get("/api/v1/site-health").status_code == 401
