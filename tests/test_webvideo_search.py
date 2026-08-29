"""YouTube / Bilibili 聚合搜索测试（离线 fixture，不依赖公网）。"""

from __future__ import annotations

import asyncio

import pytest

from app.providers.indexer import webvideo
from app.providers.indexer.webvideo import (
    BilibiliSearchProvider,
    YouTubeSearchProvider,
    parse_duration,
    strip_tags,
)
from app.providers.registry import list_providers, load_builtin_providers
from app.schemas.enums import ProviderKind, ResourceKind

load_builtin_providers()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_state():
    webvideo.reset_state()
    yield
    webvideo.reset_state()


#: B 站 search/type 接口的真实响应片段（实测抓取后裁剪）
BILI_ROWS = [
    {
        "title": '《<em class="keyword">流浪地球</em>2》4K 解析',
        "bvid": "BV1VtRqBwEfv",
        "arcurl": "http://www.bilibili.com/video/av116516070757883",
        "pic": "//i1.hdslb.com/bfs/archive/bc94961f.jpg",
        "duration": "166:39",
        "play": 491162,
        "author": "染柒影视",
        "pubdate": 1778065200,
        "description": "带 <em>高亮</em> 的简介",
    },
    {
        "title": "无 bvid 的课程页",
        "bvid": "",
        "arcurl": "https://www.bilibili.com/cheese/play/ss19704",
        "pic": "https://archive.biliimg.com/bfs/archive/2e206f.jpg",
        "duration": "",
        "play": 679201,
        "author": "益起映创",
        "pubdate": 0,
    },
]


# ---------------------------------------------------------------- 工具函数
def test_strip_tags_removes_highlight():
    """B 站会在标题里塞 <em class="keyword">，必须清掉否则标题很脏。"""
    assert strip_tags('《<em class="keyword">流浪地球</em>》') == "《流浪地球》"
    assert strip_tags("&amp;quot;") == '&quot;'


def test_parse_duration_variants():
    assert parse_duration("166:39") == 166 * 60 + 39
    assert parse_duration("1:02:03") == 3723
    assert parse_duration("") == 0
    assert parse_duration("--") == 0


# ---------------------------------------------------------------- 注册
def test_webvideo_providers_registered():
    names = {item["name"] for item in list_providers(ProviderKind.INDEXER.value)}
    assert {"bilibili", "youtube"} <= names


# ---------------------------------------------------------------- Bilibili
def patch_bili(provider, rows):
    async def fake(keyword):
        return rows

    provider._do_search = fake  # type: ignore[method-assign]


def test_bilibili_builds_webvideo_resources():
    """B 站结果必须是 WEBVIDEO 类型，且只暴露下载动作。"""
    provider = BilibiliSearchProvider({"name": "B站", "options": {"limit": 10}})
    patch_bili(
        provider,
        [
            {
                "title": "标题",
                "link": "https://www.bilibili.com/video/BV1",
                "poster": "https://i1.hdslb.com/x.jpg",
                "uploader": "up主",
                "duration": 100,
                "play_count": 5000,
                "publish_at": None,
            }
        ],
    )
    items = run(provider.search("流浪地球"))
    assert len(items) == 1
    item = items[0]
    assert item.kind == ResourceKind.WEBVIDEO.value
    assert "download" in item.actions
    assert "save" not in item.actions, "视频网页不能转存网盘"
    assert item.seeders == 5000, "播放量投影到 seeders 以便参与热度排序"
    assert item.extra["poster"].startswith("https://")


def test_bilibili_prefers_bvid_link():
    """有 bvid 时要拼规范地址，而不是用 av 号的 arcurl。"""
    provider = BilibiliSearchProvider({"name": "B站"})
    rows = run(_bili_rows(provider))
    assert rows[0]["link"] == "https://www.bilibili.com/video/BV1VtRqBwEfv"


def test_bilibili_falls_back_to_arcurl_without_bvid():
    provider = BilibiliSearchProvider({"name": "B站"})
    rows = run(_bili_rows(provider))
    assert rows[1]["link"] == "https://www.bilibili.com/cheese/play/ss19704"


def test_bilibili_normalizes_protocol_relative_poster():
    """B 站封面常是 //i1.hdslb.com/... 需补 https: 否则前端加载不了。"""
    provider = BilibiliSearchProvider({"name": "B站"})
    rows = run(_bili_rows(provider))
    assert rows[0]["poster"].startswith("https://i1.hdslb.com")


