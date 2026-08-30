"""网页视频订阅（UP 主 / 频道追更）测试。

全程离线：``list_entries`` 与 ``download.add_download`` 都被替换掉。
覆盖重点是几个**如果写错就会静默失效**的地方：

* 增量判据必须是**视频 ID**（B 站扁平提取不给标题/日期，用标题去重会把
  所有条目当成同一个）；
* 标题缺失时 include/exclude 必须**放行**，否则 B 站订阅永远下不到东西；
* 首检 ``skip_existing`` 只记账不下载，否则订阅老 UP 会瞬间投几十个任务；
* 失败也要记 ID，否则一个永久失效的视频会把每轮配额吃光。
"""

from __future__ import annotations

import asyncio

import pytest

from app.schemas.enums import SubscribeStatus
from app.services import video_subscribe as service


def run(coro):
    return asyncio.run(coro)


def entry(video_id: str, title: str | None = None, url: str | None = None) -> dict:
    """构造一条扁平提取结果。title=None 模拟 B 站不返回标题的真实情况。"""
    return {
        "id": video_id,
        "title": title,
        "url": url or f"https://www.bilibili.com/video/{video_id}",
        "duration": 600,
        "uploader": "\u6d4b\u8bd5 UP",
    }


@pytest.fixture
def sub(client, auth_headers):
    """建一条订阅并在用例结束后删掉（避免污染其他用例的列表断言）。"""
    created: list[int] = []

    def _make(**overrides):
        payload = {
            "name": "\u6d4b\u8bd5\u8ba2\u9605",
            "url": "https://space.bilibili.com/946974",
            "check_limit": 10,
            "max_per_run": 3,
            "skip_existing": False,
        }
        payload.update(overrides)
        response = client.post(
            "/api/v1/video-subscribes", json=payload, headers=auth_headers
        )
        assert response.status_code == 200, response.text
        record = response.json()["data"]
        created.append(record["id"])
        return record

    yield _make
    for subscribe_id in created:
        client.delete(f"/api/v1/video-subscribes/{subscribe_id}", headers=auth_headers)


# ---------------- guess_site ----------------
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://space.bilibili.com/946974", "bilibili"),
        ("https://b23.tv/abcd", "bilibili"),
        ("https://www.youtube.com/@somechannel", "youtube"),
        ("https://youtu.be/xxxx", "youtube"),
        ("https://www.douyin.com/user/xxx", "douyin"),
        ("https://www.acfun.cn/u/123", "acfun"),
        ("https://example.com/feed", "other"),
        ("", "other"),
    ],
)
def test_guess_site(url, expected):
    assert service.guess_site(url) == expected


# ---------------- match_entries ----------------
def test_match_entries_skips_handled_ids():
    """已处理过的 ID 不再重复挑出——增量判据就是 ID。"""
    picked = service.match_entries(
        [entry("BV1"), entry("BV2"), entry("BV3")], handled=["BV1", "BV3"]
    )
    assert [item["id"] for item in picked] == ["BV2"]


def test_match_entries_passes_when_title_missing():
    """标题缺失时必须放行。

    这是本模块最关键的一条：B 站扁平提取实测 title=None，如果按
    "匹配不上 include 就跳过"处理，B 站订阅将永远下不到任何东西。
    """
    picked = service.match_entries(
        [entry("BV1", None)], include="\u5fc5\u987b\u5305\u542b\u8fd9\u4e32"
    )
    assert [item["id"] for item in picked] == ["BV1"]


def test_match_entries_exclude_also_passes_when_title_missing():
    """标题缺失时排除规则同样不该拦——宁可多下一个也不能整条失效。"""
    picked = service.match_entries([entry("BV1", None)], exclude="\u9884\u544a")
    assert [item["id"] for item in picked] == ["BV1"]


def test_match_entries_applies_include_when_title_present():
    """有标题时 include 正常生效。"""
    picked = service.match_entries(
        [entry("BV1", "\u7b2c12\u671f \u6d4b\u8bc4"), entry("BV2", "\u76f4\u64ad\u56de\u653e")],
        include="\u6d4b\u8bc4",
    )
    assert [item["id"] for item in picked] == ["BV1"]


def test_match_entries_applies_exclude_when_title_present():
    picked = service.match_entries(
        [entry("BV1", "\u6b63\u7247"), entry("BV2", "\u9884\u544a\u7247")],
        exclude="\u9884\u544a",
    )
    assert [item["id"] for item in picked] == ["BV1"]


