"""追新雷达测试（离线：假站点 + 内存订阅）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.db.init_db import create_tables
from app.db.models import Subscribe
from app.db.session import session_scope
from app.providers.base import Resource, SearchProvider
from app.schemas.enums import ResourceKind, SubscribeStatus
from app.services import radar


class FakeIndexer(SearchProvider):
    """返回固定「最新流」的假站点。"""

    name = "fake_latest"
    display_name = "假站点"

    def __init__(self, config, feed: list[Resource] | None = None, fail: bool = False):
        super().__init__(config)
        self._feed = feed or []
        self._fail = fail

    async def search(self, keyword, **kwargs) -> list[Resource]:
        return []

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        if self._fail:
            raise RuntimeError("站点炸了")
        return self._feed[:limit]


def _magnet(title: str, infohash: str, size: int = 5 * 1024**3) -> Resource:
    return Resource(
        title=title,
        link=f"magnet:?xt=urn:btih:{infohash}",
        site="假站点",
        kind=ResourceKind.MAGNET.value,
        size=size,
        seeders=50,
    )


@pytest.fixture
def clean_subscribes():
    """每个用例前后清空订阅表（并保证表已建好，便于单独运行本文件）。"""
    create_tables()
    with session_scope() as session:
        session.query(Subscribe).delete()
    yield
    with session_scope() as session:
        session.query(Subscribe).delete()


def _add_subscribe(**kwargs: Any) -> int:
    """插入一个活跃订阅。"""
    payload = {
        "title": "师兄太稳健",
        "media_type": "tv",
        "season": 1,
        "total_episodes": 20,
        "start_episode": 1,
        "downloaded_episodes": [],
        "status": SubscribeStatus.ACTIVE.value,
        "lack_episodes": 20,
        **kwargs,
    }
    with session_scope() as session:
        record = Subscribe(**payload)
        session.add(record)
        session.flush()
        return record.id


# ---------------------------------------------------------------- 标题匹配
def test_title_tokens():
    """标题变体：原样 + 去空格。"""
    tokens = radar._title_tokens("The Last of Us")
    assert "the last of us" in tokens
    assert "thelastofus" in tokens


def test_match_subscribe_prefers_longest():
    """《凡人修仙传》不应被《凡人》抢走。"""
    subs = [
        {"id": 1, "title": "凡人", "tokens": radar._title_tokens("凡人")},
        {"id": 2, "title": "凡人修仙传", "tokens": radar._title_tokens("凡人修仙传")},
    ]
    hit = radar.match_subscribe(
        "凡人修仙传：外海风云[第165集].2160p.WEB-DL-ColorTV", subs
    )
    assert hit is not None and hit["id"] == 2


def test_match_subscribe_handles_separators():
    """资源名里的点/下划线分隔符不应影响匹配。"""
    subs = [{"id": 1, "title": "The Last of Us",
             "tokens": radar._title_tokens("The Last of Us")}]
    assert radar.match_subscribe(
        "The.Last.of.Us.S02E03.2160p.WEB-DL.H265", subs
    ) is not None


def test_match_subscribe_no_hit():
    """不相关资源返回 None。"""
    subs = [{"id": 1, "title": "师兄太稳健",
             "tokens": radar._title_tokens("师兄太稳健")}]
    assert radar.match_subscribe("Some.Other.Show.S01E01.1080p", subs) is None


def test_match_subscribe_empty_title():
    """空标题安全处理。"""
    assert radar.match_subscribe("", [{"id": 1, "title": "x", "tokens": {"x"}}]) is None


# ---------------------------------------------------------------- 最新流汇总
def test_fetch_feed_dedupes_and_isolates_failures(monkeypatch):
    """跨站去重；单站异常不影响其他站点。"""
    shared = _magnet("Show.S01E01.1080p.WEB-DL", "A" * 32)
    good = FakeIndexer({"name": "好站"}, [shared, _magnet("Show.S01E02.1080p", "B" * 32)])
    dup = FakeIndexer({"name": "重复站"}, [shared])
    bad = FakeIndexer({"name": "坏站"}, fail=True)
    monkeypatch.setattr(radar, "_providers", lambda: [good, dup, bad])

    feed = asyncio.run(radar.fetch_feed(limit_per_site=50))
    assert len(feed) == 2, "相同 infohash 应跨站去重"


def test_fetch_feed_without_providers(monkeypatch):
    """没有启用站点时返回空。"""
    monkeypatch.setattr(radar, "_providers", lambda: [])
    assert asyncio.run(radar.fetch_feed()) == []


# ---------------------------------------------------------------- 完整雷达闭环
def test_radar_matches_missing_episode_and_downloads(monkeypatch, clean_subscribes):
    """核心闭环：最新流里的缺集资源被识别并投递下载。"""
    subscribe_id = _add_subscribe(downloaded_episodes=[1, 2], total_episodes=5)

    feed = [
        _magnet("师兄太稳健[第3集][国语配音+中文字幕].Pull.Strings.S01.2026.2160p.WEB-DL.H265", "A" * 32),
        _magnet("师兄太稳健[第1集][国语配音].Pull.Strings.S01.2026.2160p.WEB-DL.H265", "B" * 32),
        _magnet("无关剧集.Other.Show.S01E09.1080p.WEB-DL", "C" * 32),
    ]
    monkeypatch.setattr(radar, "_providers", lambda: [FakeIndexer({"name": "假站点"}, feed)])

    added: list[dict[str, Any]] = []

    async def fake_add(resource, **kwargs):
        added.append({"resource": resource, "kwargs": kwargs})
        return object()

    monkeypatch.setattr(radar.download_service, "add_download", fake_add)

    result = asyncio.run(radar.run())

    assert result["subscribes"] == 1
    assert result["resources"] == 3
    assert result["matched"] == 1
    assert len(added) == 1, "只应下载缺失的第 3 集"
    assert "第3集" in added[0]["resource"]["title"]
    assert added[0]["kwargs"]["subscribe_id"] == subscribe_id


def test_radar_dry_run_does_not_download(monkeypatch, clean_subscribes):
    """预览模式只匹配不下载。"""
    _add_subscribe(downloaded_episodes=[], total_episodes=3)
    feed = [_magnet("师兄太稳健[第1集].Pull.Strings.S01.2026.2160p.WEB-DL", "A" * 32)]
    monkeypatch.setattr(radar, "_providers", lambda: [FakeIndexer({"name": "假站点"}, feed)])

    called = False

    async def fake_add(resource, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(radar.download_service, "add_download", fake_add)

    result = asyncio.run(radar.run(dry_run=True))
    assert result["dry_run"] is True
    assert len(result["downloads"]) == 1
    assert result["downloads"][0]["dry_run"] is True
    assert called is False, "dry_run 不应调用下载"


def test_radar_skips_already_downloaded(monkeypatch, clean_subscribes):
    """已入库的集不再重复下载。"""
    _add_subscribe(downloaded_episodes=[1, 2, 3], total_episodes=3)
    feed = [_magnet("师兄太稳健[第2集].Pull.Strings.S01.2026.2160p.WEB-DL", "A" * 32)]
    monkeypatch.setattr(radar, "_providers", lambda: [FakeIndexer({"name": "假站点"}, feed)])

    added: list[Any] = []

    async def fake_add(resource, **kwargs):
        added.append(resource)

    monkeypatch.setattr(radar.download_service, "add_download", fake_add)

    result = asyncio.run(radar.run())
    assert added == []
    assert result["downloads"] == []


def test_radar_respects_filter_rules(monkeypatch, clean_subscribes):
    """订阅的分辨率偏好应过滤掉不合格资源。"""
    _add_subscribe(downloaded_episodes=[], total_episodes=2, resolution="2160p")
    feed = [
        _magnet("师兄太稳健[第1集].Pull.Strings.S01.2026.720p.WEB-DL", "A" * 32),
    ]
    monkeypatch.setattr(radar, "_providers", lambda: [FakeIndexer({"name": "假站点"}, feed)])

    added: list[Any] = []

    async def fake_add(resource, **kwargs):
        added.append(resource)

    monkeypatch.setattr(radar.download_service, "add_download", fake_add)

    result = asyncio.run(radar.run())
    assert added == [], "720p 不满足 2160p 偏好"
    assert result["skipped"], "应记录被过滤的原因"


def test_radar_season_pack_fills_multiple_episodes(monkeypatch, clean_subscribes):
    """季包可一次补齐多集。"""
    _add_subscribe(downloaded_episodes=[], total_episodes=10)
    feed = [
        _magnet("师兄太稳健[全10集].Pull.Strings.S01.2026.2160p.WEB-DL", "A" * 32, 60 * 1024**3),
    ]
    monkeypatch.setattr(radar, "_providers", lambda: [FakeIndexer({"name": "假站点"}, feed)])

    added: list[Any] = []

    async def fake_add(resource, **kwargs):
        added.append(resource)

    monkeypatch.setattr(radar.download_service, "add_download", fake_add)

    asyncio.run(radar.run())
    assert len(added) == 1


def test_radar_without_subscribes(monkeypatch, clean_subscribes):
    """没有活跃订阅时直接返回，不拉取站点。"""
    fetched = False

    def providers():
        nonlocal fetched
        fetched = True
        return []

    monkeypatch.setattr(radar, "_providers", providers)
    result = asyncio.run(radar.run())
    assert result["subscribes"] == 0
    assert fetched is False, "没有订阅就不该请求站点"


def test_radar_paused_subscribes_ignored(monkeypatch, clean_subscribes):
    """暂停的订阅不参与追新。"""
    _add_subscribe(status=SubscribeStatus.PAUSED.value)
    monkeypatch.setattr(radar, "_providers", lambda: [FakeIndexer({"name": "x"}, [])])
    result = asyncio.run(radar.run())
    assert result["subscribes"] == 0
