"""Bangumi 放送日历 Provider 测试（全程离线，用 monkeypatch 打掉网络）。

重点覆盖三个**实测踩到的真坑**（详见 ``app/providers/metadata/bangumi.py``）：

1. ``weekday.id`` 是 1~7（周一=1），与 Python ``weekday()`` 的 0~6 错开一位；
2. 接口返回的封面**全部是 http://**，HTTPS 部署会被混合内容拦成空白；
3. 约 10% 条目没有 ``name_cn``，必须回退日文原名；
4. ``images.large`` 是未压缩原图（实测 300 KB ~ 3 MB、单张最慢 21 秒），
   卡片必须取 ``common``（11 KB），否则图片代理会超时 502 导致随机裂图。
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.providers.metadata import bangumi


def run(coro):
    return asyncio.run(coro)


#: 一份按真实 ``https://api.bgm.tv/calendar`` 响应结构裁剪的样本。
#: 刻意保留了实测存在的几种"脏"情况：http 封面、无 name_cn、评分为 0。
SAMPLE = [
    {
        "weekday": {"id": 1, "cn": "星期一", "en": "Mon"},
        "items": [
            {
                "id": 1001,
                "name": "\u30c6\u30b9\u30c8\u6708\u66dc",
                "name_cn": "\u6d4b\u8bd5\u5468\u4e00",
                # 实测 113/113 条目五个尺寸全都有，样本要照着真实形状给，
                # 否则「优先取 common」这条根本测不出来
                "images": {
                    "large": "http://lain.bgm.tv/pic/cover/l/aa/mon.jpg",
                    "common": "http://lain.bgm.tv/pic/cover/c/aa/mon.jpg",
                    "medium": "http://lain.bgm.tv/pic/cover/m/aa/mon.jpg",
                    "small": "http://lain.bgm.tv/pic/cover/s/aa/mon.jpg",
                    "grid": "http://lain.bgm.tv/pic/cover/g/aa/mon.jpg",
                },
                "rating": {"score": 7.8},
                "air_date": "2026-01-05",
                "eps": 12,
                "url": "http://bgm.tv/subject/1001",
            },
            {
                "id": 1002,
                # 没有 name_cn：必须回退日文原名，否则卡片标题是空的
                "name": "\u30ce\u30fc\u30b8\u30e3\u30d1\u30cb\u30fc\u30ba",
                "images": {"large": "http://lain.bgm.tv/pic/cover/l/bb/nojp.jpg"},
                "rating": {"score": 0},
                "eps": 0,
            },
        ],
    },
    {
        "weekday": {"id": 7, "cn": "星期日", "en": "Sun"},
        "items": [
            {
                "id": 1007,
                "name": "\u30c6\u30b9\u30c8\u65e5\u66dc",
                "name_cn": "\u6d4b\u8bd5\u5468\u65e5",
                "images": {"common": "http://lain.bgm.tv/pic/cover/c/cc/sun.jpg"},
                "rating": {"score": 9.1},
                "eps": 24,
            }
        ],
    },
    {
        # 放送日未定：weekday.id 非法，条目应进「未定」桶而不是被错算成周日
        "weekday": {"id": 0, "cn": "", "en": ""},
        "items": [
            {
                "id": 1099,
                "name": "\u672a\u5b9a\u756a",
                "name_cn": "\u672a\u5b9a\u756a",
                "images": {"large": "http://lain.bgm.tv/pic/cover/l/dd/tbd.jpg"},
                "rating": {"score": 6.0},
            }
        ],
    },
]


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例前后都清缓存与退避，避免用例间互相污染。"""
    bangumi.reset_state()
    yield
    bangumi.reset_state()


@pytest.fixture
def fake_calendar(monkeypatch):
    """把 fetch_json 换成返回样本，全程不联网。"""

    async def _fetch(url, **kwargs):
        return SAMPLE

    monkeypatch.setattr(bangumi, "fetch_json", _fetch)


