"""awesome-zhuiju-free 社区清单接入 + KK 系网盘搜索（v1.14.0）。

**这一轮最重要的实测事实**：上游的「可访问」不等于「搜得到」。

`awesome-zhuiju-free` 每天用 GitHub Actions 跑一次可用性检测，但方式是
`GET 首页` 看 HTTP 状态码。逐站实测 20 个搜索类候选站后：

    上游 reachable = 14 个，我们真搜一次能拿到可下载链接的 = 4 个

那 10 个差额全是「首页 200 / 搜索页 200 / 但页面里一条链接都没有」——正是
ADR-20 说的最难发现的故障。若直接信上游把它们塞进搜索链路，用户每次搜索
都要多等这些站的超时然后收获 0 条（v1.13.0 刚修完的正是这类问题）。

因此这些测试钉住三条底线：

1. `probe` 四档判定必须能区分「搜到了」/「能开但搜不到」/「被拦」/「未知」；
2. 只有 `searchable` 档允许一键落库（`suggest_site`）；
3. 上游拉取失败必须回退缓存而不是让整个清单页开不出来。

KK 系（`kkso.net` / `zhuiju.us` 同模板）的解析要点同样来自实测：提取码
**不在** `copyText()` 第三参（实测恒为空串），而拼在链接的 `?pwd=` 上。
"""

from __future__ import annotations

import asyncio
import json

from app.providers.pan.kkso import KksoProvider, _clean_title
from app.services import zhuiju

# ---------------------------------------------------------------- KK 系解析

#: 取自 kkso.net / zhuiju.us 搜索页的真实结构（已裁剪）
KKSO_HTML = """
<div class="result">
  <a href="javascript:;" onclick="linkBtn(this)" data-index="0" class="title">
      【国剧】2019.庆余年.全集    </a>
  <div class="type time">2025-10-04</div>
  <div class="type"><span>来源：夸克网盘</span></div>
  <div class="btns">
    <div class="btn" @click.stop="copyText($event,'【国剧】2019.庆余年.全集','https://pan.quark.cn/s/2d5b405c3662','')">复制分享</div>
  </div>
</div>
<div class="result">
  <a href="javascript:;" class="title">庆余年1-2季+周年特别版【合集】4K</a>
  <div class="type time">2026-08-31</div>
  <div class="type"><span>来源：百度网盘</span></div>
  <div class="btns">
    <div class="btn" @click.stop="copyText($event,'庆余年1-2季+周年特别版【合集】4K','https://pan.baidu.com/s/1LaCUgIGAHFT13ZJawx6kCg?pwd=6666','')">复制分享</div>
  </div>
</div>
<div class="result">
  <a class="title">【夸克网盘】《庆余年》剧情&nbsp;/&amp;古装</a>
  <div class="type time">2026-08-31</div>
  <div class="type"><span>来源：迅雷网盘</span></div>
  <div class="btns">
    <div class="btn" @click.stop="copyText($event,'【夸克网盘】《庆余年》剧情&nbsp;/&amp;古装','https://pan.xunlei.com/s/VP-hsU4jXVzQudVyfjxxB8JWA1?pwd=4nrp','')">复制分享</div>
  </div>
</div>
"""


def _provider(url: str = "https://kkso.net") -> KksoProvider:
    return KksoProvider({"name": "KK", "url": url, "priority": 25})


def test_解析出全部三条资源():
    rows = _provider()._parse(KKSO_HTML)
    assert len(rows) == 3


def test_提取码从链接pwd取而不是copyText第三参():
    """实测 copyText 第三参恒为空串，只读它会导致所有资源都没有提取码。"""
    rows = _provider()._parse(KKSO_HTML)
    by_link = {r.link: r for r in rows}
    assert by_link["https://pan.baidu.com/s/1LaCUgIGAHFT13ZJawx6kCg?pwd=6666"].password == "6666"
    assert by_link["https://pan.xunlei.com/s/VP-hsU4jXVzQudVyfjxxB8JWA1?pwd=4nrp"].password == "4nrp"


