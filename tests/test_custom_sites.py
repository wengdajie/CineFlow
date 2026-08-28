"""自定义站点支持测试：JSON API / HTML 正则 / 导航站发现（全程离线）。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.providers.indexer.generic_api import (
    GenericApiIndexer,
    clean_text,
    flatten_items,
    guess_kind,
)
from app.providers.indexer.generic_html import GenericHtmlIndexer, strip_tags
from app.providers.indexer.mukaku import MukakuIndexer
from app.providers.registry import list_providers, load_builtin_providers
from app.schemas.enums import ResourceKind
from app.services import discovery, presets

load_builtin_providers()


# ---------------------------------------------------------------- 假响应装置
class FakeHttp:
    """把 fetch_json / fetch_text 替换为查表返回，完全不触网。"""

    def __init__(self) -> None:
        self.json_routes: dict[str, Any] = {}
        self.text_routes: dict[str, str] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def fetch_json(self, url: str, **kwargs: Any) -> Any:
        params = kwargs.get("params") or kwargs.get("json_body") or {}
        self.calls.append((url, dict(params)))
        for key, value in self.json_routes.items():
            if key in url:
                return value(params) if callable(value) else value
        return None

    async def fetch_text(self, url: str, **kwargs: Any) -> str | None:
        self.calls.append((url, dict(kwargs.get("params") or {})))
        for key, value in self.text_routes.items():
            if key in url:
                return value
        return None


@pytest.fixture
def fake_http(monkeypatch):
    """注入假 HTTP 层（覆盖各 Provider 模块内引用的函数）。"""
    fake = FakeHttp()
    for module in (
        "app.providers.indexer.generic_api",
        "app.providers.indexer.generic_html",
        "app.services.discovery",
    ):
        monkeypatch.setattr(f"{module}.fetch_json", fake.fetch_json, raising=False)
        monkeypatch.setattr(f"{module}.fetch_text", fake.fetch_text, raising=False)
    return fake


# ---------------------------------------------------------------- 工具函数
def test_dig_and_flatten():
    """路径取值与列表压平。"""
    from app.providers.indexer.generic_api import dig

    payload = {"data": {"list": [{"a": 1}, {"a": 2}], "map": {"x": [{"b": 3}]}}}
    assert dig(payload, "data.list.0.a") == 1
    assert dig(payload, "data.missing") is None
    assert len(flatten_items(dig(payload, "data.list"))) == 2
    # 字典套列表（网盘按类型分组的常见结构）
    assert flatten_items(dig(payload, "data.map")) == [{"b": 3}]
    assert flatten_items(None) == []


def test_clean_text_unescapes_entities():
    """站点标题常含 HTML 实体与零宽字符。"""
    assert clean_text("凡人修仙传&#8206;") == "凡人修仙传"
    assert clean_text("A &amp; B") == "A & B"
    assert clean_text(None) == ""


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("magnet:?xt=urn:btih:ABC", ResourceKind.MAGNET.value),
        ("https://site.test/x.torrent", ResourceKind.TORRENT.value),
        ("https://pan.quark.cn/s/abc", ResourceKind.PAN.value),
        ("https://www.123684.com/s/abc", ResourceKind.PAN.value),
    ],
)
def test_guess_kind(link, expected):
    """按链接特征推断资源类型。"""
    assert guess_kind(link) == expected


def test_guess_kind_respects_declared():
    """显式声明的类型优先于推断。"""
    assert guess_kind("https://x.test/a", "pan") == "pan"


def test_strip_tags():
    """HTML 标签清理。"""
    assert strip_tags("<b>Some.Show</b> <i>1080p</i>") == "Some.Show 1080p"


# ---------------------------------------------------------------- 一阶段 JSON 站点
ONE_STAGE = {
    "code": 200,
    "data": {
        "list": [
            {
                "name": "Some.Show.S01E05.2160p.WEB-DL.H265-Group",
                "magnet": "magnet:?xt=urn:btih:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                "size": "20 GB",
                "seeders": "42",
                "leechers": 5,
                "created_at": "2026-08-01 10:00:00",
                "detail": "/tr/1.html",
            },
            {
                "name": "Some.Show.S01E06.1080p.WEB-DL",
                "magnet": "magnet:?xt=urn:btih:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
                "size": 5368709120,
                "seeders": 8,
            },
            {"name": "无链接条目", "magnet": ""},
        ]
    },
}

ONE_STAGE_CONFIG = {
    "name": "示例站",
    "url": "https://site.test",
    "priority": 30,
    "options": {
        "api_base": "https://site.test/api/v1",
        "fixed_params": {"token": "abc"},
        "success_key": "code",
        "success_value": 200,
        "search_path": "search",
        "query_key": "kw",
        "page_key": "page",
        "limit_key": "limit",
        "limit": 20,
        "list_path": "data.list",
        "item_map": {
            "title": "name",
            "link": "magnet",
            "size": "size",
            "seeders": "seeders",
            "leechers": "leechers",
            "publish_at": "created_at",
            "page_url": "detail",
        },
    },
}


def test_generic_api_one_stage_search(fake_http):
    """列表直接带链接的站点：字段映射全部生效。"""
    fake_http.json_routes["/search"] = ONE_STAGE
    provider = GenericApiIndexer(ONE_STAGE_CONFIG)
    results = asyncio.run(provider.search("Some Show"))

    assert len(results) == 2, "无链接条目应被跳过"
    first = results[0]
    assert first.title == "Some.Show.S01E05.2160p.WEB-DL.H265-Group"
    assert first.kind == ResourceKind.MAGNET.value
    assert first.size == 20 * 1024**3
    assert first.seeders == 42
    assert first.leechers == 5
    assert first.publish_at is not None
    assert first.site == "示例站"
    assert first.priority == 30
    # 相对详情链接应补全为绝对地址
    assert first.page_url == "https://site.test/tr/1.html"
    # 固定参数与关键词/分页参数都要送出
    url, params = fake_http.calls[0]
    assert url == "https://site.test/api/v1/search"
    assert params == {"token": "abc", "kw": "Some Show", "page": 1, "limit": 20}


def test_generic_api_respects_success_flag(fake_http):
    """站点返回失败码时应安全返回空列表。"""
    fake_http.json_routes["/search"] = {"code": 500, "message": "资源不存在"}
    provider = GenericApiIndexer(ONE_STAGE_CONFIG)
    assert asyncio.run(provider.search("x")) == []


def test_generic_api_handles_network_failure(fake_http):
    """HTTP 层返回 None（网络故障）时不应抛异常。"""
    provider = GenericApiIndexer(ONE_STAGE_CONFIG)
    assert asyncio.run(provider.search("x")) == []


def test_generic_api_requires_config():
    """缺少 api_base 时优雅降级。"""
    provider = GenericApiIndexer({"name": "空站", "options": {}})
    assert asyncio.run(provider.search("x")) == []
    ok, message = asyncio.run(provider.health_check())
    assert ok is False and "api_base" in message


# ---------------------------------------------------------------- 两阶段 JSON 站点
TWO_STAGE_LIST = {
    "success": True,
    "data": {
        "data": [
            {"id": 1, "idcode": "36923479", "title": "凡人修仙传&#8206;",
             "alias": "A Mortal's Journey", "years": "2025"},
            {"id": 2, "idcode": "111", "title": "凡人", "alias": ""},
        ]
    },
}

TWO_STAGE_DETAIL = {
    "success": True,
    "data": {
        "all_seeds": [
            {"zname": "凡人修仙传[第165集].2160p.WEB-DL-ColorTV",
             "zlink": "magnet:?xt=urn:btih:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
             "zsize": "3.04 GB", "ezt": "2025-10-18"},
            {"zname": "凡人修仙传[第164集].1080p.WEB-DL-ColorTV",
             "zlink": "magnet:?xt=urn:btih:DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD",
             "zsize": "1.08 GB", "ezt": "2025-10-11"},
        ],
        "movies_online_seed": {
            "quark": [
                {"seed_name": "凡人修仙传 4K 高码率", "code": "a1b2",
                 "link": "https://pan.quark.cn/s/d219e8745077",
                 "created_at": "2026-08-26 14:57:08"}
            ],
            "baidu": [],
        },
    },
}

TWO_STAGE_CONFIG = {
    "name": "两阶段站",
    "url": "https://two.test",
    "options": {
        "api_base": "https://two.test/api",
        "success_key": "success",
        "success_value": True,
        "search_path": "getVideoList",
        "query_key": "sb",
        "list_path": "data.data",
        "item_map": {"title": "title", "alias": "alias",
                     "detail_id": "idcode", "link": "__absent__"},
        "detail_path": "getVideoDetail",
        "detail_query_key": "id",
        "max_detail_items": 1,
        "detail_extract": [
            {"list_path": "data.all_seeds", "kind": "magnet", "label": "BT",
             "map": {"title": "zname", "link": "zlink", "size": "zsize",
                     "publish_at": "ezt"}},
            {"list_path": "data.movies_online_seed", "kind": "pan", "label": "网盘",
             "map": {"title": "seed_name", "link": "link", "password": "code",
                     "publish_at": "created_at"}},
        ],
    },
}


def test_generic_api_two_stage_collects_all_links(fake_http):
    """两阶段站点：详情接口里的磁力与网盘链接都要被抽出。"""
    fake_http.json_routes["getVideoList"] = TWO_STAGE_LIST
    fake_http.json_routes["getVideoDetail"] = TWO_STAGE_DETAIL

    provider = GenericApiIndexer(TWO_STAGE_CONFIG)
    results = asyncio.run(provider.search("凡人修仙传"))

    assert len(results) == 3, "2 个磁力 + 1 个网盘"
    kinds = {item.kind for item in results}
    assert kinds == {ResourceKind.MAGNET.value, ResourceKind.PAN.value}

    magnet = next(r for r in results if r.kind == ResourceKind.MAGNET.value)
    assert magnet.size == int(3.04 * 1024**3)
    assert magnet.site == "两阶段站·BT"

    pan = next(r for r in results if r.kind == ResourceKind.PAN.value)
    assert pan.link == "https://pan.quark.cn/s/d219e8745077"
    assert pan.password == "a1b2"
    assert pan.site == "两阶段站·网盘"


def test_generic_api_ranks_candidates_before_detail(fake_http):
    """详情请求昂贵：应优先请求标题最相关的条目。"""
    fake_http.json_routes["getVideoList"] = TWO_STAGE_LIST
    fake_http.json_routes["getVideoDetail"] = TWO_STAGE_DETAIL

    provider = GenericApiIndexer(TWO_STAGE_CONFIG)
    asyncio.run(provider.search("凡人修仙传"))

    detail_calls = [c for c in fake_http.calls if "getVideoDetail" in c[0]]
    assert len(detail_calls) == 1, "max_detail_items=1 应只请求一次详情"
    # 精确匹配的 idcode=36923479 而非弱相关的「凡人」
    assert detail_calls[0][1]["id"] == "36923479"


def test_generic_api_detail_failure_is_isolated(fake_http):
    """详情接口失败时不应影响整体搜索。"""
    fake_http.json_routes["getVideoList"] = TWO_STAGE_LIST
    provider = GenericApiIndexer(TWO_STAGE_CONFIG)
    assert asyncio.run(provider.search("凡人修仙传")) == []


# ---------------------------------------------------------------- 最新流
LATEST_PAYLOAD = {
    "success": True,
    "data": {
        "list": [
            {"id": 832806, "aurl": "/tr/832806.html",
             "zname": "师兄太稳健[第18集].Pull.Strings.S01.2026.2160p.WEB-DL-BlackTV",
             "zlink": "magnet:?xt=urn:btih:EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE",
             "zsize": "3.48 GB", "eztime": "38分钟前"},
            {"id": 832805, "aurl": "/tr/832805.html",
             "zname": "欢迎来到蕾安家.第二季[全10集].Leanne.S02.2160p.NF.WEB-DL",
             "zlink": "magnet:?xt=urn:btih:FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
             "zsize": "29.41 GB", "eztime": "2026-08-27 10:00:00"},
        ]
    },
}


def test_generic_api_fetch_latest(fake_http):
    """最新流：多个变体参数各请求一次，并正确解析相对时间。"""
    fake_http.json_routes["getTList"] = LATEST_PAYLOAD
    config = {
        **TWO_STAGE_CONFIG,
        "options": {
            **TWO_STAGE_CONFIG["options"],
            "latest_path": "getTList",
            "latest_list_path": "data.list",
            "latest_params": [{"sc": 1}, {"sc": 2}],
            "latest_map": {"title": "zname", "link": "zlink", "size": "zsize",
                           "publish_at": "eztime", "page_url": "aurl"},
        },
    }
    provider = GenericApiIndexer(config)
    results = asyncio.run(provider.fetch_latest(limit=10))

    assert len(results) == 4, "2 个变体 × 2 条"
    calls = [c for c in fake_http.calls if "getTList" in c[0]]
    assert {c[1]["sc"] for c in calls} == {1, 2}
    # "38分钟前" 无法解析为时间，应为 None 而不是崩溃
    assert results[0].publish_at is None
    assert results[1].publish_at is not None
    assert results[0].page_url == "https://two.test/tr/832806.html"


def test_generic_api_latest_requires_config(fake_http):
    """未配置 latest_path 时返回空。"""
    provider = GenericApiIndexer(ONE_STAGE_CONFIG)
    assert asyncio.run(provider.fetch_latest()) == []


# ---------------------------------------------------------------- Mukaku 预设
def test_mukaku_preset_needs_no_options(fake_http):
    """内置预设：用户只填站点地址即可搜到磁力与网盘。"""
    fake_http.json_routes["getVideoList"] = TWO_STAGE_LIST
    fake_http.json_routes["getVideoDetail"] = TWO_STAGE_DETAIL

    provider = MukakuIndexer({"name": "Mukaku", "url": "https://web5.mukaku.com"})
    results = asyncio.run(provider.search("凡人修仙传"))

    assert len(results) >= 3
    assert {r.kind for r in results} == {
        ResourceKind.MAGNET.value, ResourceKind.PAN.value
    }
    # 鉴权参数自动注入
    url, params = fake_http.calls[0]
    assert "web5.mukaku.com/prod/api/v1/getVideoList" in url
    assert params["app_id"] and params["identity"]
    assert params["sb"] == "凡人修仙传"


def test_mukaku_user_options_override_preset(fake_http):
    """用户 options 应覆盖内置预设。"""
    fake_http.json_routes["getVideoList"] = TWO_STAGE_LIST
    provider = MukakuIndexer(
        {
            "name": "Mukaku",
            "url": "https://mirror.test",
            "options": {"limit": 3, "fixed_params": {"app_id": "custom"}},
        }
    )
    asyncio.run(provider.search("测试"))
    _, params = fake_http.calls[0]
    assert params["limit"] == 3
    assert params["app_id"] == "custom"


def test_mukaku_handles_mirror_domain(fake_http):
    """换域名时 API 路径自动跟随。"""
    provider = MukakuIndexer({"name": "M", "url": "https://web9.mukaku.com/"})
    assert provider.option("api_base") == "https://web9.mukaku.com/prod/api/v1"


def test_mukaku_latest_flow(fake_http):
    """最新流用于追新雷达。"""
    fake_http.json_routes["getTList"] = LATEST_PAYLOAD
    provider = MukakuIndexer({"name": "Mukaku", "url": "https://web5.mukaku.com"})
    results = asyncio.run(provider.fetch_latest(limit=10))
    assert len(results) == 4
    assert all(r.link.startswith("magnet:") for r in results)
    ok, message = asyncio.run(provider.health_check())
    assert ok is True and "最新流" in message


# ---------------------------------------------------------------- HTML 站点
SEARCH_HTML = """
<table>
  <tr class="item">
    <td><a href="/details/1" title="Some.Show.S01E05.2160p.WEB-DL-Group">链接</a></td>
    <td><a href="magnet:?xt=urn:btih:11111111111111111111111111111111">磁力</a></td>
    <td class="size">20.5 GB</td><td class="se">120</td><td class="le">9</td>
  </tr>
  <tr class="item">
    <td><a href="/details/2" title="Some.Show.S01E06.1080p.WEB-DL">链接</a></td>
    <td><a href="magnet:?xt=urn:btih:22222222222222222222222222222222">磁力</a></td>
    <td class="size">5 GB</td><td class="se">8</td><td class="le">1</td>
  </tr>
  <tr class="item"><td>没有链接的行</td></tr>
