"""RSS 追新与更新检测回归用例（v1.18.0）。

这些用例守的是**静默失败**：RSS 解析丢字段、增量判据失效、更新检测在
没有 Release 的仓库上永远回答"已是最新"——都不会报错，只是结果不对。
"""

from __future__ import annotations

from app.core import rss_dialects
from app.services import rss_feeds, update_check

# ---------------- RSS 方言解析 ----------------

NYAA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:nyaa="https://nyaa.si/xmlns/nyaa">
<channel><title>Nyaa - Anime - Torrent File RSS</title><link>https://nyaa.si/</link>
<item>
  <title>[SubsPlease] Foo Bar - 09 (1080p) [ABCD1234].mkv</title>
  <link>https://nyaa.si/download/1900001.torrent</link>
  <guid isPermaLink="true">https://nyaa.si/view/1900001</guid>
  <pubDate>Tue, 02 Sep 2026 12:00:00 -0000</pubDate>
  <nyaa:seeders>120</nyaa:seeders>
  <nyaa:leechers>7</nyaa:leechers>
  <nyaa:downloads>900</nyaa:downloads>
  <nyaa:size>1.4 GiB</nyaa:size>
  <nyaa:infoHash>abcdef0123456789</nyaa:infoHash>
  <enclosure url="" length="0" type="application/x-bittorrent" />
</item>
</channel></rss>
"""

MIKAN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torrent="https://mikanani.me/0.1/">
<channel><title>Mikan Project - 我的番组</title><link>http://mikanani.me/RSS/MyBangumi</link>
<item>
  <title>[Nekomoe kissaton] Foo Bar [09][1080p][JPSC]</title>
  <link>https://mikanani.me/Home/Episode/aaaabbbbccccdddd</link>
  <torrent xmlns="https://mikanani.me/0.1/">
    <link>https://mikanani.me/Home/Episode/aaaabbbbccccdddd</link>
    <contentLength>591224832</contentLength>
    <pubDate>2026-09-02T20:00:00</pubDate>
  </torrent>
  <enclosure type="application/x-bittorrent" length="591224832"
    url="https://mikanani.me/Download/20260902/aaaabbbbccccdddd.torrent" />
</item>
</channel></rss>
"""

DMHY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>【動漫花園】</title><link>http://share.dmhy.org</link>
<item>
  <title>[Sakurato] Foo Bar [09][HEVC-10bit 1080p AAC][CHS]</title>
  <link>http://share.dmhy.org/topics/view/700001_x.html</link>
  <description>&lt;p&gt;&lt;strong&gt;Size&lt;/strong&gt;: 456.7MB&lt;/p&gt;</description>
  <enclosure url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"
    length="0" type="application/x-bittorrent" />
