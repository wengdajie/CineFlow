"""豆瓣封面 Provider 测试（全程离线，用 monkeypatch 打掉网络）。"""

from __future__ import annotations

import asyncio

import pytest

from app.providers.metadata import douban


def run(coro):
    return asyncio.run(coro)


#: 一份真实的豆瓣 subject_suggest 响应片段（实测抓取后裁剪）
SAMPLE = [
    {
        "episode": "46",
        "img": "https://img3.doubanio.com/view/photo/s_ratio_poster/public/p2575362797.jpg",
        "title": "庆余年 第一季",
        "url": "https://movie.douban.com/subject/25853071/",
        "type": "movie",
        "year": "2019",
        "sub_title": "庆余年 第一季",
        "id": "25853071",
    },
    {
        "episode": "36",
        "img": "https://img1.doubanio.com/view/photo/s_ratio_poster/public/p2895916343.jpg",
        "title": "庆余年 第二季",
        "url": "https://movie.douban.com/subject/35143550/",
        "type": "movie",
        "year": "2024",
        "sub_title": "庆余年 第二季",
        "id": "35143550",
    },
    {
        "episode": "",
        "img": "https://img9.doubanio.com/view/photo/s_ratio_poster/public/p2564253726.jpg",
        "title": "流浪地球",
        "url": "https://movie.douban.com/subject/26266893/",
        "type": "movie",
        "year": "2019",
        "sub_title": "流浪地球",
        "id": "26266893",
    },
]


@pytest.fixture(autouse=True)
def clean_state():
    """每个用例前后都清掉缓存与退避，避免用例互相污染。"""
    douban.reset_state()
    yield
    douban.reset_state()


def fake_fetch(payload):
    """构造一个假的 fetch_json，并记录调用次数（用于验证缓存生效）。"""
    calls = {"n": 0}

    async def _fetch(url, **kwargs):
        calls["n"] += 1
        return payload

    return _fetch, calls


# ---------------------------------------------------------------- 归一化
def test_normalize_tv_and_movie():
    """有 episode 的判为剧集，没有的判为电影。"""
    tv = douban._normalize(SAMPLE[0])
    assert tv["media_type"] == "tv"
    assert tv["episodes"] == 46
    assert tv["year"] == 2019
    assert tv["poster"].endswith(".jpg")
    assert tv["douban_id"] == "25853071"
    assert tv["source"] == "douban"

    movie = douban._normalize(SAMPLE[2])
    assert movie["media_type"] == "movie"
    assert movie["episodes"] is None


def test_normalize_rejects_untitled():
    """没有标题的条目直接丢弃，不能污染结果。"""
    assert douban._normalize({"img": "x.jpg"}) is None
    assert douban._normalize("not a dict") is None


# ---------------------------------------------------------------- suggest
def test_suggest_parses_and_caches(monkeypatch):
    """suggest 能解析结果，且第二次调用命中缓存不再发请求。"""
    fetch, calls = fake_fetch(SAMPLE)
    monkeypatch.setattr(douban, "fetch_json", fetch)

    first = run(douban.suggest("庆余年"))
    assert len(first) == 3
    assert first[0]["title"] == "庆余年 第一季"
    assert calls["n"] == 1

    second = run(douban.suggest("庆余年"))
    assert second == first
    assert calls["n"] == 1, "第二次应命中缓存，不该再请求豆瓣"


def test_suggest_empty_keyword_makes_no_request(monkeypatch):
    """空关键词不发请求。"""
    fetch, calls = fake_fetch(SAMPLE)
    monkeypatch.setattr(douban, "fetch_json", fetch)
    assert run(douban.suggest("   ")) == []
    assert calls["n"] == 0


def test_suggest_failure_triggers_backoff(monkeypatch):
    """请求失败要进入退避期，期间不再打豆瓣（保护对方也保护自己）。"""
    fetch, calls = fake_fetch(None)
    monkeypatch.setattr(douban, "fetch_json", fetch)

    assert run(douban.suggest("庆余年")) == []
    assert douban.is_rate_limited() is True

    assert run(douban.suggest("别的片名")) == []
    assert calls["n"] == 1, "退避期内不应再发请求"