async def _bili_rows(provider):
    """直接跑 B 站的解析逻辑，但把 HTTP 层换成假响应。"""
    import app.providers.indexer.webvideo as mod

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"code": 0, "data": {"result": BILI_ROWS}}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            return FakeResponse()

    def fake_client(*args, **kwargs):
        return FakeClient()

    original = mod.async_client
    mod.async_client = fake_client
    try:
        return await provider._do_search("流浪地球")
    finally:
        mod.async_client = original


def test_bilibili_returns_none_on_error_code():
    """接口返回非 0 code（如 412 限流后的错误体）要返回 None 触发退避。"""
    provider = BilibiliSearchProvider({"name": "B站"})
    import app.providers.indexer.webvideo as mod

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"code": -412, "message": "请求被拦截"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            return FakeResponse()

    original = mod.async_client
    mod.async_client = lambda *a, **k: FakeClient()
    try:
        assert run(provider._do_search("x")) is None
    finally:
        mod.async_client = original


# ---------------------------------------------------------------- 缓存与退避
def test_search_caches_results():
    provider = BilibiliSearchProvider({"name": "B站"})
    calls = {"n": 0}

    async def fake(keyword):
        calls["n"] += 1
        return [{"title": "t", "link": "https://www.bilibili.com/video/BV1"}]

    provider._do_search = fake  # type: ignore[method-assign]
    run(provider.search("庆余年"))
    run(provider.search("庆余年"))
    assert calls["n"] == 1, "相同关键词应命中缓存"


def test_search_backs_off_after_failure():
    """_do_search 返回 None 表示失败，应进入退避且不再调用。"""
    provider = BilibiliSearchProvider({"name": "B站"})
    calls = {"n": 0}

    async def fake(keyword):
        calls["n"] += 1
        return None

    provider._do_search = fake  # type: ignore[method-assign]
    assert run(provider.search("a")) == []
    assert run(provider.search("b")) == []
    assert calls["n"] == 1


def test_search_survives_exception():
    """底层抛异常也不能让搜索崩掉，返回空即可。"""
    provider = BilibiliSearchProvider({"name": "B站"})

    async def boom(keyword):
        raise RuntimeError("network down")

    provider._do_search = boom  # type: ignore[method-assign]
    assert run(provider.search("x")) == []


def test_empty_keyword_returns_empty():
    provider = YouTubeSearchProvider({"name": "YT"})
    assert run(provider.search("   ")) == []


def test_limit_is_clamped():
    """limit 必须被夹在合理区间，避免用户填 99999 拖垮搜索。"""
    assert BilibiliSearchProvider({"options": {"limit": 99999}}).limit == 50
    # 0 视为"没填"，回落默认 20（而不是真的只搜 1 条）
    assert BilibiliSearchProvider({"options": {"limit": 0}}).limit == 20
    assert BilibiliSearchProvider({"options": {"limit": -5}}).limit == 1
    assert BilibiliSearchProvider({"options": {"limit": "bad"}}).limit == 20
    assert BilibiliSearchProvider({}).limit == 20


# ---------------------------------------------------------------- YouTube
def test_youtube_fills_thumbnail_fallback():
    """extract_flat 常不返回 thumbnail，要按 video id 兜底拼缩略图地址。"""
    provider = YouTubeSearchProvider({"name": "YT", "options": {"limit": 5}})

    fake_info = {
        "entries": [
            {
                "id": "abc123",
                "title": "视频标题",
                "url": "https://www.youtube.com/watch?v=abc123",
                "view_count": 1234,
                "duration": 61,
                "uploader": "频道",
            }
        ]
    }

    class FakeYdl:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, query, download=False):
            assert query.startswith("ytsearch5:")
            return fake_info

    import sys
    import types

    fake_module = types.SimpleNamespace(YoutubeDL=FakeYdl)
    sys.modules["yt_dlp"] = fake_module
    try:
        rows = run(provider._do_search("test"))
    finally:
        sys.modules.pop("yt_dlp", None)

    assert len(rows) == 1
    assert rows[0]["poster"] == "https://i.ytimg.com/vi/abc123/hqdefault.jpg"
    assert rows[0]["play_count"] == 1234
