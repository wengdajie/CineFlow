"""新增资源站 Provider 测试：yyets / wp_film（离线 fixture）。"""

from __future__ import annotations

import asyncio

from app.providers.base import Resource
from app.providers.indexer.wp_film import WordPressFilmProvider
from app.providers.indexer.yyets import YyetsProvider
from app.providers.registry import list_providers, load_builtin_providers
from app.schemas.enums import ProviderKind, ResourceKind

load_builtin_providers()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 注册
def test_new_site_providers_registered():
    names = {item["name"] for item in list_providers(ProviderKind.INDEXER.value)}
    assert {"yyets", "wp_film"} <= names


# ---------------------------------------------------------------- Resource 能力位
def test_pan_resource_has_save_and_download():
    """本轮需求：网盘资源必须同时给出「转存」与「下载」两个动作。"""
    row = Resource(
        title="片名",
        link="https://pan.quark.cn/s/abcd",
        kind=ResourceKind.PAN.value,
        page_url="https://pan.quark.cn/s/abcd",
    )
    assert "save" in row.actions
    assert "download" in row.actions
    assert row.to_dict()["actions"] == row.actions


def test_torrent_resource_has_no_save():
    row = Resource(title="t", link="magnet:?xt=urn:btih:aaa", kind=ResourceKind.TORRENT.value)
    assert "save" not in row.actions
    assert "download" in row.actions


def test_webvideo_resource_download_only():
    row = Resource(
        title="t",
        link="https://www.bilibili.com/video/BV1",
        kind=ResourceKind.WEBVIDEO.value,
    )
    assert row.actions.count("download") == 1
    assert "save" not in row.actions


# ---------------------------------------------------------------- yyets
#: 人人影视详情接口的真实结构（实测裁剪）
YYETS_DETAIL = {
    "info": {
        "id": 24220,
        "cnname": "阿凡达",
        "enname": "Avatar",
        "channel_cn": "电影",
        "area": "美国",
    },
    "list": [
        {
            "season_num": "1",
            "items": {
                "1080P": [
                    {
                        "itemid": "13787",
                        "name": "[阿凡达].Avatar.2009.BluRay.1080p.mkv",
                        "size": "19.58GB",
                        "dateline": "1331117695",
                        "files": [
                            {
                                "way": "1",
                                "way_cn": "电驴",
                                "address": "ed2k://|file|Avatar.mkv|21020951660|b049ad09|/",
                                "passwd": "",
                            },
                            {
                                "way": "2",
                                "way_cn": "磁力",
                                "address": "magnet:?xt=urn:btih:C4EUEC4HYFIC5FBI4RISILSUTFYY3Y7A",
                                "passwd": "",
                            },
                            {
                                "way": "3",
                                "way_cn": "诚通网盘",
                                "address": "https://pan.baidu.com/s/1abcdef",
                                "passwd": "a1b2",
                            },
                        ],
                    }
                ]
            },
        }
    ],
}


def test_yyets_flatten_all_download_ways():
    """四层嵌套要被正确拍平，三种下载方式都要产出资源。"""
    provider = YyetsProvider({"name": "人人影视", "url": "https://yyets.click"})
    items = provider._flatten(YYETS_DETAIL, "阿凡达")
    assert len(items) == 3
    kinds = {i.kind for i in items}
    assert kinds == {
        ResourceKind.DIRECT.value,
        ResourceKind.MAGNET.value,
        ResourceKind.PAN.value,
    }


def test_yyets_maps_metadata():
    provider = YyetsProvider({"name": "人人影视", "url": "https://yyets.click"})
    items = provider._flatten(YYETS_DETAIL, "阿凡达")
    magnet = next(i for i in items if i.kind == ResourceKind.MAGNET.value)
    assert magnet.size > 0, "体积应从 19.58GB 解析出来"
    assert magnet.extra["quality"] == "1080P"
    assert magnet.extra["season"] == 1
    assert magnet.extra["show_name"] == "阿凡达"
    assert magnet.publish_at is not None


def test_yyets_carries_pan_password():
    """网盘链接的提取码必须带上，否则转存会失败。"""
    provider = YyetsProvider({"name": "人人影视"})
    items = provider._flatten(YYETS_DETAIL, "阿凡达")
    pan = next(i for i in items if i.kind == ResourceKind.PAN.value)
    assert pan.password == "a1b2"
    assert "save" in pan.actions


def test_yyets_dedupes_same_address():
    """同一地址出现两次只保留一条。"""
    payload = {
        "info": {"id": 1, "cnname": "X"},
        "list": [
            {
                "season_num": "1",
                "items": {
                    "1080P": [
                        {
                            "name": "a.mkv",
                            "size": "1GB",
                            "files": [
                                {"way_cn": "磁力", "address": "magnet:?xt=urn:btih:SAME"},
                                {"way_cn": "磁力", "address": "magnet:?xt=urn:btih:SAME"},
                            ],
                        }
                    ]
                },
            }
        ],
    }
    provider = YyetsProvider({"name": "人人影视"})
    assert len(provider._flatten(payload, "X")) == 1


def test_yyets_handles_empty_payload():
    provider = YyetsProvider({"name": "人人影视"})
    assert provider._flatten({}, "X") == []