def test_没有提取码的条目password为None():
    rows = _provider()._parse(KKSO_HTML)
    quark = next(r for r in rows if "quark" in r.link)
    assert quark.password is None


def test_链接保留完整pwd参数不被截断():
    """转存时要用完整链接，截掉 ?pwd= 会导致转存失败。"""
    rows = _provider()._parse(KKSO_HTML)
    baidu = next(r for r in rows if "baidu" in r.link)
    assert baidu.link.endswith("?pwd=6666")


def test_标题反转义html实体():
    """标题带 &nbsp;/&amp; 不反转义会污染季集识别。"""
    rows = _provider()._parse(KKSO_HTML)
    titles = " ".join(r.title for r in rows)
    assert "&nbsp;" not in titles and "&amp;" not in titles


def test_网盘类型按域名标注():
    rows = _provider()._parse(KKSO_HTML)
    sites = {r.site for r in rows}
    assert "KK·夸克网盘" in sites
    assert "KK·百度网盘" in sites
    assert "KK·迅雷网盘" in sites


def test_资源类型是pan():
    rows = _provider()._parse(KKSO_HTML)
    assert {r.kind for r in rows} == {"pan"}


def test_日期按行对应写入publish_at():
    rows = _provider()._parse(KKSO_HTML)
    quark = next(r for r in rows if "quark" in r.link)
    assert quark.publish_at is not None
    assert quark.publish_at.date().isoformat() == "2025-10-04"


def test_重复链接去重():
    rows = _provider()._parse(KKSO_HTML + KKSO_HTML)
    assert len(rows) == 3


def test_空页面返回空列表():
    assert _provider()._parse("") == []
    assert _provider()._parse("<html><body>no results</body></html>") == []


def test_非http链接被丢弃():
    html = """<div @click.stop="copyText($event,'标题','javascript:alert(1)','')">x</div>"""
    assert _provider()._parse(html) == []


def test_标题为空的行被丢弃():
    html = """<div @click.stop="copyText($event,'','https://pan.quark.cn/s/abc','')">x</div>"""
    assert _provider()._parse(html) == []


def test_搜索地址按模板拼接并转义关键词():
    url = _provider()._search_url("庆余年", 0)
    assert url.startswith("https://kkso.net/s/")
    assert "%E5%BA%86%E4%BD%99%E5%B9%B4" in url
    assert url.endswith("?p=1")


def test_分页从1开始():
    """page 是 0-based，站点是 1-based，接反了会永远拿第二页。"""
    assert _provider()._search_url("x", 0).endswith("?p=1")
    assert _provider()._search_url("x", 1).endswith("?p=2")


def test_搜索地址可被options覆盖():
    provider = KksoProvider({
        "name": "KK", "url": "https://x.com",
        "options": {"search_url": "{base}/find/{keyword}?page={page}"},
    })
    assert provider._search_url("abc", 0) == "https://x.com/find/abc?page=1"


def test_未配置地址时搜索返回空():
    provider = KksoProvider({"name": "KK", "url": ""})
    assert asyncio.run(provider.search("庆余年")) == []


def test_空关键词不发请求():
    assert asyncio.run(_provider().search("")) == []


def test_clean_title压缩空白():
    assert _clean_title("  a   b  ") == "a b"


def test_clean_title去掉标签():
    assert "<b>" not in _clean_title("<b>粗体</b>标题")


def test_未配置地址时健康检查失败():
    ok, msg = asyncio.run(KksoProvider({"name": "KK", "url": ""}).health_check())
    assert ok is False
    assert "未配置" in msg


def test_provider注册信息():
    assert KksoProvider.name == "kkso"
    assert KksoProvider.kind == "pan"
# ---------------------------------------------------- 社区清单：合并与筛选

