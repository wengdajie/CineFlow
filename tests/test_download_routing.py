"""资源类型 → 下载方式的路由（v1.13.0 需求 2 回归）。

背景（用硬证据复现的真缺陷）：投递逻辑原先写死"没指定就拿默认下载器"，
完全不看这个下载器收不收这种资源。只启用 yt-dlp 时实测：

    磁力 magnet:?xt=urn:btih:aaa… → downloader=yt-dlp / status=downloading

yt-dlp 根本下不了磁力，任务却被标成"正在下载"，用户得等它烂在队列里
才发现不对。反过来，视频网页投给 qBittorrent 会下到一个几 KB 的 HTML。

所以要求：**不同资源对应不同下载方式，缺对应下载器时给出可行动的提示**
（说清去哪儿加什么），而不是静默失败或投给一个必然失败的下载器。
"""

import asyncio

import pytest
from sqlalchemy import select

from app.db.models import SiteConfig
from app.db.session import session_scope
from app.schemas.enums import ProviderKind, ResourceKind, TaskStatus
from app.services import download as download_service
from app.services import download_routing


@pytest.fixture
def only_downloader(client):
    """只启用某一个下载器，用完还原。模拟「用户只装了 X」。

    必须先把【已有的】下载器全部置为禁用：初始化会写入示例站点，
    其中 yt-dlp 默认就是启用的，不先关掉就测不出「只装了 qB」这种情形，
    测试会假绿（这一步真实抳到过两个假绿用例）。
    """
    created: list[int] = []
    disabled: list[int] = []

    with session_scope() as session:
        rows = session.execute(
            select(SiteConfig).where(
                SiteConfig.kind == ProviderKind.DOWNLOADER.value,
                SiteConfig.enabled.is_(True),
            )
        ).scalars().all()
        for row in rows:
            row.enabled = False
            disabled.append(row.id)

    def _make(provider: str, name: str | None = None):
        with session_scope() as session:
            site = SiteConfig(
                name=name or f"测试-{provider}",
                kind=ProviderKind.DOWNLOADER.value,
                provider=provider,
                enabled=True,
                priority=1,
                options={},
            )
            session.add(site)
            session.flush()
            created.append(site.id)
        return provider

    yield _make

    with session_scope() as session:
        for site_id in created:
            site = session.get(SiteConfig, site_id)
            if site:
                session.delete(site)
        for site_id in disabled:
            site = session.get(SiteConfig, site_id)
            if site:
                site.enabled = True


class TestSupportedKinds:
    """下载器必须如实声明自己收哪些资源类型。"""

    def test_ytdlp_只收视频网页(self):
        from app.providers.downloader.ytdlp import YtDlpDownloader

        provider = YtDlpDownloader({})
        assert provider.accepts(ResourceKind.WEBVIDEO.value)
        # 这两条就是线上把磁力投给 yt-dlp 的根因
        assert not provider.accepts(ResourceKind.MAGNET.value)
        assert not provider.accepts(ResourceKind.TORRENT.value)

    def test_qb_与_tr_只收bt(self):
        from app.providers.downloader.qbittorrent import QbittorrentDownloader
        from app.providers.downloader.transmission import TransmissionDownloader

        for cls in (QbittorrentDownloader, TransmissionDownloader):
            provider = cls({})
            assert provider.accepts(ResourceKind.MAGNET.value), cls
            assert provider.accepts(ResourceKind.TORRENT.value), cls
            # BT 下载器拿到网页地址只会下到一个 HTML 文件
            assert not provider.accepts(ResourceKind.WEBVIDEO.value), cls

    def test_aria2_收直链和bt(self):
        from app.providers.downloader.aria2 import Aria2Downloader

        provider = Aria2Downloader({})
        assert provider.accepts(ResourceKind.DIRECT.value)
        assert provider.accepts(ResourceKind.MAGNET.value)
        assert not provider.accepts(ResourceKind.WEBVIDEO.value)