def test_match_entries_bad_regex_does_not_crash():
    """用户填了坏正则时当没填，**不能让一个错正则搞崩整轮巡检**。"""
    picked = service.match_entries([entry("BV1", "\u6b63\u7247")], include="([unclosed")
    assert [item["id"] for item in picked] == ["BV1"]


def test_match_entries_dedupes_within_one_batch():
    """同一批里重复出现的 ID 只取一次。"""
    picked = service.match_entries([entry("BV1"), entry("BV1"), entry("BV2")])
    assert [item["id"] for item in picked] == ["BV1", "BV2"]


def test_match_entries_ignores_entries_without_id():
    """没有 ID 的条目无法做增量，直接丢弃。"""
    picked = service.match_entries([{"id": "", "title": "x"}, {"title": "y"}, entry("BV9")])
    assert [item["id"] for item in picked] == ["BV9"]


def test_match_entries_same_title_different_ids_all_kept():
    """标题相同但 ID 不同必须都算新投稿。

    如果误用标题做增量判据，"第 12 期"这种固定标题的系列就只会下到第一个。
    """
    picked = service.match_entries(
        [entry("BV1", "\u65e5\u5e38"), entry("BV2", "\u65e5\u5e38")]
    )
    assert [item["id"] for item in picked] == ["BV1", "BV2"]


# ---------------- _format_for ----------------
def test_format_for_none_means_best():
    assert service._format_for(None) is None
    assert service._format_for(0) is None


def test_format_for_caps_height():
    fmt = service._format_for(1080)
    assert "height<=1080" in fmt
    assert fmt.startswith("bestvideo")


# ---------------- CRUD ----------------
def test_create_infers_site(sub):
    record = sub(url="https://www.youtube.com/@test")
    assert record["site"] == "youtube"
    assert record["status"] == SubscribeStatus.ACTIVE.value


def test_create_clamps_limits(sub):
    """check_limit / max_per_run 要被夹到合法区间，不能存进离谱值。"""
    record = sub(check_limit=50, max_per_run=20)
    assert record["check_limit"] == 50
    assert record["max_per_run"] == 20


def test_list_and_delete(client, auth_headers, sub):
    record = sub()
    response = client.get("/api/v1/video-subscribes", headers=auth_headers)
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["items"]]
    assert record["id"] in ids

    assert (
        client.delete(
            f"/api/v1/video-subscribes/{record['id']}", headers=auth_headers
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/v1/video-subscribes/{record['id']}", headers=auth_headers
        ).status_code
        == 404
    )


def test_update_missing_returns_404(client, auth_headers):
    response = client.patch(
        "/api/v1/video-subscribes/999999", json={"name": "x"}, headers=auth_headers
    )
    assert response.status_code == 404


def test_to_dict_hides_handled_ids(client, auth_headers, sub):
    """响应体只给已处理 ID 的**数量**，不塞几百个 ID 进去。"""
    record = sub()
    response = client.get("/api/v1/video-subscribes", headers=auth_headers)
    row = next(item for item in response.json()["items"] if item["id"] == record["id"])
    assert "handled_ids" not in row
    assert row["handled_count"] == 0


# ---------------- 巡检：首检记账 ----------------
def test_first_run_records_history_without_downloading(monkeypatch, sub):
    """首检（skip_existing=True）只记账不下载。

    不这么做的话，订阅一个十年老 UP 会瞬间投出几十个下载任务把下载器打满。
    """
    record = sub(skip_existing=True)
    calls = []

    async def _list(url, *, limit=10):
        return [entry(f"BV{i}") for i in range(5)], ""

    async def _add(*args, **kwargs):
        calls.append(args)
        return {"id": 1}

    monkeypatch.setattr(service, "list_entries", _list)
    monkeypatch.setattr("app.services.download.add_download", _add)

    result = run(service.check_one(record["id"], notify=False))
    assert result["success"] is True
    assert result["downloaded"] == 0
    assert result["skipped_history"] == 5
    assert calls == [], "首检不该真的下载"