#: 上游 resources.json 的真实结构（裁剪到我们用到的字段）
UPSTREAM_RESOURCES = {
    "version": 1,
    "updated_at": "2026-08-31",
    "resources": [
        {
            "id": "kuakeso", "name": "KK网盘搜", "url": "https://kkso.net",
            "category": "cloud_search", "summary": "网盘搜索",
            "summary_short": "网盘搜索", "tags": ["网盘搜索", "夸克"],
            "access": {"requires_login": False, "free_level": "unknown", "regions": ["GLOBAL"]},
            "verification": {"status": "caution", "last_checked": "2026-06-13"},
        },
        {
            "id": "gaoqingzu", "name": "HDZU", "url": "https://hdzu.org",
            "category": "magnet_search", "summary": "磁力",
            "tags": ["磁力"],
            "access": {"requires_login": True, "free_level": "unknown", "regions": ["GLOBAL"]},
            "verification": {"status": "caution", "last_checked": "2026-07-12"},
        },
        {
            # 在线影视：不该进搜索链路
            "id": "some-online", "name": "在线站", "url": "https://online.example",
            "category": "online_video", "summary": "在线播放",
            "tags": [],
            "access": {"requires_login": False, "free_level": "free", "regions": ["CN"]},
            "verification": {"status": "recommended", "last_checked": "2026-08-01"},
        },
        {
            # 开源项目：同样不是可搜索站点
            "id": "some-oss", "name": "某开源项目", "url": "https://github.com/a/b",
            "category": "open_source", "summary": "工具",
            "tags": [],
            "access": {"requires_login": False, "free_level": "free", "regions": ["GLOBAL"]},
            "verification": {"status": "recommended", "last_checked": "2026-08-01"},
        },
    ],
}

UPSTREAM_AVAILABILITY = {
    "version": 1,
    "generated_at": "2026-08-31T01:33:21.559Z",
    "results": [
        {"resource_id": "kuakeso", "url": "https://kkso.net", "status": "reachable",
         "http_status": 200},
        {"resource_id": "gaoqingzu", "url": "https://hdzu.org", "status": "restricted",
         "http_status": 403},
    ],
}


def test_只保留可搜索的两个分类():
    """在线影视/开源项目等不该进搜索链路。"""
    rows = zhuiju._merge(UPSTREAM_RESOURCES, UPSTREAM_AVAILABILITY)
    assert {r.id for r in rows} == {"kuakeso", "gaoqingzu"}


def test_合并上游每日可用性():
    rows = {r.id: r for r in zhuiju._merge(UPSTREAM_RESOURCES, UPSTREAM_AVAILABILITY)}
    assert rows["kuakeso"].reachability == "reachable"
    assert rows["kuakeso"].http_status == 200
    assert rows["gaoqingzu"].reachability == "restricted"
    assert rows["gaoqingzu"].http_status == 403


def test_没有可用性数据时标unknown():
    rows = {r.id: r for r in zhuiju._merge(UPSTREAM_RESOURCES, {"results": []})}
    assert rows["kuakeso"].reachability == "unknown"
    assert rows["kuakeso"].http_status is None


def test_命中已有provider时标注避免重复添加():
    rows = {r.id: r for r in zhuiju._merge(UPSTREAM_RESOURCES, UPSTREAM_AVAILABILITY)}
    assert rows["kuakeso"].known_provider == "kkso"
    assert rows["gaoqingzu"].known_provider == "html_generic"


def test_域名去掉www前缀():
    rows = zhuiju._merge(
        {"resources": [dict(UPSTREAM_RESOURCES["resources"][0], id="x",
                            url="https://www.zhuiju.us")]}, None)
    assert rows[0].domain == "zhuiju.us"


def test_登录要求被带出():
    rows = {r.id: r for r in zhuiju._merge(UPSTREAM_RESOURCES, UPSTREAM_AVAILABILITY)}
    assert rows["gaoqingzu"].requires_login is True
    assert rows["kuakeso"].requires_login is False


def test_非法条目被跳过():
    bad = {"resources": [
        {"id": "", "url": "https://a.com", "category": "magnet_search"},
        {"id": "ok", "url": "ftp://a.com", "category": "magnet_search"},
        {"id": "ok2", "category": "magnet_search"},
        "not-a-dict",
    ]}
    assert zhuiju._merge(bad, None) == []


