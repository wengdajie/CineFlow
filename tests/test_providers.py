"""Provider 解析测试（不依赖真实网络）。"""

from __future__ import annotations

import pytest

from app.providers.base import Resource
from app.providers.indexer.torznab import TorznabIndexer
from app.providers.pan.generic import dig
from app.providers.pan.pansou import PanSouProvider, detect_pan_type
from app.providers.registry import (
    create_provider,
    get_provider_class,
    list_providers,
    load_builtin_providers,
)
from app.schemas.enums import ProviderKind, ResourceKind

load_builtin_providers()

TORZNAB_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <item>
      <title>Some.Show.S01E05.2160p.WEB-DL.H265-Group</title>
      <guid>https://site.test/details/1</guid>
      <comments>https://site.test/details/1</comments>
      <pubDate>Mon, 05 Aug 2024 10:00:00 +0800</pubDate>
      <size>21474836480</size>
      <enclosure url="https://site.test/download/1.torrent" length="21474836480"
                 type="application/x-bittorrent" />
      <torznab:attr name="seeders" value="42" />
      <torznab:attr name="peers" value="50" />
      <torznab:attr name="grabs" value="7" />
    </item>
    <item>
      <title>Some.Show.S01E06.1080p.WEB-DL</title>
      <enclosure url="magnet:?xt=urn:btih:ABCDEF123456" length="5368709120" />
      <torznab:attr name="seeders" value="8" />
    </item>
  </channel>
