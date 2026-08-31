"""MacCMS 在线影视站适配器（v1.13.0 需求 3）。

**这一轮最关键的事实**（有实测数据，不是"看代码觉得"）：
`bzzdyy.com` 这类"在线影视站"自己**不存片**，播放页的 `url` 指向的就是
各平台官方地址。抓首页 30 部片、53 个播放源实测分布：

    qq 21 / qiyi 12 / youku 10 / mgtv 6 / bilibili 3 / rrmj 1

即 **49/53 ≈ 92% 是长视频平台的会员正片**。它们唯一的"能力"是把播放交给
一个 VIP 解析网关（bzzdyy 的 `playerconfig.js` 里全部播放源共用
`"parse": "https://hls.xiguadh.com/?url="`）——而这正是 ADR-24 明确拒绝的：
解析网关依赖盗取的会员票据。

所以本 Provider 只做诚实的那部分：把站点当**索引**用，产出播放源指向的
原始平台地址，能不能下由 `is_blocked()` 统一裁决。这些测试就是钉住
"不偷偷接解析网关"和"会员正片如实标注"这两条底线。

`cz4k.com` 实测被 SafeLine WAF 拦在门外（HTTP 468），换 UA / Accept-Language
均无效，故只作预设不做 WAF 对抗（与 ADR-24 同口径）。
"""

from __future__ import annotations

import asyncio

from app.providers.base import Resource
from app.providers.indexer.maccms import (
    MacCmsIndexer,
    parse_player_config,
    source_label,
)
from app.providers.registry import list_providers
from app.schemas.enums import ProviderKind, ResourceKind


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- 真实页面片段
#: 搜索结果页（实测结构裁剪）：同一部片会出现两次链接（封面 + 标题）
SEARCH_PAGE = """
<div class="stui-vodlist__box">
  <a class="stui-vodlist__thumb" href="/index.php/vod/detail/id/59038.html" title="流浪地球2"></a>
  <h4 class="title"><a href="/index.php/vod/detail/id/59038.html" title="流浪地球2">流浪地球2</a></h4>
</div>
<div class="stui-vodlist__box">
  <a class="stui-vodlist__thumb" href="/index.php/vod/detail/id/52111.html" title="无关的片子"></a>
</div>
"""

DETAIL_PAGE = """
<h1 class="title">流浪地球2</h1>
<img class="lazyload" data-original="/upload/vod/pic.jpg" />
<a href="/index.php/vod/play/id/59038/sid/1/nid/1.html">播放1</a>
<a href="/index.php/vod/play/id/59038/sid/2/nid/1.html">播放2</a>
<a href="/index.php/vod/play/id/59038/sid/1/nid/2.html">第2集</a>
"""

#: 实测的播放页结构：配置写在 `var player_aaaa={...}</script>` 里
PLAY_BILI = (
    '<script type="text/javascript">var player_aaaa={"flag":"play","encrypt":0,'
    '"url":"https:\\/\\/www.bilibili.com\\/bangumi\\/play\\/ep744327?theme=movie",'
    '"from":"bilibili","id":"59038","sid":1,"nid":1}</script>'
)
PLAY_QQ = (
    '<script type="text/javascript">var player_aaaa={"flag":"play","encrypt":0,'
    '"url":"https:\\/\\/v.qq.com\\/x\\/cover\\/mzc002008v8rka3\\/d0046v4pddg.html",'
    '"from":"qq","id":"59038","sid":2,"nid":1}</script>'
)
#: 第 2 集的播放页**必须**存在，否则"每个播放源只取第一集"是假绿的：
#: 正则一旦放宽到 /nid/\d+，多抓的那条会因为查表 miss 被静默丢掉，测试照样过。
PLAY_BILI_EP2 = (
    '<script type="text/javascript">var player_aaaa={"flag":"play","encrypt":0,'
    '"url":"https:\\/\\/www.bilibili.com\\/bangumi\\/play\\/ep744328?theme=movie",'
    '"from":"bilibili","id":"59038","sid":1,"nid":2}</script>'
)
#: 弱相关影片也要有真实播放源，否则"过滤弱相关"同样是假绿的
PLAY_UNRELATED = (
    '<script type="text/javascript">var player_aaaa={"flag":"play","encrypt":0,'
    '"url":"https:\\/\\/www.bilibili.com\\/video\\/BV1unrelated",'
    '"from":"bilibili","id":"52111","sid":1,"nid":1}</script>'
)