def test_上游结构完全不对时返回空而不抛():
    assert zhuiju._merge(None, None) == []
    assert zhuiju._merge({"resources": None}, None) == []
    assert zhuiju._merge("garbage", None) == []


def test_分类标签有中文展示名():
    rows = zhuiju._merge(UPSTREAM_RESOURCES, UPSTREAM_AVAILABILITY)
    labels = {r.to_dict()["category_label"] for r in rows}
    assert labels == {"网盘搜索", "磁力 / BT"}


def test_stats按探测结论汇总():
    out = zhuiju.stats([
        {"probe": "searchable"}, {"probe": "searchable"},
        {"probe": "reachable_only"}, {"probe": "blocked"}, {},
    ])
    assert out["total"] == 5
    assert out["searchable"] == 2
    assert out["reachable_only"] == 1
    assert out["blocked"] == 1
    assert out["unknown"] == 1


# ---------------------------------------------------- 探测：四档判定

MAGNET = "magnet:?xt=urn:btih:78b2f90c4b3ec01c14e1f1ea5b7f002678f53222"


def _fake_get(mapping):
    """按 URL 前缀返回 (正文, 状态码)。"""
    async def _inner(url, *, timeout):
        for key, value in mapping.items():
            if key in url:
                return value
        return None, None
    return _inner


def test_探测到磁力判定searchable(monkeypatch):
    monkeypatch.setattr(zhuiju, "_get_with_status",
                        _fake_get({"?s=": (f'<a href="{MAGNET}">x</a>', 200)}))
    out = asyncio.run(zhuiju.probe_site("https://site.example"))
    assert out["probe"] == "searchable"
    assert out["magnets"] == 1


def test_探测到网盘链接判定searchable(monkeypatch):
    body = '<a href="https://pan.quark.cn/s/2d5b405c3662">x</a>'
    monkeypatch.setattr(zhuiju, "_get_with_status", _fake_get({"/s/": (body, 200)}))
    out = asyncio.run(zhuiju.probe_site("https://site.example"))
    assert out["probe"] == "searchable"
    assert out["pan_links"] == 1


def test_探测到种子判定searchable(monkeypatch):
    body = '<a href="/t/317576.torrent">x</a>'
    monkeypatch.setattr(zhuiju, "_get_with_status", _fake_get({"?s=": (body, 200)}))
    out = asyncio.run(zhuiju.probe_site("https://site.example"))
    assert out["probe"] == "searchable"
    assert out["torrents"] == 1


def test_首页能开但搜不到判定reachable_only(monkeypatch):
    """这是本模块存在的理由：上游会把这种站标成 reachable。"""
    monkeypatch.setattr(zhuiju, "_get_with_status",
                        _fake_get({"": ("<html>没有找到</html>", 200)}))
    out = asyncio.run(zhuiju.probe_site("https://site.example"))
    assert out["probe"] == "reachable_only"


def test_搜索有结果但链接在详情页也算reachable_only(monkeypatch):
    """关键词出现在页面里说明搜索生效，只是链接不在列表页。"""
    monkeypatch.setattr(zhuiju, "_get_with_status",
                        _fake_get({"": ("<html>庆余年 第二季</html>", 200)}))
    out = asyncio.run(zhuiju.probe_site("https://site.example"))
    assert out["probe"] == "reachable_only"
    assert "详情页" in out["probe_note"]


def test_403判定blocked且说明不做对抗(monkeypatch):
    monkeypatch.setattr(zhuiju, "_get_with_status", _fake_get({"": (None, 403)}))
    out = asyncio.run(zhuiju.probe_site("https://site.example"))
    assert out["probe"] == "blocked"
    assert "403" in out["probe_note"]
    assert "对抗" in out["probe_note"]


def test_WAF的468也判定blocked(monkeypatch):
    """cz4k 实测返回 468（SafeLine），不能漏进 reachable。"""
    monkeypatch.setattr(zhuiju, "_get_with_status", _fake_get({"": (None, 468)}))
    out = asyncio.run(zhuiju.probe_site("https://site.example"))
    assert out["probe"] == "blocked"
    assert "468" in out["probe_note"]