</item>
</channel></rss>
"""


def test_nyaa_size_and_seeders_from_namespace():
    """Nyaa 的体积/做种数只在 nyaa: 命名空间里，enclosure 是空的。

    只读 enclosure 会让全站结果 size=0/seeders=0：设了最小做种数就被整站
    滤光，界面显示"0 条"却不报错——最难发现的一类故障。
    """
    title, dialect, entries = rss_dialects.parse_feed(NYAA_XML, url="https://x/rss")
    assert dialect == "nyaa"
    assert "Nyaa" in title
    assert len(entries) == 1
    entry = entries[0]
    assert entry.size == 1503238553, entry.size
    assert entry.seeders == 120
    assert entry.leechers == 7
    assert entry.grabs == 900
    # Nyaa 的 <link> 本身就是种子地址，不是详情页
    assert entry.link.endswith(".torrent")


def test_mikan_uses_enclosure_and_keeps_detail_page():
    """Mikan 的种子在 enclosure，<link> 是详情页（要留着给界面跳转）。"""
    _title, dialect, entries = rss_dialects.parse_feed(MIKAN_XML, url="https://x/rss")
    assert dialect == "mikan"
    entry = entries[0]
    assert entry.link.endswith(".torrent")
    assert entry.homepage and "Episode" in entry.homepage
    assert entry.size == 591224832


def test_dmhy_size_from_description_with_html_tags():
    """dmhy 把体积写在正文里，且标签夹在关键词与冒号之间。

    ``<strong>Size</strong>: 456.7MB`` —— 不先剥标签的正则匹配不上，
    结果 size=0（评分里体积项恒 0，这些资源永远排最后）。
    """
    _title, dialect, entries = rss_dialects.parse_feed(DMHY_XML, url="https://x/rss")
    assert dialect == "dmhy"
    entry = entries[0]
    assert entry.is_magnet, entry.link
    assert entry.size > 400_000_000, entry.size


def test_dialect_detected_from_feed_self_description_not_url():
    """镜像域名认不出时要靠 feed 自述兜底，否则镜像站全退化成 generic。"""
    _t, dialect, _e = rss_dialects.parse_feed(
        MIKAN_XML, url="https://my-private-mirror.example.com/feed.xml"
    )
    assert dialect == "mikan"


def test_bad_xml_and_empty_input_do_not_raise():
    """坏 XML 不能把整轮巡检带崩（一个源坏了不该影响其它源）。"""
    assert rss_dialects.parse_feed("<not xml", url="https://x") == ("", "generic", [])
    assert rss_dialects.parse_feed("", url="https://x") == ("", "generic", [])


def test_generic_fallback_still_returns_items():
    """认不出方言也要出结果，只是字段可能不全。"""
    xml = """<rss><channel><title>Some Private Tracker</title>
    <item><title>Foo.S01E01.1080p</title>
    <link>https://pt.example/download/1</link></item></channel></rss>"""
    _t, dialect, entries = rss_dialects.parse_feed(xml, url="https://pt.example/rss")
    assert dialect == "generic"
    assert len(entries) == 1
    assert entries[0].title.startswith("Foo")


# ---------------- 过滤优先级 ----------------


def test_exclude_beats_include():
    """同时命中包含与排除时判为不要：排除词的意图更明确。"""
    assert rss_feeds.title_allowed("[生肉] Foo 01", "Foo", "生肉") is False
    assert rss_feeds.title_allowed("[简中] Foo 01", "Foo", "生肉") is True


def test_broken_regex_is_ignored_not_fatal():
    """用户写错正则不该让整条源永远失败。"""
    assert rss_feeds.title_allowed("Foo", "[unclosed") is True
    assert rss_feeds._compile("[unclosed") is None
    assert rss_feeds._compile("") is None
    assert rss_feeds._compile(None) is None


# ---------------- 更新检测 ----------------


def test_version_compare_rules():
    assert update_check.is_newer("1.18.0", "1.17.0") is True
    assert update_check.is_newer("v1.18.0", "1.17.0") is True
    assert update_check.is_newer("1.17.0", "1.17.0") is False
    assert update_check.is_newer("1.16.9", "1.17.0") is False
    assert update_check.is_newer("1.17.1", "1.17.0") is True
    assert update_check.is_newer("2.0.0", "1.99.99") is True
    # 预发布号视为低于同号正式版
    assert update_check.is_newer("1.18.0-rc1", "1.18.0") is False
    assert update_check.is_newer("1.18.0", "1.18.0-rc1") is True
    # 解析不出时宁可不提示也不误报
    assert update_check.is_newer("latest", "1.17.0") is False
    assert update_check.is_newer(None, "1.17.0") is False


def test_deployment_mode_is_one_of_known_values():
    assert update_check.deployment_mode() in ("source", "docker")