</table>
"""

HTML_CONFIG = {
    "name": "网页站",
    "url": "https://html.test",
    "options": {
        "search_url": "https://html.test/search?q={keyword}&page={page}",
        "latest_url": "https://html.test/latest",
        "row_pattern": '<tr class="item">(.*?)</tr>',
        "field_patterns": {
            "title": 'title="([^"]+)"',
            "link": 'href="(magnet:[^"]+)"',
            "size": '<td class="size">([^<]+)</td>',
            "seeders": '<td class="se">(\\d+)</td>',
            "leechers": '<td class="le">(\\d+)</td>',
            "page_url": 'href="(/details/[^"]+)"',
        },
        "local_filter": False,
    },
}


def test_generic_html_search(fake_http):
    """正则映射：逐行提取字段。"""
    fake_http.text_routes["/search"] = SEARCH_HTML
    provider = GenericHtmlIndexer(HTML_CONFIG)
    results = asyncio.run(provider.search("Some Show"))

    assert len(results) == 2, "无链接行应跳过"
    assert results[0].title == "Some.Show.S01E05.2160p.WEB-DL-Group"
    assert results[0].size == int(20.5 * 1024**3)
    assert results[0].seeders == 120
    assert results[0].leechers == 9
    assert results[0].kind == ResourceKind.MAGNET.value
    assert results[0].page_url == "https://html.test/details/1"
    # URL 模板占位符被正确替换
    assert "q=Some%20Show" in fake_http.calls[0][0]
    assert "page=1" in fake_http.calls[0][0]


def test_generic_html_magnet_only_fallback(fake_http):
    """magnet_only：不写正则也能抓出页面内所有磁力链并去重。"""
    html_text = (
        'x <a href="magnet:?xt=urn:btih:33333333333333333333333333333333&dn=My+Movie.2026.1080p">a</a>'
        ' y <a href="magnet:?xt=urn:btih:33333333333333333333333333333333">dup</a>'
        ' z magnet:?xt=urn:btih:44444444444444444444444444444444'
    )
    fake_http.text_routes["/search"] = html_text
    provider = GenericHtmlIndexer(
        {
            "name": "简易站",
            "url": "https://html.test",
            "options": {
                "search_url": "https://html.test/search?q={keyword}",
                "magnet_only": True,
                "local_filter": False,
            },
        }
    )
    results = asyncio.run(provider.search("My Movie"))
    assert len(results) == 2, "同 infohash 应去重"
    # dn 参数里的资源名被还原
    assert results[0].title == "My Movie.2026.1080p"


def test_generic_html_invalid_regex_is_safe(fake_http):
    """用户写错正则不应让整站抛异常。"""
    fake_http.text_routes["/search"] = SEARCH_HTML
    provider = GenericHtmlIndexer(
        {
            "name": "坏正则站",
            "url": "https://html.test",
            "options": {
                "search_url": "https://html.test/search?q={keyword}",
                "row_pattern": "([unclosed",
            },
        }
    )
    assert asyncio.run(provider.search("x")) == []


def test_generic_html_latest_and_health(fake_http):
    """最新页与健康检查。"""
    fake_http.text_routes["/latest"] = SEARCH_HTML
    provider = GenericHtmlIndexer(HTML_CONFIG)
    assert len(asyncio.run(provider.fetch_latest(limit=5))) == 2
    ok, message = asyncio.run(provider.health_check())
    assert ok is True and "最新页" in message


def test_generic_html_requires_config():
    """缺配置时优雅降级。"""
    provider = GenericHtmlIndexer({"name": "空", "options": {}})
    assert asyncio.run(provider.search("x")) == []
    ok, _ = asyncio.run(provider.health_check())
    assert ok is False


# ---------------------------------------------------------------- 导航站发现
ONENAV_HTML = """
<div class="row io-mx-n2">
  <div class="url-card"><div class="url-body mini">
    <a href="javascript:" class="card is-views site-3208" data-target="sitewindow"
       data-id="3208" data-url="https://uz998.com"
       title="影视、直播、漫画、小说一站式追剧APP" data-toggle="tooltip">
      <div class="card-body"><div class="url-content">
        <div class="url-info"><div class="text-sm overflowClip_1"> 蓝光追剧神器 </div></div>
      </div></div>
    </a>
  </div></div>
  <div class="url-card"><div class="url-body mini">
    <a href="https://gaze.red/" target="_blank" rel="external nofollow"
       data-id="143" data-url="https://gaze.red"
       class="card no-c is-views site-143"
       title="注视影视：海外影视剧、国内高口碑影视剧资源为主的在线影视网站">
      <div class="text-sm overflowClip_1"> 注视影视 </div>
    </a>
  </div></div>
  <div class="url-card">
    <a href="javascript:" data-id="2594" data-url="https://xinghuo.xfyun.cn/desk/"
       class="yh-link is-views site-2594" title="讯飞星火：讯飞星火AI助手">
      <div class="text-sm overflowClip_1"> 讯飞星火 </div>
    </a>
  </div>
  <div class="url-card">
    <a href="/ios-app/" target="_blank" data-id="3731" data-url="/ios-app"
       class="card is-views site-3731" title="站内页面不是资源站">站内</a>
  </div>
  <div class="url-card">
    <a href="javascript:" data-id="3208" data-url="https://uz998.com"
       class="site-togoicon is-views site-3208">重复卡片</a>
  </div>