def test_second_run_downloads_only_new(monkeypatch, sub):
    """第二轮只下新出现的 ID。"""
    record = sub(skip_existing=True)
    batch = [entry(f"BV{i}") for i in range(3)]
    added: list[str] = []

    async def _list(url, *, limit=10):
        return list(batch), ""

    async def _add(payload, **kwargs):
        added.append(payload["title"])
        return {"id": len(added)}

    monkeypatch.setattr(service, "list_entries", _list)
    monkeypatch.setattr("app.services.download.add_download", _add)

    run(service.check_one(record["id"], notify=False))  # 首检记账
    batch.insert(0, entry("BVNEW", "\u65b0\u6295\u7a3f"))
    result = run(service.check_one(record["id"], notify=False))
    assert result["downloaded"] == 1
    assert added == ["\u65b0\u6295\u7a3f"]


def test_skip_existing_false_downloads_first_run(monkeypatch, sub):
    """明确不勾"跳过历史"时，首检就会下载（受 max_per_run 限制）。"""
    record = sub(skip_existing=False, max_per_run=2)
    added: list[str] = []

    async def _list(url, *, limit=10):
        return [entry(f"BV{i}") for i in range(5)], ""

    async def _add(payload, **kwargs):
        added.append(payload["title"])
        return {"id": len(added)}

    monkeypatch.setattr(service, "list_entries", _list)
    monkeypatch.setattr("app.services.download.add_download", _add)

    result = run(service.check_one(record["id"], notify=False))
    assert result["downloaded"] == 2
    assert len(added) == 2


def test_max_per_run_caps_downloads(monkeypatch, sub):
    """单轮下载数受 max_per_run 限制，防止一次把下载器打满。"""
    record = sub(skip_existing=False, max_per_run=1)

    async def _list(url, *, limit=10):
        return [entry(f"BV{i}") for i in range(9)], ""

    async def _add(payload, **kwargs):
        return {"id": 1}

    monkeypatch.setattr(service, "list_entries", _list)
    monkeypatch.setattr("app.services.download.add_download", _add)
    assert run(service.check_one(record["id"], notify=False))["downloaded"] == 1


def test_failed_download_still_records_id(monkeypatch, sub):
    """投递失败也要记 ID。

    否则一个永久失效的视频会在每一轮把配额吃光，后面的新投稿永远排不上。
    """
    record = sub(skip_existing=False, max_per_run=1)
    seen: list[str] = []

    async def _list(url, *, limit=10):
        return [entry("BVBAD"), entry("BVGOOD")], ""

    async def _add(payload, **kwargs):
        seen.append(payload["link"])
        return None  # 模拟投递失败

    monkeypatch.setattr(service, "list_entries", _list)
    monkeypatch.setattr("app.services.download.add_download", _add)

    result = run(service.check_one(record["id"], notify=False))
    assert result["downloaded"] == 0
    # 下一轮应当轮到 BVGOOD，而不是又卡在 BVBAD 上
    run(service.check_one(record["id"], notify=False))
    assert seen[-1].endswith("BVGOOD")


def test_no_new_entries_message(monkeypatch, sub):
    record = sub(skip_existing=False)

    async def _list(url, *, limit=10):
        return [], ""

    monkeypatch.setattr(service, "list_entries", _list)
    result = run(service.check_one(record["id"], notify=False))
    assert result["downloaded"] == 0
    assert result["success"] is True


# ---------------- 失败与自动暂停 ----------------
def test_failures_accumulate_and_auto_pause(monkeypatch, client, auth_headers, sub):
    """连续失败到阈值自动暂停，不再无意义重试。"""
    record = sub()

    async def _list(url, *, limit=10):
        return [], "\u5217\u8868\u63d0\u53d6\u5931\u8d25\uff1a404"

    monkeypatch.setattr(service, "list_entries", _list)
    for _ in range(service.MAX_FAILURES):
        result = run(service.check_one(record["id"], notify=False))
        assert result["success"] is False

    response = client.get("/api/v1/video-subscribes", headers=auth_headers)
    row = next(item for item in response.json()["items"] if item["id"] == record["id"])
    assert row["status"] == SubscribeStatus.PAUSED.value
    assert row["failure_count"] >= service.MAX_FAILURES