class TestRoutingHints:
    def test_每种类型都有可行动的提示(self):
        """提示必须说清"去哪儿加什么"，只说"未配置下载器"等于没说。"""
        for kind in (
            ResourceKind.MAGNET.value,
            ResourceKind.TORRENT.value,
            ResourceKind.DIRECT.value,
            ResourceKind.WEBVIDEO.value,
            ResourceKind.PAN.value,
        ):
            hint = download_routing.hint_of(kind)
            assert hint, kind
            # 必须指出去哪儿操作，而不是只报一句"没配"
            assert "设置" in hint or "网盘管理" in hint, (kind, hint)

    def test_网盘提示同时覆盖转存与本地下载两条去处(self):
        hint = download_routing.hint_of(ResourceKind.PAN.value)
        assert "aria2" in hint
        assert "转存" in hint

    def test_未知类型按种子兜底而不是崩掉(self):
        assert download_routing.label_of("没见过的类型")
        assert download_routing.hint_of("") == download_routing.hint_of(
            ResourceKind.TORRENT.value
        )

    def test_describe_覆盖全部类型(self, client):
        items = download_routing.describe()["items"]
        kinds = {item["kind"] for item in items}
        assert kinds == {"magnet", "torrent", "pan", "direct", "webvideo"}
        for item in items:
            assert "ready" in item and "hint" in item
            if not item["ready"]:
                assert item["reason"], item


class TestCandidatesFiltering:
    def test_只装ytdlp时磁力筛不出候选(self, client, only_downloader):
        only_downloader("ytdlp")
        assert download_routing.candidates_for(ResourceKind.MAGNET.value) == []
        # 但它自己的类型必须能筛出来，否则等于把功能一起砍了
        assert download_routing.candidates_for(ResourceKind.WEBVIDEO.value)

    def test_只装qb时视频网页筛不出候选(self, client, only_downloader):
        only_downloader("qbittorrent")
        assert download_routing.candidates_for(ResourceKind.WEBVIDEO.value) == []
        assert download_routing.candidates_for(ResourceKind.MAGNET.value)

    def test_网盘按直链能力找下载器(self, client, only_downloader):
        """网盘落地本地投的是**换出来的直链**，所以要看谁能收 direct。"""
        only_downloader("qbittorrent")
        # qB 不收直链 → 网盘下不到本地
        assert download_routing.candidates_for(ResourceKind.PAN.value) == []

    def test_装了aria2则网盘有下载候选(self, client, only_downloader):
        only_downloader("aria2")
        assert download_routing.candidates_for(ResourceKind.PAN.value)


class TestAddDownloadRouting:
    def test_磁力不再被投给ytdlp(self, client, only_downloader):
        """核心回归：修复前 status=downloading / downloader=yt-dlp。"""
        only_downloader("ytdlp")
        task = asyncio.run(
            download_service.add_download(
                {
                    "title": "某剧 S01E01 1080p",
                    "link": "magnet:?xt=urn:btih:" + "a" * 40,
                    "kind": ResourceKind.MAGNET.value,
                    "site": "测试站",
                },
                notify=False,
            )
        )
        assert task is not None
        assert task.downloader is None, task.downloader
        assert task.status == TaskStatus.FAILED.value
        # 失败原因必须能指导用户下一步
        assert "下载器" in (task.error or "")
        assert "设置" in (task.error or "")

    def test_视频网页缺ytdlp时给出可行动提示(self, client, only_downloader):
        only_downloader("qbittorrent")
        task = asyncio.run(
            download_service.add_download(
                {
                    "title": "某个视频",
                    "link": "https://www.bilibili.com/video/BV1xx411c7mD",
                    "kind": ResourceKind.WEBVIDEO.value,
                    "site": "Bilibili",
                },
                notify=False,
            )
        )
        assert task is not None
        assert task.status == TaskStatus.FAILED.value
        assert "yt-dlp" in (task.error or "")

    def test_直链缺aria2时不投给qb(self, client, only_downloader):
        only_downloader("qbittorrent")
        task = asyncio.run(
            download_service.add_download(
                {
                    "title": "某个直链",
                    "link": "https://example.com/a.mkv",
                    "kind": ResourceKind.DIRECT.value,
                    "site": "测试站",
                },
                notify=False,
            )
        )
        assert task is not None
        assert task.downloader is None
        assert task.status == TaskStatus.FAILED.value
        assert "aria2" in (task.error or "")

    def test_网盘任务不再空着pending(self, client, only_downloader):
        """既没网盘账号也没 aria2 时，任务永远走不完，必须写清下一步。"""
        only_downloader("qbittorrent")
        task = asyncio.run(
            download_service.add_download(
                {
                    "title": "某网盘资源",
                    "link": "https://pan.quark.cn/s/abcdef123456",
                    "kind": ResourceKind.PAN.value,
                    "site": "PanSou",
                },
                notify=False,
            )
        )
        assert task is not None
        assert task.status == TaskStatus.PENDING.value
        assert task.error, "pending 却没有任何说明，界面上就是一片空白"
        assert "网盘管理" in task.error


