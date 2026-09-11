"""端到端自动化测试：订阅 -> 搜索 -> 下载 -> 整理入库 -> 缺集收敛。

使用内存假 Provider，完全不触网。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.core.config import settings
from app.db.init_db import create_tables
from app.db.models import DownloadTask, LibraryFile, Subscribe
from app.db.session import session_scope
from app.providers.base import Resource, SearchProvider
from app.providers.downloader.base import BaseDownloader, TorrentState
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind, SubscribeStatus, TaskStatus
from app.services import download as download_service
from app.services import library as library_service
from app.services import search as search_service
from app.services import sites as site_service
from app.services import subscribe as subscribe_service

# ---------------------------------------------------------------- 假 Provider
FAKE_TITLES = [
    "自动化测试剧.S01E01.2160p.WEB-DL.H265-CF",
    "自动化测试剧.S01E02.2160p.WEB-DL.H265-CF",
    "自动化测试剧.S01E03.1080p.WEB-DL.H264-CF",
    "自动化测试剧.S01E01.CAM.枪版",
    "完全无关的另一部剧.S01E01.1080p",
]


@register
class FakeIndexer(SearchProvider):
    """内存索引器。"""

    name = "fake_indexer"
    kind = ProviderKind.INDEXER.value
    display_name = "测试索引器"

    async def search(self, keyword, *, media_type=None, season=None, episode=None, page=0):
        return [
            Resource(
                title=title,
                link=f"magnet:?xt=urn:btih:FAKE{index:04d}",
                site="FakeSite",
                kind=ResourceKind.MAGNET.value,
                size=8 * 1024**3,
                seeders=100,
            )
            for index, title in enumerate(FAKE_TITLES)
        ]


class FakeDownloader(BaseDownloader):
    """内存下载器：add 后立即视为完成，并在磁盘生成文件。"""

    name = "fake_downloader"
    display_name = "测试下载器"
    store: dict[str, TorrentState] = {}
    content_root: Path | None = None

    async def add(self, link, *, save_path=None, category=None, paused=False, cookie=None):
        external_id = link.split("btih:")[-1]
        title = self.config.get("_title_map", {}).get(link, "Unknown.S01E01.1080p")
        root = FakeDownloader.content_root or Path(settings.DOWNLOAD_DIR)
        target = root / f"{title}.mkv"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"0" * 4096)

        FakeDownloader.store[external_id] = TorrentState(
            external_id=external_id,
            name=title,
            status=TaskStatus.COMPLETED.value,
            progress=1.0,
            size=4096,
            save_path=str(target.parent),
            content_path=str(target),
        )
        return external_id

    async def get(self, external_id):
        return FakeDownloader.store.get(external_id)

    async def list_tasks(self, category=None):
        return list(FakeDownloader.store.values())

    async def remove(self, external_id, *, delete_files=False):
        FakeDownloader.store.pop(external_id, None)
        return True


register(FakeDownloader)


@pytest.fixture
def automation_env(tmp_path, monkeypatch):
    """把搜索/下载器替换为假实现，并隔离媒体库目录。"""
    library = tmp_path / "library"
    downloads = tmp_path / "downloads"
    library.mkdir()
    downloads.mkdir()

    monkeypatch.setattr(settings, "LIBRARY_DIR", library)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", downloads)
    FakeDownloader.store = {}
    FakeDownloader.content_root = downloads

    indexer = FakeIndexer({"name": "FakeSite", "enabled": True, "priority": 1})
    title_map = {
        f"magnet:?xt=urn:btih:FAKE{index:04d}": title
        for index, title in enumerate(FAKE_TITLES)
    }
    downloader = FakeDownloader(
        {"name": "FakeDownloader", "enabled": True, "_title_map": title_map}
    )

    monkeypatch.setattr(site_service, "search_providers", lambda: [indexer])
    monkeypatch.setattr(site_service, "downloaders", lambda: [downloader])
    monkeypatch.setattr(site_service, "default_downloader", lambda prefer=None: downloader)
    monkeypatch.setattr(site_service, "notifiers", list)
    monkeypatch.setattr(site_service, "media_servers", list)

    def cleanup_tasks() -> None:
        create_tables()
        with session_scope() as session:
            session.query(DownloadTask).delete()
            session.query(Subscribe).delete()

    cleanup_tasks()
    try:
        yield {"library": library, "downloads": downloads}
    finally:
        cleanup_tasks()


# ---------------------------------------------------------------- 测试
def test_search_filters_and_ranks(automation_env):
    """聚合搜索应过滤枪版与无关剧，并按画质排序。"""
    results = asyncio.run(
        search_service.search("自动化测试剧", media_type="tv", season=1, save_history=False)
    )
    titles = [item["title"] for item in results]

    assert titles, "应有搜索结果"
    assert not any("CAM" in title for title in titles), "枪版应被过滤"
    assert not any("无关" in title for title in titles), "无关剧应被过滤"
    assert "2160p" in titles[0], "4K 应排在最前"


def test_keyword_generation():
    """关键词按 SxxExx -> Sxx -> 片名 逐级降级。"""
    keywords = search_service.build_keywords(
        "庆余年", media_type="tv", season=2, episode=5
    )
    assert keywords[0] == "庆余年 S02E05"
    assert "庆余年 S02" in keywords
    assert keywords[-1] == "庆余年"


def test_full_automation_flow(automation_env, client, auth_headers):
    """完整链路：订阅 -> 巡检下载 -> 同步整理 -> 入库 -> 缺集清零。"""
    subscribe = asyncio.run(
        subscribe_service.create_subscribe(
            {
                "title": "自动化测试剧",
                "media_type": "tv",
                "season": 1,
                "total_episodes": 3,
            }
        )
    )
    sub_id = subscribe.id

    # 初始缺 3 集
    with session_scope() as session:
        record = session.get(Subscribe, sub_id)
        assert subscribe_service.compute_missing(record) == [1, 2, 3]

    # 巡检：应命中并创建下载任务
    result = asyncio.run(subscribe_service.process_subscribe(sub_id))
    assert result["matched"] > 0
    assert len(result["downloads"]) > 0

    with session_scope() as session:
        tasks = session.query(DownloadTask).filter(
            DownloadTask.subscribe_id == sub_id
        ).all()
        assert tasks, "应创建下载任务"
        assert all(task.status == TaskStatus.DOWNLOADING.value for task in tasks)

    # 同步下载状态：假下载器立即完成，并触发整理
    stats = asyncio.run(download_service.sync_tasks())
    assert stats["completed"] > 0

    # 校验文件已按规范入库
    library_root = automation_env["library"]
    media_files = list(library_root.rglob("*.mkv"))
    assert media_files, "媒体库应有文件"
    assert any("Season 01" in str(path) for path in media_files)
    assert any("自动化测试剧" in str(path) for path in media_files)

    # 校验入库索引与订阅进度
    with session_scope() as session:
        indexed = session.query(LibraryFile).filter(
            LibraryFile.title == "自动化测试剧"
        ).all()
        assert indexed, "应写入媒体库索引"

        record = session.get(Subscribe, sub_id)
        assert record.downloaded_episodes, "应记录已下载集数"
        assert record.status in (
            SubscribeStatus.ACTIVE.value,
            SubscribeStatus.COMPLETED.value,
        )

    # 再次巡检不应重复下载已入库的集
    before = _task_count(sub_id)
    asyncio.run(subscribe_service.process_subscribe(sub_id))
    assert _task_count(sub_id) == before, "已入库集不应重复下载"


def _task_count(subscribe_id: int) -> int:
    with session_scope() as session:
        return (
            session.query(DownloadTask)
            .filter(DownloadTask.subscribe_id == subscribe_id)
            .count()
        )


def test_library_scan_and_existing_episodes(automation_env):
    """扫描媒体库可重建索引并支撑缺集判断。"""
    library = automation_env["library"]
    season_dir = library / "扫描测试剧" / "Season 02"
    season_dir.mkdir(parents=True)
    for episode in (1, 2, 5):
        (season_dir / f"扫描测试剧 - S02E{episode:02d}.mkv").write_bytes(b"0" * 2048)

    stats = library_service.scan_library(library)
    assert stats["added"] >= 3

    episodes = library_service.existing_episodes("扫描测试剧", 2)
    assert episodes == {1, 2, 5}


def test_movie_subscribe_completes(automation_env):
    """电影订阅命中后即完成。"""
    subscribe = asyncio.run(
        subscribe_service.create_subscribe(
            {"title": "自动化测试剧", "media_type": "movie", "season": 1}
        )
    )
    missing = asyncio.run(subscribe_service.process_subscribe(subscribe.id))
    assert missing["missing"] == [1]


# ------------------------------------------------- 内置调度任务（v1.5.0 扩容）
def test_all_builtin_jobs_are_registered_and_resolvable():
    """16 个内置任务都要有可解析的执行目标，否则调度器起来就报错。"""
    from app.services.scheduler import builtin_specs, scheduler_service

    specs = builtin_specs()
    keys = [spec.key for spec in specs]
    assert keys == [
        "subscribe",
        "radar",
        "download",
        "pan_transfer",
        "pan_subscribe",
        "video_subscribe",
        "pan_keepalive",
        "strm_sync",
        "speed_limit",
        "site_health",
        "zhuiju_sync",
        "rss",
        "upgrade",
        "library",
    ]
    for spec in specs:
        func, kwargs = scheduler_service._job_target(spec.key)
        assert callable(func), spec.key
        assert isinstance(kwargs, dict)
        assert spec.job_id.startswith("cineflow.")
        assert spec.name and spec.description


def test_new_jobs_are_off_by_default_when_feature_disabled():
    """危险/重活任务默认不能自己跑起来：洗版默认关。

    v1.24.0 起刮削整体下线（ADR-96），原来的 ``specs["scrape"]`` 断言随之删除 ——
    不是判断被推翻，是那个任务不存在了。
    """
    from app.core.config import settings
    from app.services.scheduler import builtin_specs

    specs = {spec.key: spec for spec in builtin_specs()}
    assert specs["upgrade"].enabled is bool(settings.UPGRADE_ENABLED)
    assert "scrape" not in specs
    # STRM 同步默认间隔为 0 = 关闭（多数用户没有网盘）
    assert specs["strm_sync"].enabled is bool(settings.STRM_SYNC_INTERVAL_MINUTES > 0)

# ---------------------------------------------------------------- v1.28.2
def _make_gated_provider(title: str, gate: asyncio.Event | None = None,
                         entered: asyncio.Event | None = None):
    """造一个返回指定剧集的内存索引器；给 gate 时第一次搜索会阻塞。"""
    @register
    class GatedIndexer(SearchProvider):
        name = f"gated_{abs(hash(title))}"
        kind = ProviderKind.INDEXER.value
        display_name = "门控测试索引器"
        calls = 0

        async def search(self, keyword, *, media_type=None, season=None,
                         episode=None, page=0):
            type(self).calls += 1
            if entered is not None:
                entered.set()
            if gate is not None:
                await gate.wait()
            return [
                Resource(
                    title=f"{title}.S01E01.1080p.WEB-DL.H264-CF",
                    link=f"magnet:?xt=urn:btih:{abs(hash(title)):040x}",
                    site="GatedSite",
                    kind=ResourceKind.MAGNET.value,
                    size=2 * 1024**3,
                    seeders=50,
                )
            ]

    return GatedIndexer({"name": "GatedSite", "enabled": True})


def test_create_subscribe_triggers_immediate_search_for_all_episodes(
    automation_env, client, monkeypatch
):
    """创建订阅后应立即搜到站点最新集，并一次性投递全部缺失集。"""
    from app.services.scheduler import scheduler_service

    title = "即搜多集测试剧"
    monkeypatch.setattr(
        site_service, "search_providers",
        lambda: [_episode_provider(title, episodes=(1, 2, 3))],
    )

    async def scenario() -> None:
        # 直接起底层调度器（测试环境 SCHEDULER_ENABLED=false，
        # start() 不注册内置周期任务；date 一次性任务照发）
        scheduler_service.scheduler.start()
        try:
            sub = await subscribe_service.create_subscribe(
                {"title": title, "media_type": "tv", "season": 1}
            )
            for _ in range(60):
                await asyncio.sleep(0.05)
                with session_scope() as session:
                    count = (
                        session.query(DownloadTask)
                        .filter(DownloadTask.subscribe_id == sub.id)
                        .count()
                    )
                if count == 3:
                    break
            with session_scope() as session:
                tasks = (
                    session.query(DownloadTask)
                    .filter(DownloadTask.subscribe_id == sub.id)
                    .all()
                )
            assert len(tasks) == 3, "创建订阅后应立即搜索并创建全部下载任务"
        finally:
            scheduler_service.shutdown()

    asyncio.run(scenario())


def test_process_subscribe_single_flight(automation_env, client, monkeypatch):
    """同一订阅的巡检重叠时只允许一个在跑，防止打爆上游站点。"""
    title = "单飞测试剧"
    entered = asyncio.Event()
    gate = asyncio.Event()
    indexer = _make_gated_provider(title, gate=gate, entered=entered)
    monkeypatch.setattr(site_service, "search_providers", lambda: [indexer])

    subscribe = asyncio.run(
        subscribe_service.create_subscribe(
            {"title": title, "media_type": "tv", "season": 1, "total_episodes": 1}
        )
    )
    sub_id = subscribe.id

    async def scenario() -> None:
        task1 = asyncio.create_task(
            subscribe_service.process_subscribe(sub_id))
        await entered.wait()
        # 第一个还卡在搜索中，第二个必须直接跳过
        task2 = asyncio.create_task(
            subscribe_service.process_subscribe(sub_id))
        result2 = await task2
        gate.set()
        result1 = await task1
        assert result2.get("skipped") is True
        assert result1.get("matched", 0) > 0

    asyncio.run(scenario())

# ---------------------------------------------------------------- v1.28.3
def _episode_provider(title: str, *, episodes=(1, 2, 3), empty=False):
    """返回指定剧 E01..En 资源的内存索引器（标题与订阅同名，能过白名单）。"""
    @register
    class EpisodeIndexer(SearchProvider):
        name = f"epidx_{abs(hash((title, episodes, empty)))}"
        kind = ProviderKind.INDEXER.value
        display_name = "多集测试索引器"

        async def search(self, keyword, *, media_type=None, season=None,
                         episode=None, page=0):
            if empty:
                return []
            return [
                Resource(
                    title=f"{title}.S01E{n:02d}.1080p.WEB-DL.H264-CF",
                    link=f"magnet:?xt=urn:btih:{abs(hash((title, n))):040x}",
                    site="EpisodeSite",
                    kind=ResourceKind.MAGNET.value,
                    size=2 * 1024**3,
                    seeders=50,
                )
                for n in episodes
            ]

    return EpisodeIndexer({"name": "EpisodeSite", "enabled": True})


def test_unknown_total_discovers_and_enqueues_all_episodes(
    automation_env, client, monkeypatch
):
    """总集数未知（无 TMDB，番剧/综艺常态）时，应从搜索结果探测最新集，
    并一次性投递全部缺失集——而不是下完一集再等下一轮巡检。"""
    title = "探测多集剧甲"
    monkeypatch.setattr(
        site_service, "search_providers", lambda: [_episode_provider(title)]
    )
    subscribe = asyncio.run(
        subscribe_service.create_subscribe(
            {"title": title, "media_type": "tv", "season": 1}
        )
    )
    sub_id = subscribe.id
    with session_scope() as session:
        assert session.get(Subscribe, sub_id).total_episodes == 0

    result = asyncio.run(subscribe_service.process_subscribe(sub_id))

    # 缺集探测应从 [1] 扩展到站点上实际有的 [1,2,3]
    assert result["missing"] == [1, 2, 3]
    assert result["matched"] >= 3
    with session_scope() as session:
        tasks = (
            session.query(DownloadTask)
            .filter(DownloadTask.subscribe_id == sub_id)
            .all()
        )
    assert len(tasks) == 3, "应一次性创建 3 集的下载任务"


def test_unknown_total_with_no_results_stays_single_probe(
    automation_env, client, monkeypatch
):
    """站点上一集都没有时，缺集列表保持 [1]（不能凭空扩出几十集）。"""
    title = "探测多集剧乙"
    monkeypatch.setattr(
        site_service, "search_providers",
        lambda: [_episode_provider(title, empty=True)],
    )
    subscribe = asyncio.run(
        subscribe_service.create_subscribe(
            {"title": title, "media_type": "tv", "season": 1}
        )
    )
    result = asyncio.run(subscribe_service.process_subscribe(subscribe.id))
    assert result["missing"] == [1]
    assert result["downloads"] == []


def test_discovery_does_not_duplicate_after_completion(
    automation_env, client, monkeypatch
):
    """已下载的集不应被探测逻辑重复投递。"""
    title = "探测多集剧丙"
    monkeypatch.setattr(
        site_service, "search_providers", lambda: [_episode_provider(title)]
    )
    subscribe = asyncio.run(
        subscribe_service.create_subscribe(
            {"title": title, "media_type": "tv", "season": 1}
        )
    )
    sub_id = subscribe.id
    asyncio.run(subscribe_service.process_subscribe(sub_id))

    # 模拟第 1 集已入库，再次巡检：缺集不再含 1，也不重下第 1 集
    subscribe_service.mark_episodes_done(sub_id, [1])
    before = _task_count(sub_id)
    result = asyncio.run(subscribe_service.process_subscribe(sub_id))
    assert 1 not in result["missing"]
    assert _task_count(sub_id) == before, "已下载集不应重复创建任务"