</div>
"""


def test_parse_directory_extracts_sites():
    """解析 OneNav 导航站卡片。"""
    sites = discovery.parse_directory(ONENAV_HTML, source="硬核指南")
    by_domain = {item.domain: item for item in sites}

    assert "uz998.com" in by_domain
    assert "gaze.red" in by_domain
    # 站内相对链接不算资源站
    assert not any("ios-app" in item.url for item in sites)
    # 同域名去重
    assert len([s for s in sites if s.domain == "uz998.com"]) == 1

    first = by_domain["uz998.com"]
    assert first.name == "蓝光追剧神器"
    assert "追剧" in first.description
    assert first.media_related is True
    assert "追剧" in first.tags
    assert first.source == "硬核指南"

    # AI 工具站不应被判为影视相关
    assert by_domain["xinghuo.xfyun.cn"].media_related is False


def test_parse_directory_name_from_title():
    """没有卡片展示名时从 title 的「名称：简介」取名。"""
    sites = discovery.parse_directory(ONENAV_HTML, source="x")
    gaze = next(s for s in sites if s.domain == "gaze.red")
    assert gaze.name == "注视影视"


def test_parse_directory_plain_link_fallback():
    """非卡片结构退化为抓取外链。"""
    html_text = (
        '<a href="https://movie.test/">高清影视资源站</a>'
        '<a href="https://tool.test/">在线工具</a>'
    )
    sites = discovery.parse_directory(html_text, source="https://nav.test")
    assert {s.domain for s in sites} == {"movie.test", "tool.test"}
    assert next(s for s in sites if s.domain == "movie.test").media_related is True


def test_parse_directory_empty():
    """空页面安全返回。"""
    assert discovery.parse_directory("", source="x") == []


def test_discover_filters_media_only(fake_http):
    """discover：media_only 过滤掉非影视站点。"""
    fake_http.text_routes["yinghezhinan"] = ONENAV_HTML

    result = asyncio.run(discovery.discover(media_only=True))
    domains = {item["domain"] for item in result["sites"]}
    assert "uz998.com" in domains
    assert "xinghuo.xfyun.cn" not in domains
    assert result["errors"] == []

    everything = asyncio.run(discovery.discover(media_only=False))
    assert everything["total"] > result["total"]


def test_discover_handles_fetch_failure(fake_http):
    """导航站抓取失败时返回明确错误而不抛异常。"""
    result = asyncio.run(discovery.discover(url="https://down.test/"))
    assert result["sites"] == []
    assert result["errors"] and "抓取失败" in result["errors"][0]


# ---------------------------------------------------------------- 预设与注册
def test_new_providers_registered():
    """新增的自定义站点 Provider 均已注册。"""
    names = {item["name"] for item in list_providers()}
    for expected in ("api_generic", "html_generic", "mukaku"):
        assert expected in names, expected


def test_presets_are_wellformed():
    """每个预设都能直接用于创建站点。"""
    from app.providers.registry import get_provider_class

    items = presets.list_presets()
    assert items
    for item in items:
        assert {"id", "name", "kind", "provider", "options"} <= set(item)
        assert get_provider_class(item["provider"]) is not None, item["provider"]
    assert presets.get_preset("mukaku") is not None
    assert presets.get_preset("不存在") is None


def test_presets_kind_filter():
    """按类别过滤预设。"""
    pan = presets.list_presets("pan")
    assert pan and all(item["kind"] == "pan" for item in pan)