def make_site(pages, **options):
    """构造离线实例：把 _get 换成查表，一个请求都不真发。"""
    provider = MacCmsIndexer(
        {"name": "西瓜影院", "url": "https://www.bzzdyy.com", "options": options}
    )

    async def fake_get(path):
        return pages.get(path, "")

    provider._get = fake_get  # type: ignore[method-assign]
    return provider


PAGES = {
    "/index.php/vod/search.html?wd=流浪地球": SEARCH_PAGE,
    "/index.php/vod/detail/id/59038.html": DETAIL_PAGE,
    "/index.php/vod/detail/id/52111.html": (
        "<h1>无关的片子</h1>"
        '<a href="/index.php/vod/play/id/52111/sid/1/nid/1.html">播放</a>'
    ),
    "/index.php/vod/play/id/59038/sid/1/nid/1.html": PLAY_BILI,
    "/index.php/vod/play/id/59038/sid/2/nid/1.html": PLAY_QQ,
    "/index.php/vod/play/id/59038/sid/1/nid/2.html": PLAY_BILI_EP2,
    "/index.php/vod/play/id/52111/sid/1/nid/1.html": PLAY_UNRELATED,
}


class TestRegistration:
    def test_provider_已注册为索引器(self):
        names = {item["name"]: item for item in list_providers()}
        assert "maccms" in names
        assert names["maccms"]["kind"] == ProviderKind.INDEXER.value

    def test_两个在线站已作为预设内置(self):
        from app.db.init_db import DEFAULT_SITES

        urls = {
            item["url"]
            for item in DEFAULT_SITES
            if item.get("provider") == "maccms"
        }
        assert "https://www.bzzdyy.com" in urls
        assert "https://www.cz4k.com" in urls
        # 需要用户确认才启用，且必须写清"大部分是会员正片"这个预期
        for item in DEFAULT_SITES:
            if item.get("provider") == "maccms":
                assert item["enabled"] is False
                assert item["options"]["note"]


class TestPlayerConfigParsing:
    def test_抠出播放源与地址(self):
        config = parse_player_config(PLAY_BILI)
        assert config["from"] == "bilibili"
        assert config["url"] == "https://www.bilibili.com/bangumi/play/ep744327?theme=movie"

    def test_换模板抠不出来时不炸(self):
        """站点换模板是常态，解析失败必须降级成空而不是抛异常。"""
        assert parse_player_config("<html>换了模板</html>") == {}
        assert parse_player_config("") == {}
        assert parse_player_config("var player_aaaa={不是合法JSON}</script>") == {}

    def test_播放源代号翻译成中文(self):
        assert source_label("qq") == "腾讯视频"
        assert source_label("mgtv") == "芒果TV"
        # 没见过的代号原样显示，方便用户自己判断，而不是隐藏成"未知"
        assert source_label("someNewSource") == "somenewsource"


class TestSearch:
    def test_产出播放源的原始平台地址(self):
        rows = run(make_site(PAGES).search("流浪地球"))
        links = sorted(row.link for row in rows)
        assert links == [
            "https://v.qq.com/x/cover/mzc002008v8rka3/d0046v4pddg.html",
            "https://www.bilibili.com/bangumi/play/ep744327?theme=movie",
        ]
        # 必须是 webvideo：这样才会走 yt-dlp 链路并接受 ADR-24 的裁决
        assert all(row.kind == ResourceKind.WEBVIDEO.value for row in rows)

    def test_标题带上平台名以便区分(self):
        rows = run(make_site(PAGES).search("流浪地球"))
        titles = sorted(row.title for row in rows)
        assert titles == ["流浪地球2（哔哩哔哩）", "流浪地球2（腾讯视频）"]

    def test_每个播放源只取第一集(self):
        """产物是「这部片在哪个平台能看」，不该把 92 集刷成 92 条结果。"""
        rows = run(make_site(PAGES).search("流浪地球"))
        assert len(rows) == 2
        # 第 2 集的地址（ep744328）不得出现
        assert all("ep744328" not in row.link for row in rows), [r.link for r in rows]

    def test_过滤站内弱相关结果(self):
        """站内搜索常把弱相关片子也返回，这些必须被剔掉。"""
        rows = run(make_site(PAGES).search("流浪地球"))
        assert all("无关" not in row.title for row in rows)
        # 弱相关影片有真实播放源，不过滤的话它的地址会泄进结果
        assert all("BV1unrelated" not in row.link for row in rows), [r.link for r in rows]

    def test_带上封面与来源信息(self):
        rows = run(make_site(PAGES).search("流浪地球"))
        row = next(item for item in rows if "哔哩" in item.title)
        assert row.extra["poster"] == "https://www.bzzdyy.com/upload/vod/pic.jpg"
        assert row.extra["play_source"] == "bilibili"
        assert row.page_url.endswith("/index.php/vod/detail/id/59038.html")

    def test_搜索无结果时返回空(self):
        assert run(make_site({}).search("不存在的片")) == []

    def test_未配置地址时不炸(self):
        provider = MacCmsIndexer({"name": "x", "url": ""})
        assert run(provider.search("任意")) == []

    def test_max_items_限制详情抓取量(self):
        provider = make_site(PAGES, max_items=1)
        rows = run(provider.search("流浪地球"))
        # 只抓第一部片（59038），仍应拿到它的两个播放源
        assert len(rows) == 2