def _git_call_args() -> list[list[str]]:
    """从源码里取出所有 ``_git(...)`` 的字面量参数。

    这里刻意做 AST 分析而不是全文 grep：文档字符串里**必须**能写清楚
    "我们为什么不用 reset --hard"，grep 会把这段解释当成违规。
    """
    import ast
    import pathlib as _p

    tree = ast.parse(
        _p.Path("app/services/update_check.py").read_text("utf-8")
    )
    calls: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "_git":
            continue
        calls.append(
            [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        )
    return calls


def test_apply_update_never_resets_or_merges():
    """更新只允许 ``git pull --ff-only``：不 merge、不 reset，绝不丢用户改动。

    ``reset --hard`` 会直接抹掉用户在容器/源码里的本地修改，自动 merge 则
    产生他看不懂的冲突——两者都比"更新失败"糟糕得多。
    """
    calls = _git_call_args()
    assert calls, "没有找到任何 _git 调用，更新逻辑可能被改写了"
    flat = [arg for call in calls for arg in call]
    assert "--ff-only" in flat
    assert "reset" not in flat
    assert "--hard" not in flat
    assert "merge" not in flat
    # 自更新绝不碰 docker.sock：那等于把宿主机控制权交给本进程
    import ast
    import pathlib as _p

    tree = ast.parse(_p.Path("app/services/update_check.py").read_text("utf-8"))
    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    # 排除文档字符串（它们正是用来解释"为什么不碰"的）
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    assert not [
        text for text in literals if "docker.sock" in text and text not in docstrings
    ]


def test_docker_mode_does_not_pretend_to_update(monkeypatch):
    """容器部署要如实说"我换不了自己的镜像"并给出命令，而不是假装成功。"""
    monkeypatch.setattr(update_check, "deployment_mode", lambda: "docker")
    result = update_check.apply_update()
    assert result["success"] is False
    assert result.get("commands")
    assert any("docker compose" in cmd for cmd in result["commands"])


# ---------------- API 与增量 ----------------


def test_rss_feed_crud_and_guid_increment(client, auth_headers):
    """建源 → 首次拉取只记账 → 再拉没有新条目 → 重置后重新全量。"""
    created = client.post(
        "/api/v1/rss-feeds",
        headers=auth_headers,
        json={
            "name": "回归用例源",
            "url": "https://example.invalid/regression-feed.xml",
            "aggregate": True,
        },
    )
    assert created.status_code == 200, created.text
    feed = created.json()["data"]
    feed_id = feed["id"]
    assert feed["handled_count"] == 0
    assert feed["skip_existing"] is True

    # 同一 URL 再添加返回既有记录而不是 500
    again = client.post(
        "/api/v1/rss-feeds",
        headers=auth_headers,
        json={"name": "dup", "url": "https://example.invalid/regression-feed.xml"},
    )
    assert again.status_code == 200
    assert again.json()["data"]["id"] == feed_id

    listing = client.get("/api/v1/rss-feeds", headers=auth_headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] >= 1
    assert "stats" in body
    assert any(item["id"] == feed_id for item in body["items"])

    # 拉不通的地址要如实失败并计数，而不是静默"成功但 0 条"
    checked = client.post(f"/api/v1/rss-feeds/{feed_id}/check", headers=auth_headers)
    assert checked.status_code == 200
    assert checked.json()["success"] is False

    # 先手动停用，再确认 reset_failures 会把它一并恢复启用——
    # 否则用户点了「重置」却发现源还是不跑，只会以为按钮没生效
    disabled = client.patch(
        f"/api/v1/rss-feeds/{feed_id}", headers=auth_headers, json={"enabled": False}
    )
    assert disabled.status_code == 200
    assert disabled.json()["data"]["enabled"] is False

    patched = client.patch(
        f"/api/v1/rss-feeds/{feed_id}",
        headers=auth_headers,
        json={"reset_failures": True, "max_per_run": 3, "aggregate": False},
    )
    assert patched.status_code == 200
    data = patched.json()["data"]
    assert data["failure_count"] == 0
    assert data["enabled"] is True, "reset_failures 应同时恢复被自动停用的源"
    assert data["max_per_run"] == 3
    assert data["aggregate"] is False

    assert client.delete(
        f"/api/v1/rss-feeds/{feed_id}", headers=auth_headers
    ).status_code == 200


def test_rss_feed_missing_returns_404(client, auth_headers):
    assert client.post(
        "/api/v1/rss-feeds/999999/check", headers=auth_headers
    ).status_code == 404
    assert client.patch(
        "/api/v1/rss-feeds/999999", headers=auth_headers, json={"name": "x"}
    ).status_code == 404
    assert client.delete(
        "/api/v1/rss-feeds/999999", headers=auth_headers
    ).status_code == 404


def test_rss_dialects_endpoint_explains_field_differences(client, auth_headers):
    """界面要能告诉用户"这个站拿不到做种数"，否则用户会以为是程序坏了。"""
    response = client.get("/api/v1/rss-feeds/dialects", headers=auth_headers)
    assert response.status_code == 200
    items = response.json()["items"]
    keys = {item["key"] for item in items}
    assert {"mikan", "nyaa", "dmhy", "generic"} <= keys
    assert all(item["note"] for item in items)


def test_rss_preview_reports_failure_clearly(client, auth_headers):
    """预览拉不通时给可操作的提示，而不是空列表 + success=true。"""
    response = client.post(
        "/api/v1/rss-feeds/preview",
        headers=auth_headers,
        json={"url": "https://example.invalid/nope.xml"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert "RSS" in body["message"] or "Cookie" in body["message"]


def test_update_check_endpoint_reports_its_source(client, auth_headers):
    """结论必须带 source：仓库没有 Release 时要走 branch 兜底而不是谎称最新。"""
    response = client.get("/api/v1/system/update/check", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["current"]
    assert body["mode"] in ("source", "docker")
    assert body["source"] in ("release", "branch", "")
    assert isinstance(body["has_update"], bool)


def test_rss_job_registered_in_scheduler():
    """定时任务没注册的话，界面上加了 RSS 源永远等不到动静。"""
    from app.services.scheduler import JOB_RSS, builtin_specs

    specs = {spec.key: spec for spec in builtin_specs()}
    assert "rss" in specs
    assert specs["rss"].job_id == JOB_RSS


def _fake_entry(title: str, guid: str) -> rss_dialects.RssEntry:
    return rss_dialects.RssEntry(
        title=title,
        link=f"https://example.invalid/{guid}.torrent",
        size=1024 * 1024 * 500,
        seeders=10,
        guid=guid,
    )


def test_first_run_accounts_history_then_only_new_entries(
    client, auth_headers, monkeypatch
):
    """首次拉取只记账，之后按 guid 判增量。

    不做「首次只记账」的话，用户新加一条老 RSS 会**立刻投出几十个下载任务**；
    而增量若按 pubDate 判断，遇到重发/修种刷新时间的站点会重复下载。
    """
    import asyncio

    entries = [_fake_entry("测试番 - 01 [1080p]", "g1"),
               _fake_entry("测试番 - 02 [1080p]", "g2")]

    async def fake_fetch(url, *, cookie=None, timeout=None):
        return ("伪造源", "mikan", list(entries))

    monkeypatch.setattr(rss_feeds, "fetch_entries", fake_fetch)

    created = client.post(
        "/api/v1/rss-feeds",
        headers=auth_headers,
        json={"name": "增量用例源", "url": "https://example.invalid/incr.xml"},
    )
    feed_id = created.json()["data"]["id"]
    try:
        first = asyncio.run(rss_feeds.check_feed(feed_id, notify=False))
        assert first["first_run"] is True, first
        assert first["downloaded"] == 0
        assert first["total"] == 2

        feeds = {
            item["id"]: item
            for item in client.get("/api/v1/rss-feeds", headers=auth_headers).json()["items"]
        }
        assert feeds[feed_id]["handled_count"] == 2

        second = asyncio.run(rss_feeds.check_feed(feed_id, notify=False))
        assert second.get("first_run") is not True
        assert second["new"] == 0, second

        entries.append(_fake_entry("测试番 - 03 [1080p]", "g3"))
        third = asyncio.run(rss_feeds.check_feed(feed_id, notify=False))
        assert third["new"] == 1, third

        # 方言由 feed 自述判定后落库，省掉后续重复判断
        feeds = {
            item["id"]: item
            for item in client.get("/api/v1/rss-feeds", headers=auth_headers).json()["items"]
        }
        assert feeds[feed_id]["dialect"] == "mikan"
        assert feeds[feed_id]["handled_count"] == 3
    finally:
        client.delete(f"/api/v1/rss-feeds/{feed_id}", headers=auth_headers)


def test_aggregate_feed_skips_entries_without_subscribe(
    client, auth_headers, monkeypatch
):
    """聚合流里没命中订阅的条目必须跳过并说明原因。

    聚合 RSS 一条流混着几十部作品，不做匹配就全量下载 = 把整站新番拖回来。
    """
    import asyncio

    async def fake_fetch(url, *, cookie=None, timeout=None):
        return (
            "伪造聚合源",
            "mikan",
            [_fake_entry("某部没订阅的番 - 05 [1080p]", "x1")],
        )

    monkeypatch.setattr(rss_feeds, "fetch_entries", fake_fetch)
    created = client.post(
        "/api/v1/rss-feeds",
        headers=auth_headers,
        json={
            "name": "聚合用例源",
            "url": "https://example.invalid/agg.xml",
            "aggregate": True,
            "skip_existing": False,
        },
    )
    feed_id = created.json()["data"]["id"]
    try:
        result = asyncio.run(rss_feeds.check_feed(feed_id, notify=False))
        assert result["downloaded"] == 0
        assert result["aggregate"] is True
        assert any("订阅" in item["reason"] for item in result["skipped"]), result
    finally:
        client.delete(f"/api/v1/rss-feeds/{feed_id}", headers=auth_headers)


def test_dry_run_does_not_consume_guids(client, auth_headers, monkeypatch):
    """试运行不能写回 guid：否则试跑一次，真巡检时就什么都不下了。"""
    import asyncio

    async def fake_fetch(url, *, cookie=None, timeout=None):
        return ("伪造源", "mikan", [_fake_entry("试运行番 - 01", "d1")])

    monkeypatch.setattr(rss_feeds, "fetch_entries", fake_fetch)
    created = client.post(
        "/api/v1/rss-feeds",
        headers=auth_headers,
        json={
            "name": "试运行用例源",
            "url": "https://example.invalid/dry.xml",
            "skip_existing": False,
        },
    )
    feed_id = created.json()["data"]["id"]
    try:
        asyncio.run(rss_feeds.check_feed(feed_id, dry_run=True, notify=False))
        feeds = {
            item["id"]: item
            for item in client.get("/api/v1/rss-feeds", headers=auth_headers).json()["items"]
        }
        assert feeds[feed_id]["handled_count"] == 0, "dry_run 不应写回 guid"
    finally:
        client.delete(f"/api/v1/rss-feeds/{feed_id}", headers=auth_headers)


def test_handled_guids_are_capped(client, auth_headers):
    """``handled_guids`` 必须有上限，否则这一列会随时间无限膨胀。

    RSS 每轮都返回全量，几年下来 JSON 里会攒下几万个 guid；这一列每轮读写，
    膨胀后既拖慢巡检又把数据库撑大，而且**不会报任何错**。
    """
    created = client.post(
        "/api/v1/rss-feeds",
        headers=auth_headers,
        json={"name": "上限用例源", "url": "https://example.invalid/cap.xml"},
    )
    feed_id = created.json()["data"]["id"]
    try:
        overflow = rss_feeds.MAX_HANDLED + 50
        rss_feeds._apply_success(
            feed_id,
            [f"guid-{index}" for index in range(overflow)],
            dialect="mikan",
            downloaded=0,
            message="上限用例",
        )
        feeds = {
            item["id"]: item
            for item in client.get("/api/v1/rss-feeds", headers=auth_headers).json()["items"]
        }
        assert feeds[feed_id]["handled_count"] == rss_feeds.MAX_HANDLED, (
            f"应裁剪到 {rss_feeds.MAX_HANDLED} 条，实际 {feeds[feed_id]['handled_count']}"
        )
    finally:
        client.delete(f"/api/v1/rss-feeds/{feed_id}", headers=auth_headers)