def test_连不上判定blocked(monkeypatch):
    monkeypatch.setattr(zhuiju, "_get_with_status", _fake_get({}))
    out = asyncio.run(zhuiju.probe_site("https://site.example"))
    assert out["probe"] == "blocked"
    assert "超时" in out["probe_note"]


def test_非法地址判定unknown():
    out = asyncio.run(zhuiju.probe_site("not-a-url"))
    assert out["probe"] == "unknown"


def test_探测命中即返回不再打其它形态(monkeypatch):
    """礼貌性要求：搜到了就停，不要把 5 种形态 × 3 个关键词全打一遍。"""
    calls = []

    async def _counting(url, *, timeout):
        calls.append(url)
        return f'<a href="{MAGNET}">x</a>', 200

    monkeypatch.setattr(zhuiju, "_get_with_status", _counting)
    asyncio.run(zhuiju.probe_site("https://site.example"))
    assert len(calls) == 1


# ---------------------------------------------------- 只允许可搜到的站落库

def _fake_cache(monkeypatch, entries):
    monkeypatch.setattr(zhuiju, "load", lambda: {"entries": entries})


def test_searchable才给建站配置(monkeypatch):
    _fake_cache(monkeypatch, [{
        "id": "kuakeso", "name": "KK", "url": "https://kkso.net",
        "domain": "kkso.net", "probe": "searchable", "known_provider": "kkso",
    }])
    out = zhuiju.suggest_site("kuakeso")
    assert out["ok"] is True
    assert out["provider"] == "kkso"
    assert out["kind"] == "pan"


def test_搜不到的站拒绝建站(monkeypatch):
    """加进来也搜不到东西，等于给用户一个「加了但没用」的坑。"""
    _fake_cache(monkeypatch, [{
        "id": "x", "name": "X", "url": "https://x.com", "domain": "x.com",
        "probe": "reachable_only", "probe_note": "搜不到链接",
    }])
    out = zhuiju.suggest_site("x")
    assert out["ok"] is False
    assert "搜不到链接" in out["reason"]


def test_被拦截的站拒绝建站(monkeypatch):
    _fake_cache(monkeypatch, [{
        "id": "x", "name": "X", "url": "https://x.com", "domain": "x.com",
        "probe": "blocked", "probe_note": "被拦截（HTTP 403）",
    }])
    assert zhuiju.suggest_site("x")["ok"] is False


def test_未探测的站拒绝建站(monkeypatch):
    _fake_cache(monkeypatch, [{
        "id": "x", "name": "X", "url": "https://x.com", "domain": "x.com",
        "probe": "unknown",
    }])
    assert zhuiju.suggest_site("x")["ok"] is False


def test_清单里没有该条目返回None(monkeypatch):
    _fake_cache(monkeypatch, [])
    assert zhuiju.suggest_site("nope") is None


def test_未知站点兜底用html_generic并带上探测到的搜索地址(monkeypatch):
    _fake_cache(monkeypatch, [{
        "id": "new", "name": "新站", "url": "https://new.example",
        "domain": "new.example", "probe": "searchable",
        "search_url": "https://new.example/?s={keyword}", "known_provider": None,
    }])
    out = zhuiju.suggest_site("new")
    assert out["provider"] == "html_generic"
    assert out["options"]["search_url"] == "https://new.example/?s={keyword}"
    assert out["options"]["magnet_only"] is True


# ---------------------------------------------------- 拉取与缓存回退

def test_上游拉不到时回退缓存而不是让页面开不出来(monkeypatch, tmp_path):
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({
        "fetched_at": 0, "count": 1, "entries": [{"id": "old", "probe": "searchable"}],
        "updated_at": "2026-01-01", "upstream_updated_at": "2026-01-01",
    }), encoding="utf-8")
    monkeypatch.setattr(zhuiju, "CACHE_FILE", cache)

    async def _fail(path):
        return None

    monkeypatch.setattr(zhuiju, "_fetch_json", _fail)
    out = asyncio.run(zhuiju.refresh(force=True))
    assert out["stale"] is True
    assert out["entries"][0]["id"] == "old"


