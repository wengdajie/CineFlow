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
