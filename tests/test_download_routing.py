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