def test_suggest_handles_non_list_payload(monkeypatch):
    """豆瓣偶尔返回非列表（如错误页），必须安全降级为空。"""
    fetch, _ = fake_fetch({"error": "blocked"})
    monkeypatch.setattr(douban, "fetch_json", fetch)
    assert run(douban.suggest("庆余年")) == []


def test_suggest_respects_limit(monkeypatch):
    fetch, _ = fake_fetch(SAMPLE)
    monkeypatch.setattr(douban, "fetch_json", fetch)
    assert len(run(douban.suggest("庆余年", limit=2))) == 2


# ---------------------------------------------------------------- match
def test_match_prefers_exact_title_and_year(monkeypatch):
    """同名 + 同年份应排在最前。"""
    fetch, _ = fake_fetch(SAMPLE)
    monkeypatch.setattr(douban, "fetch_json", fetch)
    found = run(douban.match("庆余年 第二季", year=2024))
    assert found["douban_id"] == "35143550"


def test_match_filters_by_media_type(monkeypatch):
    """指定 media_type=tv 时优先返回剧集。"""
    fetch, _ = fake_fetch(SAMPLE)
    monkeypatch.setattr(douban, "fetch_json", fetch)
    found = run(douban.match("庆余年", media_type="tv"))
    assert found["media_type"] == "tv"


def test_match_falls_back_when_no_same_type(monkeypatch):
    """没有同类型候选时不能返回 None——有封面总比没封面好。"""
    fetch, _ = fake_fetch([SAMPLE[2]])  # 只有电影
    monkeypatch.setattr(douban, "fetch_json", fetch)
    found = run(douban.match("流浪地球", media_type="tv"))
    assert found is not None
    assert found["media_type"] == "movie"


def test_match_empty_title():
    assert run(douban.match("")) is None


def test_poster_shortcut(monkeypatch):
    fetch, _ = fake_fetch(SAMPLE)
    monkeypatch.setattr(douban, "fetch_json", fetch)
    url = run(douban.poster("流浪地球", year=2019))
    assert url.startswith("https://img")


def test_poster_none_when_no_result(monkeypatch):
    fetch, _ = fake_fetch([])
    monkeypatch.setattr(douban, "fetch_json", fetch)
    assert run(douban.poster("不存在的片子")) is None


# ------------------------------------------------- 年份罚分与匹配门槛（M41）
#: 同名不同年的两条：2002 电影版《无间道》与 2019 剧版（虚构 ID，结构照真实响应）
REMAKE_SAMPLE = [
    {
        "episode": "",
        "img": "https://img1.doubanio.com/view/photo/s_ratio_poster/public/p_movie.jpg",
        "title": "无间道",
        "url": "https://movie.douban.com/subject/1307914/",
        "type": "movie",
        "year": "2002",
        "sub_title": "Infernal Affairs",
        "id": "1307914",
    },
    {
        "episode": "30",
        "img": "https://img2.doubanio.com/view/photo/s_ratio_poster/public/p_tv.jpg",
        "title": "无间道",
        "url": "https://movie.douban.com/subject/30330823/",
        "type": "movie",
        "year": "2019",
        "sub_title": "无间道",
        "id": "30330823",
    },
]


def test_year_mismatch_is_penalized_not_ignored():
    """年份差得多要**扣分**，不是「不加分」就算了。

    v1.11.0 的逻辑下，同名条目无论年份都能拿满 100 分，于是 2002 年的
    电影《无间道》会被配上 2019 剧版的封面。
    """
    movie = douban._normalize(REMAKE_SAMPLE[0])
    remake = douban._normalize(REMAKE_SAMPLE[1])
    assert douban._score(movie, "无间道", 2002) > douban._score(remake, "无间道", 2002)


def test_same_name_wrong_year_falls_below_threshold():
    """「精确同名 + 有图 - 年份罚分」必须**低于**门槛。

    这条锁住的是罚分的**具体数值**：精确同名(100) + 有图(5) - 60 = 45 < 60，
    所以过不了关。若罚分只有 40，该组合得 65 分仍能过门槛，等于没拦住——
    这正是调这个值时踩过的点，所以用一条测试把它钉死。
    """
    remake = douban._normalize(REMAKE_SAMPLE[1])
    score = douban._score(remake, "无间道", 2002)
    assert score < douban.MIN_MATCH_SCORE, (
        f"同名不同年得了 {score} 分，仍能过 {douban.MIN_MATCH_SCORE} 分门槛"
    )