class TestPaywallHonesty:
    """底线测试：会员正片必须如实标注并拒绝，不能偷偷接解析网关。"""

    def test_会员正片被标记为_paywalled(self):
        rows = run(make_site(PAGES).search("流浪地球"))
        by_source = {row.extra["play_source"]: row for row in rows}
        assert by_source["qq"].paywalled is True, "腾讯正片必须标为会员内容"
        assert by_source["bilibili"].paywalled is False, "B 站番剧页属公开内容"

    def test_会员正片不渲染下载按钮(self):
        """渲染一个必然失败的按钮 = 让用户白点一次。"""
        rows = run(make_site(PAGES).search("流浪地球"))
        by_source = {row.extra["play_source"]: row for row in rows}
        assert "download" not in by_source["qq"].actions
        # 但仍留"详情页"入口，让用户能去官方平台看
        assert "open" in by_source["qq"].actions
        assert "download" in by_source["bilibili"].actions

    def test_paywalled_随结果下发给前端(self):
        rows = run(make_site(PAGES).search("流浪地球"))
        payload = [row.to_dict() for row in rows]
        assert all("paywalled" in item for item in payload)

    def test_非网页视频资源不受影响(self):
        """paywalled 只对 webvideo 有意义，别把种子/网盘也误标。"""
        assert Resource(title="t", link="magnet:?xt=urn:btih:aaa").paywalled is False
        assert (
            Resource(
                title="t",
                link="https://v.qq.com/x/cover/a/b.html",
                kind=ResourceKind.PAN.value,
            ).paywalled
            is False
        )

    def test_源码里不出现解析网关调用(self):
        """守住 ADR-24：不接入任何 VIP 解析网关。

        这是**元测试**：站点自己的 playerconfig.js 里写着
        `"parse": "https://hls.xiguadh.com/?url="`，全部播放源共用它。
        接进来就能"什么都能下"，但它依赖盗取的会员票据。
        钉住源码里不出现这类网关，防止日后有人"顺手加一下"。
        """
        import ast
        import pathlib

        source = pathlib.Path("app/providers/indexer/maccms.py").read_text(
            encoding="utf-8"
        )
        # 只看【真正的代码】：注释与文档字符串里写网关名字是在解释
        # "我们为什么不用它"，那是应该保留的。用 AST 把字符串字面量全拉出来检查。
        literals = [
            node.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]
        # 排掉模块/类/函数的文档字符串（它们不参与运行时拼 URL）
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        runtime_strings = chr(10).join(
            text for text in literals if text not in docstrings
        )
        for gateway in ("xiguadh", "/?url=", "jiexi"):
            assert gateway not in runtime_strings, f"疑似接入了解析网关: {gateway}"


class TestHealthCheck:
    def test_结构匹配则通过(self):
        provider = make_site({"/": SEARCH_PAGE})
        ok, message = run(provider.health_check())
        assert ok is True
        assert "MacCMS" in message

    def test_访问不通给出可读原因(self):
        ok, message = run(make_site({}).health_check())
        assert ok is False
        assert "WAF" in message or "失效" in message

    def test_结构不匹配时明确说明(self):
        provider = make_site({"/": "<html>这不是影视站</html>"})
        ok, message = run(provider.health_check())
        assert ok is False
        assert "MacCMS" in message

    def test_未配置地址(self):
        provider = MacCmsIndexer({"name": "x", "url": ""})
        ok, message = run(provider.health_check())
        assert ok is False
        assert "地址" in message
