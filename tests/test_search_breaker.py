"""搜索熔断器 + 下载「真实结局」回归（v1.15.0）。

本轮用户反馈：「站点资源搜索以及下载依然有问题」。用实测数据定位到三个真缺陷：

**① 投递失败仍报成功**（最误导的一个）。`POST /downloads` 无论结局都回
`{"success": true}`，前端于是弹绿色的「已加入下载队列」，而任务列表里
其实是一条红色 failed。实测（qBittorrent 未启动）：

    magnet   http=200 success=True status=failed  error=下载器投递失败 → qBittorrent: 拒绝或超时

**② 前置检查只看「配没配」，不看「连不连得上」**。`/downloads/routing`
对指向 127.0.0.1:8080 的示例 qBittorrent 报 `ready=true`，
用户点了才拿到失败 —— 而这个信息本来在站点健康巡检里已经有了。

**③ 一个连不通的站决定所有人的等待时间**。实测 8 站搜索共 25.4s，其中
YouTube 吃满整个 25s 预算返回 0 条，其余站 5s 内全部返回。
`asyncio.gather` 要等最慢的那个，所以慢站不是「多等它一会」而是「大家一起等」。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.providers.base import Resource
from app.schemas.enums import ResourceKind, SiteHealthStatus, TaskStatus
from app.services import download_routing, search, search_breaker


@pytest.fixture(autouse=True)
def _clean_breaker():
    """每个用例都从干净的熔断表开始——它是进程级全局状态，会串味。"""
    search_breaker.reset()
    yield
    search_breaker.reset()


@pytest.fixture
def breaker_cfg(monkeypatch):
    """把阈值/冷却调成可测的小值。"""
    def _apply(threshold=2, cooldown=10, enabled=True, timeout=2):
        monkeypatch.setattr(settings, "SEARCH_BREAKER_ENABLED", enabled)
        monkeypatch.setattr(settings, "SEARCH_BREAKER_THRESHOLD", threshold)
        monkeypatch.setattr(settings, "SEARCH_BREAKER_COOLDOWN_MINUTES", cooldown)
        monkeypatch.setattr(settings, "SEARCH_TIMEOUT", timeout)
    return _apply


class _Dead:
    """永远不返回的站点：模拟连不通但 TCP 不立刻拒绝的情况（最难缠的一类）。"""

    site_name = "卡死站"

    def __init__(self):
        self.calls = 0

    async def search(self, keyword, **kwargs):
        self.calls += 1
        await asyncio.sleep(600)


class _Fast:
    site_name = "健康站"

    def __init__(self):
        self.calls = 0

    async def search(self, keyword, **kwargs):
        self.calls += 1
        return [
            Resource(
                title="某剧 S01E01 1080p",
                link="magnet:?xt=urn:btih:" + "b" * 40,
                site=self.site_name,
                kind=ResourceKind.MAGNET.value,
            )
        ]


class _Empty:
    """快速返回空：冷门片的正常表现，**绝不能**被熔断误伤。"""

    site_name = "空结果站"

    def __init__(self):
        self.calls = 0

    async def search(self, keyword, **kwargs):
        self.calls += 1
        return []


def _run(providers):
    return asyncio.run(
        search.search_detailed("某剧", providers=providers, save_history=False)
    )


class TestBreakerTripsOnTimeout:
    def test_连续吃满预算后被跳过且不再发请求(self, breaker_cfg):
        """核心诉求：慢站熔断后，搜索耗时立刻回到健康站的水平。"""
        breaker_cfg(threshold=2)
        dead, fast = _Dead(), _Fast()
        for _ in range(2):
            _run([dead, fast])
        assert dead.calls == 2

        _, outcomes = _run([dead, fast])
        by_site = {o.site: o for o in outcomes}
        assert by_site["卡死站"].status == "skipped"
        # 关键：跳过意味着**没有再去发请求**，否则等于没熔断
        assert dead.calls == 2
        # 健康站不受影响，结果照常
        assert by_site["健康站"].status == "ok"

    def test_跳过必须说明原因和恢复时间(self, breaker_cfg):
        """静默变少是 ADR-20 那类最难排查的故障，必须留下可读的痕迹。"""
        breaker_cfg(threshold=1)
        dead = _Dead()
        _run([dead, _Fast()])
        _, outcomes = _run([dead, _Fast()])
        msg = next(o for o in outcomes if o.site == "卡死站").message
        assert "跳过" in msg
        assert "分钟" in msg  # 必须告诉用户多久后自动重试

    def test_熔断不影响其他站点的结果(self, breaker_cfg):
        breaker_cfg(threshold=1)
        dead, fast = _Dead(), _Fast()
        _run([dead, fast])
        ranked, _ = _run([dead, fast])
        assert len(ranked) == 1
        assert ranked[0]["site"] == "健康站"


class TestBreakerDoesNotMisfire:
    def test_快速返回空的站不熔断(self, breaker_cfg):
        """冷门片搜不到是正常的，把这种站熔断会把好站也弄丢。"""
        breaker_cfg(threshold=1)
        empty = _Empty()
        for _ in range(5):
            _run([empty, _Fast()])
        assert not search_breaker.is_open("空结果站")
        assert empty.calls == 5

    def test_有结果的慢站不熔断(self, breaker_cfg, monkeypatch):
        """盘搜就是慢但有用，只按「慢」熔断会把最有价值的站剔掉。"""
        breaker_cfg(threshold=1, timeout=3)

        class SlowButGood:
            site_name = "慢而有用的站"

            async def search(self, keyword, **kwargs):
                await asyncio.sleep(0.2)
                return [
                    Resource(
                        title="某剧 S01E01 1080p",
                        link="magnet:?xt=urn:btih:" + "c" * 40,
                        site=self.site_name,
                        kind=ResourceKind.MAGNET.value,
                    )
                ]

        for _ in range(4):
            _run([SlowButGood()])
        assert not search_breaker.is_open("慢而有用的站")

    def test_命中一次就清零计数(self, breaker_cfg):
        """站点恢复了就该立刻恢复信任，不能让旧账把它拖进熔断。"""
        breaker_cfg(threshold=3)
        dead, fast = _Dead(), _Fast()
        _run([dead, fast])
        assert search_breaker.snapshot()[0]["strikes"] == 1
        search_breaker.record_success("卡死站")
        assert search_breaker.snapshot()[0]["strikes"] == 0
        assert not search_breaker.is_open("卡死站")

    def test_站点恢复后计数经搜索链路清零(self, breaker_cfg):
        """覆盖 search.py 里「命中即 record_success」这行接线。

        原先只直接调 record_success()，那行被删掉测试照样绿 ——
        真实场景是「站点先超时几次、后来恢复」，必须走完整搜索链路才测得到。
        """
        breaker_cfg(threshold=3)
        flaky = _Dead()
        _run([flaky, _Fast()])
        assert next(
            r for r in search_breaker.snapshot() if r["site"] == "卡死站"
        )["strikes"] == 1

        # 同名站点这次正常返回结果 → 计数必须被清零
        class Recovered(_Fast):
            site_name = "卡死站"

        _run([Recovered()])
        row = next(
            (r for r in search_breaker.snapshot() if r["site"] == "卡死站"), None
        )
        assert row is None or row["strikes"] == 0

    def test_开关关闭时完全不熔断(self, breaker_cfg):
        breaker_cfg(threshold=1, enabled=False)
        dead = _Dead()
        for _ in range(3):
            _run([dead, _Fast()])
        assert dead.calls == 3
        assert not search_breaker.is_open("卡死站")

    def test_冷却设为0时只计数不跳过(self, breaker_cfg):
        """留一个「只观察不干预」档位，便于用户先看清再决定。

        断言 ``trips == 0`` 而不只是 ``not is_open``：若把 `cooldown<=0` 的早退
        去掉，``open_until`` 会被设成「此刻」，``is_open`` 立刻判过期并复位，
        表面行为完全一样 —— 只有 trips 能证明它真的没熔断过。
        """
        breaker_cfg(threshold=1, cooldown=0)
        dead = _Dead()
        _run([dead, _Fast()])
        _run([dead, _Fast()])
        assert dead.calls == 2
        assert not search_breaker.is_open("卡死站")
        row = next(r for r in search_breaker.snapshot() if r["site"] == "卡死站")
        assert row["trips"] == 0, "cooldown=0 不应该真的熔断"
        assert row["strikes"] >= 2, "但仍要如实计数，供用户观察"


class TestBreakerRecovery:
    def test_冷却到期自动恢复并重新计数(self, breaker_cfg, monkeypatch):
        breaker_cfg(threshold=1, cooldown=10)
        dead = _Dead()
        _run([dead, _Fast()])
        assert search_breaker.is_open("卡死站")

        # 把时钟推过冷却期，而不是真的 sleep 10 分钟
        base = search_breaker._now()
        monkeypatch.setattr(search_breaker, "_now", lambda: base + 601)
        assert not search_breaker.is_open("卡死站")
        # 半开：恢复后从零开始，给一次完整机会
        assert search_breaker.snapshot()[0]["strikes"] == 0

    def test_手动解除熔断(self, breaker_cfg):
        """用户刚改完地址/代理，不该还要干等冷却结束。"""
        breaker_cfg(threshold=1)
        _run([_Dead(), _Fast()])
        assert search_breaker.is_open("卡死站")
        assert search_breaker.reset("卡死站") == 1
        assert not search_breaker.is_open("卡死站")

    def test_reset全部返回清理条数(self, breaker_cfg):
        breaker_cfg(threshold=1)
        _run([_Dead(), _Fast()])
        assert search_breaker.reset() >= 1
        assert search_breaker.snapshot() == []


class TestBreakerApi:
    def test_熔断状态接口(self, client, auth_headers, breaker_cfg):
        breaker_cfg(threshold=1)
        _run([_Dead(), _Fast()])
        res = client.get("/api/v1/search/breaker", headers=auth_headers)
        assert res.status_code == 200
        body = res.json()
        assert body["enabled"] is True
        row = next(r for r in body["items"] if r["site"] == "卡死站")
        assert row["open"] is True
        assert row["remaining_seconds"] > 0

    def test_解除熔断接口(self, client, auth_headers, breaker_cfg):
        breaker_cfg(threshold=1)
        _run([_Dead(), _Fast()])
        res = client.post(
            "/api/v1/search/breaker/reset?site=" + "卡死站", headers=auth_headers
        )
        assert res.status_code == 200
        assert res.json()["cleared"] == 1
        assert not search_breaker.is_open("卡死站")

    def test_未登录不能查看熔断状态(self, client):
        """熔断状态会暴露站点名，属于要登录才能看的信息。"""
        res = client.get("/api/v1/search/breaker")
        assert res.status_code in (401, 403)


class TestDownloadReportsRealOutcome:
    """① 投递失败不能再报 success:true。"""

    def test_投递失败时success为false且带可行动原因(self, client, auth_headers, monkeypatch):
        from app.db.models import DownloadTask

        async def fake_add(*args, **kwargs):
            return DownloadTask(
                id=99,
                title="某剧",
                kind=ResourceKind.MAGNET.value,
                status=TaskStatus.FAILED.value,
                error="下载器投递失败 → qBittorrent: 拒绝或超时",
            )

        monkeypatch.setattr(
            "app.api.routers.downloads.download_service.add_download", fake_add
        )
        res = client.post(
            "/api/v1/downloads",
            headers=auth_headers,
            json={
                "title": "某剧",
                "link": "magnet:?xt=urn:btih:" + "a" * 40,
                "kind": ResourceKind.MAGNET.value,
            },
        )
        assert res.status_code == 200
        body = res.json()
        # 修复前这里是 True —— 界面于是弹绿色「已加入下载队列」
        assert body["success"] is False
        assert body["status"] == TaskStatus.FAILED.value
        assert "qBittorrent" in body["message"]

    def test_投递成功仍然是success为true(self, client, auth_headers, monkeypatch):
        from app.db.models import DownloadTask

        async def fake_add(*args, **kwargs):
            return DownloadTask(
                id=98,
                title="某剧",
                kind=ResourceKind.MAGNET.value,
                status=TaskStatus.DOWNLOADING.value,
                downloader="qBittorrent",
            )

        monkeypatch.setattr(
            "app.api.routers.downloads.download_service.add_download", fake_add
        )
        res = client.post(
            "/api/v1/downloads",
            headers=auth_headers,
            json={
                "title": "某剧",
                "link": "magnet:?xt=urn:btih:" + "a" * 40,
                "kind": ResourceKind.MAGNET.value,
            },
        )
        body = res.json()
        assert body["success"] is True
        assert body["downloader"] == "qBittorrent"
        assert "message" not in body or not body["message"]

    def test_网盘停在pending时给出下一步提示(self, client, auth_headers, monkeypatch):
        """pending 不是失败，但也没真开始下，不提示用户会一直干等。"""
        from app.db.models import DownloadTask

        async def fake_add(*args, **kwargs):
            return DownloadTask(
                id=97,
                title="某网盘资源",
                kind=ResourceKind.PAN.value,
                status=TaskStatus.PENDING.value,
            )

        monkeypatch.setattr(
            "app.api.routers.downloads.download_service.add_download", fake_add
        )
        monkeypatch.setattr(
            download_routing, "pan_pending_hint", lambda: "请先登录网盘或添加 aria2"
        )
        res = client.post(
            "/api/v1/downloads",
            headers=auth_headers,
            json={
                "title": "某网盘资源",
                "link": "https://pan.quark.cn/s/abc",
                "kind": ResourceKind.PAN.value,
            },
        )
        body = res.json()
        assert body["success"] is True          # 不是失败
        assert body["status"] == TaskStatus.PENDING.value
        assert "aria2" in body["message"]       # 但必须告诉用户还差什么


class TestRoutingChecksReachability:
    """② 前置检查要看「连不连得上」，不只看「配没配」。"""

    def test_下载器被巡检判定down时不再报ready(self, client, monkeypatch):
        monkeypatch.setattr(
            download_routing,
            "downloader_reachability",
            lambda: {"qBittorrent": SiteHealthStatus.DOWN.value},
        )

        class _Fake:
            site_name = "qBittorrent"

            def accepts(self, kind):
                return True

        monkeypatch.setattr(
            download_routing, "candidates_for", lambda kind, prefer=None: [_Fake()]
        )
        ready, reason = download_routing.check(ResourceKind.MAGNET.value)
        assert ready is False
        assert "连不上" in reason
        assert "qBittorrent" in reason

    def test_判据必须是down而不是error(self, monkeypatch):
        """写死 "error" 会永远匹配不上（第一版真踩过），这条钉住枚举值。"""
        monkeypatch.setattr(
            download_routing, "downloader_reachability", lambda: {"qB": "down"}
        )
        assert download_routing._unreachable(["qB"]) == ["qB"]

    def test_degraded不算连不上(self, monkeypatch):
        """能连通但结果异常（0 结果/极慢）不该拦住下载。"""
        monkeypatch.setattr(
            download_routing,
            "downloader_reachability",
            lambda: {"qB": SiteHealthStatus.DEGRADED.value},
        )
        assert download_routing._unreachable(["qB"]) == []

    def test_没巡检过时不拦下载(self, monkeypatch):
        """没有证据说明它坏，就不能凭猜测拦住用户。"""
        monkeypatch.setattr(download_routing, "downloader_reachability", lambda: {})
        assert download_routing._unreachable(["qB"]) == []

    def test_只要还有一个能连上就照常投递(self, monkeypatch):
        """多下载器时部分挂掉应自动换源，而不是整体报不可用。"""
        monkeypatch.setattr(
            download_routing,
            "downloader_reachability",
            lambda: {"qB": SiteHealthStatus.DOWN.value, "tr": SiteHealthStatus.OK.value},
        )

        class _F:
            def __init__(self, name):
                self.site_name = name

            def accepts(self, kind):
                return True

        monkeypatch.setattr(
            download_routing,
            "candidates_for",
            lambda kind, prefer=None: [_F("qB"), _F("tr")],
        )
        ready, _ = download_routing.check(ResourceKind.MAGNET.value)
        assert ready is True

    def test_健康数据异常不影响下载判断(self, monkeypatch):
        """巡检模块炸了不能连带把下载功能废掉。"""
        def _boom():
            raise RuntimeError("db gone")

        monkeypatch.setattr("app.services.site_health.downloader_health", _boom)
        assert download_routing.downloader_reachability() == {}

    def test_routing接口带出unreachable字段(self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(
            download_routing,
            "downloader_reachability",
            lambda: {"qBittorrent": SiteHealthStatus.DOWN.value},
        )
        res = client.get("/api/v1/downloads/routing", headers=auth_headers)
        assert res.status_code == 200
        for item in res.json()["items"]:
            assert "unreachable" in item