</rss>
"""


def test_registry_contains_core_providers():
    """核心 Provider 均已注册。"""
    names = {item["name"] for item in list_providers()}
    for expected in (
        "torznab", "rss", "nyaa", "pansou", "pan_generic",
        "qbittorrent", "transmission", "aria2",
        "emby", "jellyfin", "plex",
        "telegram", "webhook", "bark", "wecom",
    ):
        assert expected in names, expected


def test_registry_kind_filter():
    """按类别过滤 Provider。"""
    downloaders = list_providers(ProviderKind.DOWNLOADER.value)
    names = {item["name"] for item in downloaders}
    # 内置下载器必须全部注册；测试用假 Provider 可能已注册，故用子集断言
    assert {"qbittorrent", "transmission", "aria2"} <= names
    assert all(item["kind"] == ProviderKind.DOWNLOADER.value for item in downloaders)


def test_create_provider_unknown():
    """未知 Provider 返回 None 而不抛异常。"""
    assert create_provider("not-exists") is None
    assert get_provider_class("not-exists") is None


def test_torznab_parse():
    """Torznab XML 解析出种子与磁力两类资源。"""
    indexer = TorznabIndexer({"name": "TestSite", "url": "http://x/api", "priority": 5})
    resources = indexer._parse(TORZNAB_XML)

    assert len(resources) == 2
    first = resources[0]
    assert first.title == "Some.Show.S01E05.2160p.WEB-DL.H265-Group"
    assert first.kind == ResourceKind.TORRENT.value
    assert first.size == 21474836480
    assert first.seeders == 42
    assert first.leechers == 50
    assert first.site == "TestSite"
    assert first.priority == 5
    assert first.publish_at is not None

    second = resources[1]
    assert second.kind == ResourceKind.MAGNET.value


def test_torznab_parse_invalid_xml():
    """非法 XML 安全返回空列表。"""
    indexer = TorznabIndexer({"name": "X", "url": "http://x"})
    assert indexer._parse("<not xml") == []


def test_torznab_endpoint_normalization():
    """URL 自动补 /api。"""
    assert TorznabIndexer({"url": "http://x"})._endpoint() == "http://x/api"
    assert TorznabIndexer({"url": "http://x/api"})._endpoint() == "http://x/api"
    assert TorznabIndexer({})._endpoint() == ""


@pytest.mark.parametrize(
    "url,label",
    [
        ("https://pan.quark.cn/s/abc", "夸克网盘"),
        ("https://www.alipan.com/s/xyz", "阿里云盘"),
        ("https://pan.baidu.com/s/1abc", "百度网盘"),
        ("https://115.com/s/abc", "115网盘"),
        ("https://unknown.site/s/1", "未知网盘"),
    ],
)
def test_detect_pan_type(url, label):
    """按域名识别网盘类型。"""
    assert detect_pan_type(url) == label


def test_pansou_parse_merged_structure():
    """解析 merged_by_type 结构，并提取提取码。"""
    provider = PanSouProvider({"name": "PanSou", "url": "http://pansou.test"})
    payload = {
        "data": {
            "merged_by_type": {
                "夸克网盘": [
                    {
                        "title": "庆余年第二季 4K",
                        "url": "https://pan.quark.cn/s/abcdef",
                        "content": "全36集 密码: 8h2k",
                        "datetime": "2024-05-20 12:00:00",
                    }
                ],
                "阿里云盘": [
                    {
                        "note": "庆余年S02",
                        "link": "https://www.alipan.com/s/zzz",
                        "password": "w9q1",
                    }
                ],
            }
        }
    }
    resources = provider._parse(payload, "庆余年")

    assert len(resources) == 2
    assert all(item.kind == ResourceKind.PAN.value for item in resources)
    quark = next(item for item in resources if "quark" in item.link)
    assert quark.password == "8h2k"
    assert "夸克网盘" in quark.site
    assert quark.publish_at is not None


def test_pansou_parse_list_structure():
    """兼容 data.results 列表结构并按链接去重。"""
    provider = PanSouProvider({"name": "PanSou", "url": "http://x"})
    payload = {
        "data": {
            "results": [
                {"title": "A", "url": "https://pan.quark.cn/s/1"},
                {"title": "A 重复", "url": "https://pan.quark.cn/s/1"},
                {"title": "B", "url": "https://pan.xunlei.com/s/2"},
            ]
        }
    }
    resources = provider._parse(payload, "kw")
    assert len(resources) == 2


def test_pansou_parse_empty():
    """空数据不报错。"""
    provider = PanSouProvider({"url": "http://x"})
    assert provider._parse({}, "kw") == []
    assert provider._parse({"data": None}, "kw") == []


def test_dig_path():
    """嵌套路径取值。"""
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}
    assert dig(data, "a.b.1.c") == 2
    assert dig(data, "a.x.y") is None
    assert dig(data, "") == data


def test_resource_unique_key_magnet():
    """磁力链按 infohash 去重。"""
    first = Resource(
        title="A", link="magnet:?xt=urn:btih:ABC123&dn=name1", kind="magnet"
    )
    second = Resource(
        title="B", link="magnet:?xt=urn:btih:ABC123&dn=name2", kind="magnet"
    )
    assert first.unique_key == second.unique_key


def test_resource_size_parsing():
    """体积字符串自动转字节。"""
    resource = Resource(title="A", link="http://x", size="1.5 GB")
    assert resource.size == int(1.5 * 1024**3)


# ---------------- 下载器调度策略（v1.5.0） ----------------
class _FakeDownloader:
    """只带 name/site_name/priority 的假下载器，够 healthy_downloaders 排序用。"""

    def __init__(self, site_name: str, priority: int) -> None:
        self.name = site_name
        self.site_name = site_name
        self.priority = priority

    def __repr__(self) -> str:  # 断言失败时好读
        return f"<{self.site_name} p{self.priority}>"


@pytest.fixture
def fake_downloaders(monkeypatch):
    """注入三个假下载器，并默认让健康数据为空（全健康）。"""
    from app.services import site_health, sites

    items = [
        _FakeDownloader("qb-a", 3),
        _FakeDownloader("qb-b", 1),
        _FakeDownloader("tr-c", 2),
    ]
    monkeypatch.setattr(sites, "downloaders", lambda: list(items))
    monkeypatch.setattr(site_health, "downloader_health", lambda: {})
    monkeypatch.setattr(sites, "_task_counts", lambda: {})
    return items


def test_priority_strategy_sorts_by_priority(monkeypatch, fake_downloaders):
    from app.core.config import settings
    from app.services import sites

    monkeypatch.setattr(settings, "DOWNLOADER_STRATEGY", "priority")
    names = [item.site_name for item in sites.healthy_downloaders()]
    # priority 数字越小越优先，与其它 Provider 的约定一致
    assert names == ["qb-b", "tr-c", "qb-a"]


def test_least_tasks_strategy_prefers_idle(monkeypatch, fake_downloaders):
    from app.core.config import settings
    from app.services import sites

    monkeypatch.setattr(settings, "DOWNLOADER_STRATEGY", "least_tasks")
    # qb-b 虽然 priority 最高，但它手上活最多，应该让给空闲的
    monkeypatch.setattr(sites, "_task_counts", lambda: {"qb-b": 9, "tr-c": 4})
    names = [item.site_name for item in sites.healthy_downloaders()]
    assert names[0] == "qb-a"
    assert names == ["qb-a", "tr-c", "qb-b"]


def test_least_tasks_breaks_ties_by_priority(monkeypatch, fake_downloaders):
    from app.core.config import settings
    from app.services import sites

    monkeypatch.setattr(settings, "DOWNLOADER_STRATEGY", "least_tasks")
    monkeypatch.setattr(sites, "_task_counts", lambda: {})
    # 任务数相同时退回 priority，保证结果稳定可预期
    assert [item.site_name for item in sites.healthy_downloaders()] == ["qb-b", "tr-c", "qb-a"]


def test_round_robin_strategy_rotates(monkeypatch, fake_downloaders):
    from app.core.config import settings
    from app.services import sites

    monkeypatch.setattr(settings, "DOWNLOADER_STRATEGY", "round_robin")
    monkeypatch.setattr(sites, "_ROUND_ROBIN_CURSOR", 0)
    first = [item.site_name for item in sites.healthy_downloaders()]
    second = [item.site_name for item in sites.healthy_downloaders()]
    # 轮询的意义就是连续两次首选不同，把压力摊开
    assert first[0] != second[0]
    assert sorted(first) == sorted(second)


def test_unhealthy_downloaders_go_last_not_removed(monkeypatch, fake_downloaders):
    """不健康的排到最后但不剔除——健康数据可能过期（ADR-21）。"""
    from app.core.config import settings
    from app.services import site_health, sites

    monkeypatch.setattr(settings, "DOWNLOADER_STRATEGY", "priority")
    monkeypatch.setattr(
        site_health, "downloader_health", lambda: {"qb-b": "down", "tr-c": "degraded"}
    )
    names = [item.site_name for item in sites.healthy_downloaders()]
    assert names[0] == "qb-a"
    # 三个都还在，只是坏的靠后
    assert set(names) == {"qb-a", "qb-b", "tr-c"}
    assert names.index("qb-a") < names.index("qb-b")


def test_health_lookup_failure_falls_back(monkeypatch, fake_downloaders):
    """健康数据取不到时不能抛异常，退回策略排序即可。"""
    from app.core.config import settings
    from app.services import site_health, sites

    def _boom():
        raise RuntimeError("健康表还没建好")

    monkeypatch.setattr(settings, "DOWNLOADER_STRATEGY", "priority")
    monkeypatch.setattr(site_health, "downloader_health", _boom)
    assert [item.site_name for item in sites.healthy_downloaders()] == ["qb-b", "tr-c", "qb-a"]


def test_default_downloader_respects_prefer(monkeypatch, fake_downloaders):
    from app.core.config import settings
    from app.services import sites

    monkeypatch.setattr(settings, "DOWNLOADER_STRATEGY", "priority")
    assert sites.default_downloader().site_name == "qb-b"
    assert sites.default_downloader(prefer="tr-c").site_name == "tr-c"
    # 指定的名字不存在时退回策略首选，而不是返回 None
    assert sites.default_downloader(prefer="不存在").site_name == "qb-b"


def test_default_downloader_without_any(monkeypatch):
    from app.services import sites

    monkeypatch.setattr(sites, "downloaders", lambda: [])
    assert sites.default_downloader() is None
    assert sites.downloader_candidates() == []


def test_candidates_include_backups_when_failover_on(monkeypatch, fake_downloaders):
    from app.core.config import settings
    from app.services import sites

    monkeypatch.setattr(settings, "DOWNLOADER_STRATEGY", "priority")
    monkeypatch.setattr(settings, "DOWNLOADER_FAILOVER", True)
    candidates = [item.site_name for item in sites.downloader_candidates(prefer="tr-c")]
    # 首选在前，其余作为失败自动换源的备选
    assert candidates[0] == "tr-c"
    assert len(candidates) == 3


def test_candidates_single_when_failover_off(monkeypatch, fake_downloaders):
    """关掉 failover 时行为与 v1.4.0 一致：只投首选，不擅自换源。"""
    from app.core.config import settings
    from app.services import sites

    monkeypatch.setattr(settings, "DOWNLOADER_STRATEGY", "priority")
    monkeypatch.setattr(settings, "DOWNLOADER_FAILOVER", False)
    candidates = sites.downloader_candidates()
    assert len(candidates) == 1 and candidates[0].site_name == "qb-b"


def test_single_downloader_skips_strategy(monkeypatch):
    """只有一个下载器时直接返回，不做任何排序与健康查询。"""
    from app.core.config import settings
    from app.services import sites

    only = _FakeDownloader("solo", 9)
    monkeypatch.setattr(sites, "downloaders", lambda: [only])
    monkeypatch.setattr(settings, "DOWNLOADER_STRATEGY", "round_robin")
    assert [item.site_name for item in sites.healthy_downloaders()] == ["solo"]