def test_没有缓存又拉不到时给出可读错误(monkeypatch, tmp_path):
    monkeypatch.setattr(zhuiju, "CACHE_FILE", tmp_path / "missing.json")

    async def _fail(path):
        return None

    monkeypatch.setattr(zhuiju, "_fetch_json", _fail)
    out = asyncio.run(zhuiju.refresh(force=True))
    assert out["count"] == 0
    assert out["error"]


def test_刷新清单不丢已有探测结论(monkeypatch, tmp_path):
    """探测要打真实站点，不能每次同步清单就全部退回 unknown。"""
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({
        "fetched_at": 0,
        "entries": [{"id": "kuakeso", "probe": "searchable", "probe_note": "命中 5 条"}],
    }), encoding="utf-8")
    monkeypatch.setattr(zhuiju, "CACHE_FILE", cache)

    async def _ok(path):
        return UPSTREAM_RESOURCES if "resources" in path else UPSTREAM_AVAILABILITY

    monkeypatch.setattr(zhuiju, "_fetch_json", _ok)
    out = asyncio.run(zhuiju.refresh(force=True))
    rows = {e["id"]: e for e in out["entries"]}
    assert rows["kuakeso"]["probe"] == "searchable"
    assert rows["kuakeso"]["probe_note"] == "命中 5 条"
    assert rows["gaoqingzu"]["probe"] == "unknown"


def test_缓存新鲜时不打上游(monkeypatch, tmp_path):
    import time as _time
    cache = tmp_path / "c.json"
    cache.write_text(json.dumps({
        "fetched_at": _time.time(), "count": 1, "entries": [], "updated_at": "now",
    }), encoding="utf-8")
    monkeypatch.setattr(zhuiju, "CACHE_FILE", cache)
    called = []

    async def _spy(path):
        called.append(path)
        return None

    monkeypatch.setattr(zhuiju, "_fetch_json", _spy)
    out = asyncio.run(zhuiju.refresh())
    assert out["from_cache"] is True
    assert called == []


def test_同步开关关闭时跳过(monkeypatch):
    """开关关掉必须在打网络之前就返回，所以把出网入口全部换成会炸的替身。

    如果只断言 skipped，开关失效时这个用例会去打真站点，
    表现为「卡住 90 秒」而不是「失败」，CI 里等于没有防线。
    """
    monkeypatch.setattr(zhuiju.settings, "ZHUIJU_SYNC_ENABLED", False)

    async def _never(*args, **kwargs):
        raise AssertionError("开关已关闭，不应触碰网络")

    monkeypatch.setattr(zhuiju, "_fetch_json", _never)
    monkeypatch.setattr(zhuiju, "refresh", _never)
    monkeypatch.setattr(zhuiju, "probe_all", _never)
    monkeypatch.setattr(zhuiju, "probe_site", _never)
    monkeypatch.setattr(zhuiju, "_get_with_status", _never)
    out = asyncio.run(zhuiju.sync())
    assert out["skipped"] is True
    assert out["reason"]


def test_探测单站异常不拖垮整批(monkeypatch, tmp_path):
    monkeypatch.setattr(zhuiju, "CACHE_FILE", tmp_path / "c.json")
    monkeypatch.setattr(zhuiju, "load", lambda: {"entries": [
        {"id": "a", "url": "https://a.com"}, {"id": "b", "url": "https://b.com"},
    ]})

    async def _boom(url, **kwargs):
        if "a.com" in url:
            raise RuntimeError("boom")
        return {"probe": "searchable", "probe_note": "ok"}

    monkeypatch.setattr(zhuiju, "probe_site", _boom)
    out = asyncio.run(zhuiju.probe_all())
    assert out["probed"] == 2


def test_上游署名信息完整():
    """CC-BY-4.0 要求署名，接口/界面都要能显示来源。"""
    assert zhuiju.UPSTREAM_LICENSE == "CC-BY-4.0"
    assert zhuiju.UPSTREAM_REPO == "laoma2053/awesome-zhuiju-free"
    assert zhuiju.UPSTREAM_URL.startswith("https://github.com/")