class TestRoutingEndpoint:
    def test_接口下发各类型可用状态(self, client, auth_headers):
        response = client.get("/api/v1/downloads/routing", headers=auth_headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["success"] is True
        assert len(data["items"]) == 5
        for item in data["items"]:
            assert set(item) >= {"kind", "label", "ready", "reason", "hint", "downloaders"}

    def test_接口需要登录(self, client):
        assert client.get("/api/v1/downloads/routing").status_code == 401

# --------------------------------------------------------------------------
# 网盘资源落地到本地：转存 → 换直链 → 投 aria2（v1.25.0 补上缺失的第三段）
# --------------------------------------------------------------------------


class _FakeStorage:
    """假网盘：转存成功并给出落地路径与直链 UA 要求。"""

    site_name = "假百度"
    direct_link_user_agent = "netdisk;P2SP;3.0.20.11"

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def download_url(self, path, *, file_id=None):
        self.asked.append(path)
        return "https://d.pcs.baidu.com/file/abc?fid=1"


class _RecordingDownloader:
    """假下载器：记录 add() 收到的全部参数。"""

    site_name = "假 aria2"

    def __init__(self, accept: bool = True) -> None:
        self.calls: list[dict] = []
        self.accept = accept

    def accepts(self, kind: str) -> bool:
        return True

    async def add(self, link, *, save_path=None, category=None, paused=False,
                  cookie=None, user_agent=None):
        self.calls.append(
            {"link": link, "save_path": save_path, "user_agent": user_agent}
        )
        return "gid-42" if self.accept else None


def test_pan_landing_sends_direct_link_with_required_ua(monkeypatch):
    """转存成功后必须换直链投给下载器，并带上网盘要求的 UA。

    这一段以前**完全不存在**：`_ROUTES` 的注释与 `pan_pending_hint()` 都在讲
    「转存 → 换直链 → 投 aria2」，但代码里只有前一半，任务停在 `transferred`
    就再也不动了（实测用户报「网盘资源下载」无效）。
    UA 尤其关键：百度 CDN 直链换掉 netdisk 系 UA 后大文件必然 403。
    """
    storage = _FakeStorage()
    downloader = _RecordingDownloader()
    from app.services import pan_storage as pan_service

    monkeypatch.setattr(pan_service, "get_storage", lambda site_id: storage)
    monkeypatch.setattr(
        download_routing, "candidates_for", lambda kind, prefer=None: [downloader]
    )

    landed = asyncio.run(
        download_service._land_pan_to_local(
            {"success": True, "saved_path": "/来自CineFlow/片子.mkv", "site_id": 7},
            "K:/media/movies",
            None,
        )
    )
    assert landed.get("success"), f"落地失败：{landed}"
    assert landed["external_id"] == "gid-42"
    assert storage.asked == ["/来自CineFlow/片子.mkv"]
    assert downloader.calls, "根本没调用下载器"
    call = downloader.calls[0]
    assert call["link"].startswith("https://d.pcs.baidu.com/")
    assert call["user_agent"] == "netdisk;P2SP;3.0.20.11", (
        f"直链的 UA 没带上，大文件会 403：{call}"
    )
    assert call["save_path"] == "K:/media/movies"


def test_pan_landing_without_downloader_is_skipped_not_failed(monkeypatch):
    """没有 aria2 时算「跳过」而不是「失败」。

    文件确实已经在用户自己的云端了，报失败会让人以为转存也没成。
    但也不能报成功——调用方要能区分这三态（ADR-82）。
    """
    monkeypatch.setattr(download_routing, "candidates_for", lambda kind, prefer=None: [])
    landed = asyncio.run(
        download_service._land_pan_to_local(
            {"success": True, "saved_path": "/x/y.mkv", "site_id": 7},
            "K:/media/movies",
            None,
        )
    )
    assert landed.get("skipped") is True, f"应当是跳过：{landed}"
    assert not landed.get("success")


def test_pan_landing_reports_reason_when_delivery_fails(monkeypatch):
    """投递失败要带出**哪个下载器为什么**失败，不能只说"失败"。"""
    storage = _FakeStorage()
    downloader = _RecordingDownloader(accept=False)
    from app.services import pan_storage as pan_service

    monkeypatch.setattr(pan_service, "get_storage", lambda site_id: storage)
    monkeypatch.setattr(
        download_routing, "candidates_for", lambda kind, prefer=None: [downloader]
    )
    landed = asyncio.run(
        download_service._land_pan_to_local(
            {"success": True, "saved_path": "/x/y.mkv", "site_id": 7},
            "K:/media/movies",
            None,
        )
    )
    assert landed.get("success") is False
    assert "假 aria2" in landed["message"], f"没说清是哪个下载器：{landed}"


def test_pan_landing_without_saved_path_says_which_step_is_missing(monkeypatch):
    """转存报成功却没给落地路径时，要说清缺的是哪一步。"""
    landed = asyncio.run(
        download_service._land_pan_to_local(
            {"success": True, "saved_path": ""}, "K:/media/movies", None
        )
    )
    assert landed.get("success") is False
    assert "落地路径" in landed["message"], landed


def test_resolve_download_url_carries_required_user_agent(monkeypatch):
    """resolve_download_url 必须把 UA 要求一起返回。

    只给 URL 不给 UA，等于把 403 推迟到下载器内部发生 —— 而那里的失败
    本项目日志里看不到。
    """
    from app.services import pan_storage as pan_service

    monkeypatch.setattr(pan_service, "get_storage", lambda site_id: _FakeStorage())
    result = asyncio.run(pan_service.resolve_download_url(7, "/x/y.mkv"))
    assert result["success"] is True
    assert result["user_agent"] == "netdisk;P2SP;3.0.20.11", result


def test_baidu_storages_declare_direct_link_ua():
    """两个百度 Provider 都要声明直链 UA；别的网盘不该被凭空加上。"""
    from app.providers.panstorage.baidu import BaiduPanStorage
    from app.providers.panstorage.baidu_openapi import BaiduOpenApiStorage
    from app.providers.panstorage.local_dir import LocalDirStorage

    assert BaiduPanStorage.direct_link_user_agent
    assert "netdisk" in BaiduPanStorage.direct_link_user_agent
    assert BaiduOpenApiStorage.direct_link_user_agent
    assert "netdisk" in BaiduOpenApiStorage.direct_link_user_agent
    # 反向担保：本地目录没有 UA 要求，不能被顺手塞一个
    assert LocalDirStorage.direct_link_user_agent is None

def test_add_download_pan_actually_calls_landing(monkeypatch, client):
    """`add_download` 处理网盘资源时必须真的走落地那一段。

    注入验证抓到的假绿：把 `_land_pan_to_local(...)` 整个短路掉
    （回到 v1.24.0「只转存不落地」的行为）时，单测全绿 ——
    因为它们只直接测 `_land_pan_to_local`，**没人测调用点**。
    这就是「一个函数写好了 ≠ 它被接进链路」。
    """
    calls: list[dict] = []

    async def fake_save(link, resource):
        return {
            "success": True,
            "storage": "假百度",
            "site_id": 7,
            "saved_path": "/来自CineFlow/片子.mkv",
        }

    async def fake_land(pan_result, save_path, downloader_name):
        calls.append({"pan_result": pan_result, "save_path": save_path})
        return {"skipped": True}

    monkeypatch.setattr(download_service, "_try_auto_save_pan", fake_save)
    monkeypatch.setattr(download_service, "_land_pan_to_local", fake_land)

    task = asyncio.run(
        download_service.add_download(
            {
                "title": "某电影 (2020)",
                "link": "https://pan.baidu.com/s/1abcdefg",
                "kind": ResourceKind.PAN.value,
            },
            notify=False,
        )
    )
    assert task is not None
    assert calls, "转存成功后没有尝试落地到本地——第三段又断了"
    assert calls[0]["pan_result"]["saved_path"] == "/来自CineFlow/片子.mkv"


def test_add_download_pan_landing_failure_keeps_transferred_but_reports(monkeypatch, client):
    """落地失败时：状态保持 transferred（云端确实存下了），但 error 要写清原因。

    既不能报成整体失败（会让人以为转存也没成），也不能装作没事
    （用户等不到本地文件却看不到任何提示）。
    """

    async def fake_save(link, resource):
        return {"success": True, "storage": "假百度", "site_id": 7, "saved_path": "/x.mkv"}

    async def fake_land(pan_result, save_path, downloader_name):
        return {"success": False, "message": "直链投递失败 → 假 aria2: 拒绝或超时"}

    monkeypatch.setattr(download_service, "_try_auto_save_pan", fake_save)
    monkeypatch.setattr(download_service, "_land_pan_to_local", fake_land)

    task = asyncio.run(
        download_service.add_download(
            {
                "title": "某电影 (2020)",
                "link": "https://pan.baidu.com/s/1abcdefg",
                "kind": ResourceKind.PAN.value,
            },
            notify=False,
        )
    )
    assert task is not None
    assert task.status == TaskStatus.TRANSFERRED.value, "云端已转存，不该报整体失败"
    assert task.error and "假 aria2" in task.error, f"落地失败的原因没落到界面上：{task.error}"


def test_save_share_payload_carries_site_id(monkeypatch, client):
    """`save_share` 的返回值必须带 site_id。

    注入验证抓到的假绿：把 `"site_id"` 改成别的键名时全绿 ——
    因为 `_land_pan_to_local` 的用例都是自己构造 dict 传进去的，
    **没人验证真正的生产者会不会给这个键**。契约的两端都要有测试。
    """
    from app.providers.panstorage.base import SaveResult
    from app.services import pan_storage as pan_service

    class _Saver:
        name = "fake"
        site_name = "假百度"
        supports_save = True
        config = {"id": 77}

        async def save_share(self, share_url, *, password=None, target_dir=None):
            return SaveResult(success=True, saved_path="/来自CineFlow/x.mkv", file_count=1)

    monkeypatch.setattr(pan_service, "_pick_for_share", lambda url: _Saver())
    monkeypatch.setattr(pan_service, "suggested_target_dir", lambda storage: "/来自CineFlow")
    monkeypatch.setattr(pan_service, "remember_target_dir", lambda *a, **k: None)

    result = asyncio.run(pan_service.save_share("https://pan.baidu.com/s/1abc"))
    assert result["success"] is True
    assert result.get("site_id") == 77, f"转存结果没带 site_id，换直链无从下手：{result}"