def test_yyets_detail_limit_clamped():
    assert YyetsProvider({"options": {"detail_limit": 999}}).detail_limit == 20
    assert YyetsProvider({}).detail_limit == 5


def test_yyets_empty_keyword():
    provider = YyetsProvider({"name": "人人影视"})
    assert run(provider.search("")) == []


# ---------------------------------------------------------------- wp_film
ARTICLE_HTML = """
<html><body>
<h1>2009《阿凡达》残疾老兵化身阿凡达</h1>
<p>解压密码：abcd1234</p>
<a href="magnet:?xt=urn:btih:470d8700523fd4cddbdf975ea9ed859b48a0384e&dn=Avatar">磁力下载</a>
<a href="magnet:?xt=urn:btih:81262bf4071441436cfd04806e741fefb67dab0b">磁力2</a>
<a href="https://pan.quark.cn/s/xyz123">夸克网盘</a>
<a href="ed2k://|file|Avatar.mkv|123456|0123456789abcdef0123456789abcdef|/">电驴</a>
</body></html>
"""

RSS_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title><![CDATA[2009《阿凡达》残疾老兵化身阿凡达]]></title>
  <link>https://www.bdflixs.com/428.html</link>
  <pubDate>Tue, 10 Dec 2024 10:00:00 +0000</pubDate>
</item>
<item>
  <title>2022《阿凡达：水之道》一家人绝境求生</title>
  <link>https://www.bdflixs.com/427.html</link>
  <pubDate>Wed, 11 Dec 2024 10:00:00 +0000</pubDate>
</item>
</channel></rss>
"""


def make_wp(pages):
    """构造 wp_film 实例，把 _fetch 换成查表，完全离线。"""
    provider = WordPressFilmProvider(
        {"name": "BD影视", "url": "https://www.bdflixs.com", "options": {"article_limit": 3}}
    )

    async def fake_fetch(url):
        return pages.get(url, "")

    provider._fetch = fake_fetch  # type: ignore[method-assign]
    return provider


def test_wp_parses_rss_items():
    provider = make_wp({provider_url(): RSS_FEED})
    rows = run(provider._article_links("阿凡达"))
    assert len(rows) == 2
    assert rows[0][0].startswith("2009《阿凡达》")
    assert rows[0][1] == "https://www.bdflixs.com/428.html"
    assert rows[0][2] is not None, "pubDate 应解析成时间"


def provider_url():
    from urllib.parse import quote

    return f"https://www.bdflixs.com/?s={quote('阿凡达')}&feed=rss2"


def test_wp_extracts_all_link_types():
    """详情页里的磁力/网盘/电驴都要抓到。"""
    provider = make_wp({})
    items = provider._extract(
        ARTICLE_HTML, title="阿凡达", page_url="https://x/428.html", published=None
    )
    kinds = {i.kind for i in items}
    assert ResourceKind.MAGNET.value in kinds
    assert ResourceKind.PAN.value in kinds
    assert ResourceKind.DIRECT.value in kinds


def test_wp_attaches_password_only_to_pan():
    """密码只对网盘链接有意义，磁力不该带上。"""
    provider = make_wp({})
    items = provider._extract(
        ARTICLE_HTML, title="阿凡达", page_url="https://x/428.html", published=None
    )
    pan = next(i for i in items if i.kind == ResourceKind.PAN.value)
    magnet = next(i for i in items if i.kind == ResourceKind.MAGNET.value)
    assert pan.password == "abcd1234"
    assert magnet.password is None


def test_wp_respects_per_article_limit():
    provider = WordPressFilmProvider(
        {"name": "x", "url": "https://x", "options": {"per_article_limit": 2}}
    )
    items = provider._extract(ARTICLE_HTML, title="t", page_url="https://x/1", published=None)
    assert len(items) == 2


def test_wp_full_search_pipeline():
    """端到端：RSS 找文章 → 进详情页抓链接。"""
    provider = make_wp(
        {
            provider_url(): RSS_FEED,
            "https://www.bdflixs.com/428.html": ARTICLE_HTML,
            "https://www.bdflixs.com/427.html": "",
        }
    )
    items = run(provider.search("阿凡达"))
    assert items, "应从第一篇文章抓到链接"
    assert all(i.site == "BD影视" for i in items)
    assert all(i.page_url == "https://www.bdflixs.com/428.html" for i in items)


def test_wp_empty_feed_returns_empty():
    provider = make_wp({})
    assert run(provider.search("不存在")) == []


def test_wp_without_url_returns_empty():
    provider = WordPressFilmProvider({"name": "x"})
    assert run(provider.search("阿凡达")) == []


def test_wp_custom_search_url_template():
    """mjf2020 用的是 /ss/?s= 这种非默认路径，模板必须生效。"""
    provider = WordPressFilmProvider(
        {
            "name": "MJF",
            "url": "https://www.mjf2020.com",
            "options": {"search_url": "https://www.mjf2020.com/ss/?s={keyword}"},
        }
    )
    assert provider._search_url("阿凡达").startswith("https://www.mjf2020.com/ss/?s=")


def test_wp_article_limit_clamped():
    assert WordPressFilmProvider({"options": {"article_limit": 999}}).article_limit == 20
    assert WordPressFilmProvider({}).article_limit == 5