def test_paused_subscribe_is_skipped(monkeypatch, client, auth_headers, sub):
    """暂停的订阅巡检时直接跳过，不去打上游。"""
    record = sub()
    client.patch(
        f"/api/v1/video-subscribes/{record['id']}",
        json={"status": SubscribeStatus.PAUSED.value},
        headers=auth_headers,
    )
    calls = []

    async def _list(url, *, limit=10):
        calls.append(url)
        return [], ""

    monkeypatch.setattr(service, "list_entries", _list)
    result = run(service.check_one(record["id"], notify=False))
    assert result["skipped"] is True
    assert calls == []


def test_reset_failures_also_resumes_active(client, auth_headers, sub, monkeypatch):
    """清失败计数要一并把自动暂停的订阅恢复运行。

    否则用户点了「重置」却发现还是不跑，只会以为按钮没生效。
    """
    record = sub()

    async def _list(url, *, limit=10):
        return [], "boom"

    monkeypatch.setattr(service, "list_entries", _list)
    for _ in range(service.MAX_FAILURES):
        run(service.check_one(record["id"], notify=False))

    response = client.patch(
        f"/api/v1/video-subscribes/{record['id']}",
        json={"reset_failures": True},
        headers=auth_headers,
    )
    assert response.status_code == 200
    row = response.json()["data"]
    assert row["failure_count"] == 0
    assert row["status"] == SubscribeStatus.ACTIVE.value


def test_reset_history_clears_handled(monkeypatch, client, auth_headers, sub):
    """清空已处理记录后，下轮会把列表里的投稿重新当成新的。"""
    record = sub(skip_existing=True)

    async def _list(url, *, limit=10):
        return [entry("BV1"), entry("BV2")], ""

    monkeypatch.setattr(service, "list_entries", _list)
    run(service.check_one(record["id"], notify=False))

    response = client.patch(
        f"/api/v1/video-subscribes/{record['id']}",
        json={"reset_history": True},
        headers=auth_headers,
    )
    assert response.json()["data"]["handled_count"] == 0


def test_handled_ids_capped(monkeypatch, sub):
    """已处理 ID 只保留最近 500 个，不让 JSON 字段无限膨胀。"""
    record = sub(skip_existing=True, check_limit=50)

    async def _list(url, *, limit=10):
        return [entry(f"BV{i}") for i in range(600)], ""

    monkeypatch.setattr(service, "list_entries", _list)
    run(service.check_one(record["id"], notify=False))
    with_ids = service.list_all()
    row = next(item for item in with_ids if item["id"] == record["id"])
    assert row["handled_count"] <= 500


# ---------------- 预览端点 ----------------
def test_preview_reports_error(monkeypatch, client, auth_headers):
    """地址列不出来时 success=False 且带可读原因（最常见是贴了单个视频页）。"""

    async def _list(url, *, limit=10):
        return [], "\u8be5\u5730\u5740\u4e0d\u662f\u53ef\u5217\u4e3e\u7684\u9891\u9053"

    monkeypatch.setattr(service, "list_entries", _list)
    response = client.post(
        "/api/v1/video-subscribes/preview?url=https://www.bilibili.com/video/BV1",
        headers=auth_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["message"]
    assert body["site"] == "bilibili"


def test_preview_lists_entries(monkeypatch, client, auth_headers):
    async def _list(url, *, limit=10):
        return [entry("BV1", "\u6807\u9898"), entry("BV2", None)], ""

    monkeypatch.setattr(service, "list_entries", _list)
    response = client.post(
        "/api/v1/video-subscribes/preview?url=https://space.bilibili.com/1",
        headers=auth_headers,
    )
    body = response.json()
    assert body["success"] is True
    assert body["total"] == 2


# ---------------- 全量巡检 ----------------
def test_check_all_survives_single_failure(monkeypatch, client, auth_headers, sub):
    """单条订阅异常不能中断整轮巡检。"""
    good = sub(skip_existing=False, max_per_run=1)
    bad = sub(skip_existing=False, url="https://space.bilibili.com/2")

    async def _list(url, *, limit=10):
        if url.endswith("/2"):
            raise RuntimeError("boom")
        return [entry("BVX")], ""

    async def _add(payload, **kwargs):
        return {"id": 1}

    monkeypatch.setattr(service, "list_entries", _list)
    monkeypatch.setattr("app.services.download.add_download", _add)

    result = run(service.check_all())
    assert result["success"] is True
    ids = [item["id"] for item in result["items"]]
    assert good["id"] in ids and bad["id"] in ids
    failed = next(item for item in result["items"] if item["id"] == bad["id"])
    assert failed["success"] is False