def test_near_miss_year_only_lightly_penalized():
    """差 2~3 年只轻扣：可能只是「上映年 vs 引进年」的录入口径差异。"""
    movie = douban._normalize(REMAKE_SAMPLE[0])
    near = douban._score(movie, "无间道", 2004)   # delta 2 -> 轻扣
    far = douban._score(movie, "无间道", 2019)    # delta 17 -> 重扣
    assert near > far
    assert near >= douban.MIN_MATCH_SCORE, "差两年不该被拦掉，否则大量正常片没封面"


def test_match_returns_none_when_score_too_low(monkeypatch):
    """分不够就返回 None——**宁可没封面，也不要错封面**。

    错图会让用户以为订阅错了片，比留个占位色块糟糕得多。
    """
    fetch, _ = fake_fetch([REMAKE_SAMPLE[1]])  # 只有 2019 剧版
    monkeypatch.setattr(douban, "fetch_json", fetch)
    assert run(douban.match("无间道", year=2002)) is None


def test_match_picks_right_year_among_remakes(monkeypatch):
    """两个同名候选都在时，要挑年份对得上的那个。"""
    fetch, _ = fake_fetch(REMAKE_SAMPLE)
    monkeypatch.setattr(douban, "fetch_json", fetch)
    assert run(douban.match("无间道", year=2002))["douban_id"] == "1307914"
    assert run(douban.match("无间道", year=2019))["douban_id"] == "30330823"


def test_match_threshold_can_be_disabled(monkeypatch):
    """min_score=0 显式关掉门槛，保留「只求有图」的旧行为。"""
    fetch, _ = fake_fetch([REMAKE_SAMPLE[1]])
    monkeypatch.setattr(douban, "fetch_json", fetch)
    assert run(douban.match("无间道", year=2002)) is None
    assert run(douban.match("无间道", year=2002, min_score=0)) is not None


def test_match_exposes_score_and_confidence(monkeypatch):
    """返回体要带匹配分与置信度，前端才能提示「这个封面可能不准」。"""
    fetch, _ = fake_fetch(SAMPLE)
    monkeypatch.setattr(douban, "fetch_json", fetch)
    found = run(douban.match("庆余年 第二季", year=2024))
    assert found["match_score"] >= douban.MIN_MATCH_SCORE
    assert found["match_confidence"] == "high"


def test_fuzzy_match_alone_is_not_enough(monkeypatch):
    """只是「名字里有几个字一样」（模糊包含 50 分）不该过关。

    没有年份/类型佐证时，「三国」不能随便配上《三国演义》的封面。
    """
    partial = {
        "episode": "84",
        "img": "https://img3.doubanio.com/view/photo/s_ratio_poster/public/p_sg.jpg",
        "title": "三国演义",
        "url": "https://movie.douban.com/subject/1000000/",
        "type": "movie",
        "year": "1994",
        "sub_title": "",
        "id": "1000000",
    }
    fetch, _ = fake_fetch([partial])
    monkeypatch.setattr(douban, "fetch_json", fetch)
    assert run(douban.match("三国")) is None


def test_no_year_given_still_matches_exact_title(monkeypatch):
    """调用方没给年份时不该因此扣分——精确同名照样过关。"""
    fetch, _ = fake_fetch(REMAKE_SAMPLE)
    monkeypatch.setattr(douban, "fetch_json", fetch)
    assert run(douban.match("无间道")) is not None


# ---------------------------------------------------------------- health
def test_health_check_ok(monkeypatch):
    fetch, _ = fake_fetch(SAMPLE)
    monkeypatch.setattr(douban, "fetch_json", fetch)
    ok, message = run(douban.health_check())
    assert ok is True
    assert "正常" in message


def test_health_check_reports_rate_limit(monkeypatch):
    fetch, _ = fake_fetch(None)
    monkeypatch.setattr(douban, "fetch_json", fetch)
    run(douban.suggest("触发退避"))
    ok, message = run(douban.health_check())
    assert ok is False
    assert "限流" in message