# ---------------- weekday 映射（最容易错的地方） ----------------
def test_weekday_maps_one_based_to_python():
    """1~7（周一=1）要映射成 0~6（周一=0）。

    这是本模块的头号坑：直接拿 ``weekday.id`` 当下标会整体偏一天，
    "今天更新什么"会显示成昨天或明天的番。
    """
    assert [bangumi.bgm_weekday_to_python(i) for i in range(1, 8)] == [0, 1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("bad", [0, 8, -1, None, "", "x", 3.5j])
def test_weekday_rejects_dirty_values(bad):
    """非法值必须返回 None（归入「未定」），不能静默算成某一天。"""
    assert bangumi.bgm_weekday_to_python(bad) is None


def test_weekday_accepts_numeric_string():
    """接口偶尔给字符串数字，也应正常解析。"""
    assert bangumi.bgm_weekday_to_python("3") == 2


# ---------------- 封面必须升级成 https ----------------
def test_cover_upgraded_to_https():
    """http:// 要升级成 https://，否则 HTTPS 部署会被混合内容拦掉。"""
    assert (
        bangumi.normalize_cover("http://lain.bgm.tv/pic/cover/l/aa/x.jpg")
        == "https://lain.bgm.tv/pic/cover/l/aa/x.jpg"
    )


def test_cover_https_and_empty_passthrough():
    """已经是 https 的不动；空值返回 None 而不是空串。"""
    assert bangumi.normalize_cover("https://lain.bgm.tv/a.jpg") == "https://lain.bgm.tv/a.jpg"
    assert bangumi.normalize_cover("") is None
    assert bangumi.normalize_cover(None) is None
    assert bangumi.normalize_cover("   ") is None


def test_all_sample_posters_are_https(fake_calendar):
    """样本里全是 http 封面，走完流程后不该再有一个 http。"""
    days = run(bangumi.calendar())
    posters = [row["poster"] for day in days for row in day["items"] if row.get("poster")]
    assert posters, "样本应当产出封面"
    assert all(url.startswith("https://") for url in posters)


# ---------------- 标题回退 ----------------
def test_title_falls_back_to_japanese_name(fake_calendar):
    """没有 name_cn 的条目必须回退日文 name，不能出现空标题。"""
    days = run(bangumi.calendar())
    titles = [row["title"] for day in days for row in day["items"]]
    assert all(titles), "标题不允许为空"
    assert "\u30ce\u30fc\u30b8\u30e3\u30d1\u30cb\u30fc\u30ba" in titles


# ---------------- 评分口径 ----------------
def test_zero_score_is_none_not_zero(fake_calendar):
    """未开分（score=0）要返回 None，渲染成 0.0 会让人以为这番很烂。"""
    days = run(bangumi.calendar())
    rows = {row["title"]: row for day in days for row in day["items"]}
    assert rows["\u30ce\u30fc\u30b8\u30e3\u30d1\u30cb\u30fc\u30ba"]["rating"] is None
    assert rows["\u6d4b\u8bd5\u5468\u4e00"]["rating"] == 7.8


# ---------------- 日历结构 ----------------
def test_calendar_has_seven_buckets_plus_undated(fake_calendar):
    """7 天桶必须齐全（哪天没番也要有空桶），「未定」额外挂在最后。"""
    days = run(bangumi.calendar())
    dated = [day for day in days if day["weekday"] is not None]
    undated = [day for day in days if day["weekday"] is None]
    assert [day["weekday"] for day in dated] == [0, 1, 2, 3, 4, 5, 6]
    assert dated[0]["label"] == "\u5468\u4e00"
    assert len(undated) == 1
    assert undated[0]["label"] == "\u672a\u5b9a"
    assert undated[0]["count"] == 1


def test_undated_item_not_placed_on_sunday(fake_calendar):
    """weekday.id=0 的条目不能被错塞进周日。"""
    days = run(bangumi.calendar())
    sunday = next(day for day in days if day["weekday"] == 6)
    titles = [row["title"] for row in sunday["items"]]
    assert "\u672a\u5b9a\u756a" not in titles


def test_calendar_sorted_by_rating_within_day(fake_calendar):
    """同一天内评分高的在前，未开分的垫底。"""
    days = run(bangumi.calendar())
    monday = next(day for day in days if day["weekday"] == 0)
    assert [row["title"] for row in monday["items"]] == [
        "\u6d4b\u8bd5\u5468\u4e00",
        "\u30ce\u30fc\u30b8\u30e3\u30d1\u30cb\u30fc\u30ba",
    ]


# ---------------- 摊平榜单从今天开始 ----------------
def test_chart_starts_from_today(fake_calendar, monkeypatch):
    """榜单要从**今天**排起，而不是从周一念一遍。

    用户关心的是"今天和接下来几天更新什么"。这里把今天固定成周日，
    则周日的番必须排在周一的番之前。
    """
    monkeypatch.setattr(bangumi, "today_index", lambda today=None: 6)
    rows = run(bangumi.chart(limit=10))
    assert rows[0]["title"] == "\u6d4b\u8bd5\u5468\u65e5"
    assert rows[0]["is_today"] is True
    assert rows[0]["days_ahead"] == 0
    # 周一的番在周日之后一天
    monday_rows = [row for row in rows if row["weekday"] == 0]
    assert monday_rows and monday_rows[0]["days_ahead"] == 1
    assert monday_rows[0]["is_today"] is False


def test_chart_undated_items_go_last(fake_calendar, monkeypatch):
    """「未定」条目永远垫最后，且 days_ahead 为 None。"""
    monkeypatch.setattr(bangumi, "today_index", lambda today=None: 0)
    rows = run(bangumi.chart(limit=20))
    assert rows[-1]["title"] == "\u672a\u5b9a\u756a"
    assert rows[-1]["days_ahead"] is None
    assert rows[-1]["is_today"] is False


def test_chart_rank_continues_across_pages(fake_calendar):
    """分页时名次要接着上一页，不能每页都从 1 开始。"""
    page2 = run(bangumi.chart(limit=2, offset=2))
    assert [row["rank"] for row in page2] == [3, 4]


def test_today_index_matches_python_weekday():
    """today_index 就是 Python 的 weekday()（0=周一）。"""
    assert bangumi.today_index(date(2026, 1, 5)) == 0  # 2026-01-05 是周一
    assert bangumi.today_index(date(2026, 1, 11)) == 6  # 周日


# ---------------- 失败与退避 ----------------
def test_empty_payload_triggers_backoff(monkeypatch):
    """上游返回空/结构不对时进入退避，并且**返回空列表而不是抛异常**。"""

    async def _fetch(url, **kwargs):
        return None

    monkeypatch.setattr(bangumi, "fetch_json", _fetch)
    assert run(bangumi.calendar()) == []
    assert bangumi.is_rate_limited() is True
    # 退避期内不再打上游
    calls = []

    async def _fetch2(url, **kwargs):
        calls.append(url)
        return SAMPLE

    monkeypatch.setattr(bangumi, "fetch_json", _fetch2)
    assert run(bangumi.calendar()) == []
    assert calls == []


def test_calendar_uses_cache(fake_calendar, monkeypatch):
    """一小时内重复调用只打一次上游（挡住用户反复切页签）。"""
    calls = []

    async def _counting(url, **kwargs):
        calls.append(url)
        return SAMPLE

    monkeypatch.setattr(bangumi, "fetch_json", _counting)
    run(bangumi.calendar())
    run(bangumi.calendar())
    run(bangumi.chart())
    assert len(calls) == 1


def test_health_check_reports_total(fake_calendar):
    """探活成功时要把在播数量说出来，便于站点健康页展示。"""
    ok, message = run(bangumi.health_check())
    assert ok is True
    assert "4" in message


# ---------------- 封面必须取「卡片尺寸」而不是原图 ----------------
def test_cover_prefers_common_over_large():
    """有 common 时必须选它，绝不能选 large。

    实测同一张封面：large 937 KB / 13216 ms，common 11 KB / 600 ms。
    卡片显示宽度只有 ~120px，用原图不但慢，还会把图片代理的 15s 上游超时打爆
    （返回 502 → 前端退占位 → 新番日历随机裂图）。
    """
    images = {
        "large": "http://lain.bgm.tv/pic/cover/l/aa/x.jpg",
        "common": "http://lain.bgm.tv/pic/cover/c/aa/x.jpg",
        "medium": "http://lain.bgm.tv/pic/cover/m/aa/x.jpg",
    }
    assert bangumi.pick_cover(images) == "https://lain.bgm.tv/pic/cover/c/aa/x.jpg"


def test_cover_priority_never_starts_with_large():
    """钉住优先级表本身：谁把 large 挪到前面，这条就红。"""
    assert bangumi.COVER_SIZE_PRIORITY[0] == "common"
    assert bangumi.COVER_SIZE_PRIORITY.index("large") > bangumi.COVER_SIZE_PRIORITY.index(
        "medium"
    )


def test_cover_falls_back_when_preferred_size_missing():
    """接口没承诺字段必然存在：缺 common 就往下降级，而不是变成没封面。"""
    assert bangumi.pick_cover(
        {"large": "http://lain.bgm.tv/pic/cover/l/bb/y.jpg"}
    ) == "https://lain.bgm.tv/pic/cover/l/bb/y.jpg"
    assert bangumi.pick_cover(
        {"grid": "http://lain.bgm.tv/pic/cover/g/bb/y.jpg"}
    ) == "https://lain.bgm.tv/pic/cover/g/bb/y.jpg"


def test_cover_bad_input_returns_none():
    """脏数据不能抛异常——日历拉取失败不该演变成整页 500。"""
    assert bangumi.pick_cover(None) is None
    assert bangumi.pick_cover("not-a-dict") is None
    assert bangumi.pick_cover({}) is None
    assert bangumi.pick_cover({"large": "", "common": "   "}) is None


def test_calendar_uses_card_sized_cover(fake_calendar):
    """走完整条流程后，样本里那条「五个尺寸都有」的必须落在 /cover/c/ 上。"""
    days = run(bangumi.calendar())
    rows = [row for day in days for row in day["items"] if row["title"] == "\u6d4b\u8bd5\u5468\u4e00"]
    assert rows, "样本里应当有这条"
    assert rows[0]["poster"] == "https://lain.bgm.tv/pic/cover/c/aa/mon.jpg"
