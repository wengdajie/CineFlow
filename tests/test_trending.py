"""热度排行与定时任务设置的单元测试（不联网）。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.db.base import utcnow
from app.db.models import ResourceRecord, SearchHistory
from app.db.session import session_scope
from app.services import settings_store, trending
from app.services.scheduler import (
    JOB_RADAR,
    builtin_specs,
    effective_schedule,
    normalize_schedule,
    scheduler_service,
)


@pytest.fixture(autouse=True)
def _db(client):
    """复用会话级 TestClient，确保建表与默认数据已就绪。"""
    return client


def _seed_resources() -> None:
    """写入可预测的搜索缓存：热剧 3 站 6 条，冷片 1 站 1 条。"""
    with session_scope() as session:
        session.query(ResourceRecord).delete()
        session.query(SearchHistory).delete()
        now = utcnow()
        for index in range(6):
            session.add(
                ResourceRecord(
                    unique_key=f"hot-{index}",
                    title=f"热门剧集 S01E{index + 1:02d} 2160p WEB-DL H265 中字",
                    kind="magnet" if index % 2 else "pan",
                    site=f"站点{index % 3}",
                    link=f"magnet:?xt=urn:btih:hot{index}",
                    size=5 * 1024**3,
                    seeders=200 + index * 10,
                    media_type="tv",
                    season=1,
                    episodes=[index + 1],
                    resolution="2160p",
                    score=1200 + index,
                    publish_at=now - timedelta(hours=index),
                )
            )
        session.add(
            ResourceRecord(
                unique_key="cold-1",
                title="冷门电影 2019 1080p BluRay",
                kind="torrent",
                site="站点9",
                link="magnet:?xt=urn:btih:cold1",
                size=2 * 1024**3,
                seeders=1,
                media_type="movie",
                resolution="1080p",
                score=300,
                publish_at=now - timedelta(days=9),
            )
        )
        for _ in range(3):
            session.add(SearchHistory(keyword="热门剧集", media_type="tv", result_count=12))
        session.add(SearchHistory(keyword="冷门电影", media_type="movie", result_count=1))


# ---------------- 标题归并（榜单去碎片化） ----------------
def test_canonical_title_strips_variant_and_episode_marks():
    """版本标记 / 集号 / 季号 / 装饰符号都应被剥离（作用于已解析的片名）。"""
    cases = [
        ("师兄太稳健 高码版", "师兄太稳健"),
        ("师兄太稳健 60帧率版本 杜比视界版本 高码版", "师兄太稳健"),
        ("第18集 师兄太稳健", "师兄太稳健"),
        ("✅「师兄太稳健", "师兄太稳健"),
        ("庆余年 第二季", "庆余年"),
        ("庆余年 全36集", "庆余年"),
    ]
    for raw, expected in cases:
        assert trending._canonical_title(raw) == expected, raw


def test_group_key_merges_full_release_names():
    """整条发布名经 parse + 归并后，同剧不同封装应落到同一个键。"""
    variants = [
        "师兄太稳健[第16-17集][国语配音+中文字幕].Pull.Strings.S01.2026.2160p.WEB-DL",
        "师兄太稳健[高码版][第10-11集][国语配音+中文字幕].Pull.Strings.S01.2026.2160p",
        "师兄太稳健[60帧率版本][杜比视界版本][高码版][第08-09集].2026.2160p.HQ.WEB-DL",
        "第18集 师兄太稳健[第18集][国语音轨].Pull.Strings.S01.2026.2160p",
    ]
    keys = {trending._group_key(title, "tv", 1) for title in variants}
    assert keys == {"tv:师兄太稳健:s1"}, keys


def test_group_key_merges_season_variants():
    """季号缺失与显式 S01 应归并；不同季必须分开。"""
    a = trending._group_key("师兄太稳健[高码版][第10-11集].2026.2160p", "tv", 1)
    b = trending._group_key("师兄太稳健[60帧率版本][第10-11集].2026.2160p", "tv", None)
    assert a == b == "tv:师兄太稳健:s1"

    s2 = trending._group_key("庆余年 第二季[全36集].2024.2160p", "tv", 2)
    s1 = trending._group_key("庆余年 第一季[全46集].2019.2160p", "tv", 1)
    assert s1 != s2
    assert s2 == "tv:庆余年:s2"


def test_display_title_is_readable():
    """展示用片名不应残留集号与版本标记。"""
    assert trending._display_title(
        "第09集 庆余年 第二季[杜比视界版本][第09集][国语配音+中文字幕].2024.2160p",
        "第09集 庆余年 第二季 杜比视界版本 第09集 国语配音 中文字幕",
    ) == "庆余年 第二季"
    assert trending._display_title("✅「师兄太稳健」全17集", "✅「师兄太稳健") == "师兄太稳健"


def test_ranking_merges_release_variants():
    """端到端：8 条不同封装的同一部剧只产生 1 条榜单项。"""
    with session_scope() as session:
        session.query(ResourceRecord).delete()
        now = utcnow()
        titles = [
            "师兄太稳健[第16-17集][国语配音+中文字幕].Pull.Strings.S01.2026.2160p.WEB-DL",
            "师兄太稳健[高码版][第10-11集][国语配音+中文字幕].Pull.Strings.S01.2026.2160p",
            "师兄太稳健[60帧率版本][第01-14集][国语配音+中文字幕].2026.2160p.WEB-DL",
            "师兄太稳健[杜比视界版本][第06-10集][国语音轨].Pull.Strings.S01.2026.2160p",
            "师兄太稳健[60帧率版本][杜比视界版本][高码版][第08-09集].2026.2160p.HQ.WEB-DL",
            "第18集 师兄太稳健[第18集][国语音轨].Pull.Strings.S01.2026.2160p",
            "师兄太稳健[杜比视界版本][高码版][第13-14集].2026.2160p.HQ.WEB-DL",
            "师兄太稳健[第15集][国语配音+中文字幕].Pull.Strings.S01.2026.1080p.WEB-DL",
        ]
        for index, title in enumerate(titles):
            session.add(
                ResourceRecord(
                    unique_key=f"variant-{index}",
                    title=title,
                    kind="magnet",
                    site=f"站点{index % 2}",
                    link=f"magnet:?xt=urn:btih:variant{index}",
                    size=4 * 1024**3,
                    seeders=50,
                    media_type="tv",
                    season=1 if index % 3 else None,
                    episodes=[index + 1],
                    resolution="2160p",
                    score=1000,
                    publish_at=now - timedelta(hours=index),
                )
            )

    data = trending.resource_ranking(limit=20, days=30)
    assert data["total"] == 1, [item["title"] for item in data["items"]]
    item = data["items"][0]
    assert item["title"] == "师兄太稳健"
    assert item["resource_count"] == 8
    assert item["site_count"] == 2


def test_unknown_media_type_collapses_into_known_group():
    """盘搜缺少类型信息时，同名 unknown 组应折叠进 tv 组而非并列成榜。"""
    with session_scope() as session:
        session.query(ResourceRecord).delete()
        now = utcnow()
        session.add(
            ResourceRecord(
                unique_key="known-tv",
                title="折叠测试剧 第一季[第01-05集].2026.2160p.WEB-DL",
                kind="magnet",
                site="BT站",
                link="magnet:?xt=urn:btih:knowntv",
                size=3 * 1024**3,
                seeders=80,
                media_type="tv",
                season=1,
                episodes=[1, 2, 3],
                resolution="2160p",
                score=900,
                publish_at=now,
            )
        )
        # 网盘结果：类型未识别、季号缺失
        session.add(
            ResourceRecord(
                unique_key="unknown-pan",
                title="折叠测试剧 全集 国语中字",
                kind="pan",
                site="盘搜",
                link="https://pan.quark.cn/s/abc123",
                size=9 * 1024**3,
                seeders=0,
                media_type=None,
                season=None,
                episodes=[4, 5],
                score=700,
                publish_at=now,
            )
        )

    data = trending.resource_ranking(limit=20, days=30)
    assert data["total"] == 1, [
        (item["title"], item["media_type"]) for item in data["items"]
    ]
    item = data["items"][0]
    assert item["media_type"] == "tv"
    assert item["resource_count"] == 2
    assert item["site_count"] == 2
    # 网盘资源被吸收进来 → 集数合并、kinds 含 pan
    assert set(item["episodes"]) == {1, 2, 3, 4, 5}
    assert "pan" in item["kinds"]


# ---------------- 热度排行 ----------------
def test_resource_ranking_orders_by_heat():
    _seed_resources()
    data = trending.resource_ranking(limit=10, days=30)
    assert data["total"] >= 2
    items = data["items"]
    assert items[0]["title"].startswith("热门剧集")
    assert items[0]["rank"] == 1
    assert items[0]["heat_percent"] == 100.0
    # 多站收录 + 高做种应显著领先
    assert items[0]["heat"] > items[-1]["heat"]
    assert items[0]["site_count"] == 3
    assert items[0]["resource_count"] == 6
    assert items[0]["latest_episode"] == 6
    assert "pan" in items[0]["kinds"]
    assert items[0]["samples"], "应带样例资源"


def test_resource_ranking_filters():
    _seed_resources()
    movies = trending.resource_ranking(limit=10, days=30, media_type="movie")
    assert movies["total"] == 1
    assert movies["items"][0]["media_type"] == "movie"

    pans = trending.resource_ranking(limit=10, days=30, kind="pan")
    assert pans["total"] == 1
    assert pans["items"][0]["kinds"] == ["pan"]


def test_resource_ranking_window_excludes_old():
    _seed_resources()
    recent = trending.resource_ranking(limit=10, days=1)
    titles = [item["title"] for item in recent["items"]]
    assert any(title.startswith("热门剧集") for title in titles)


def test_hot_keywords():
    _seed_resources()
    data = trending.hot_keywords(limit=5, days=30)
    assert data["items"][0]["keyword"] == "热门剧集"
    assert data["items"][0]["times"] == 3
    assert data["items"][0]["heat_percent"] == 100.0


def test_site_activity():
    _seed_resources()
    data = trending.site_activity(limit=10, days=30)
    assert data["total"] == 4
    assert data["items"][0]["resources"] == 2
    assert data["items"][0]["heat_percent"] == 100.0


def test_overview_contains_three_boards():
    _seed_resources()
    data = trending.overview(limit=3, days=30)
    assert set(data) == {"resources", "keywords", "sites"}
    assert len(data["resources"]["items"]) <= 3


def test_trending_api(client, auth_headers):
    _seed_resources()
    response = client.get("/api/v1/trending?limit=5&days=30", headers=auth_headers)
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["resources"]["items"][0]["rank"] == 1

    for path in ("/api/v1/trending/resources", "/api/v1/trending/keywords",
                 "/api/v1/trending/sites"):
        assert client.get(path, headers=auth_headers).status_code == 200

    # 未启用站点时实时榜应优雅返回空
    live = client.get("/api/v1/trending/live", headers=auth_headers)
    assert live.status_code == 200
    assert live.json()["data"]["items"] == []


def test_trending_requires_auth(client):
    assert client.get("/api/v1/trending").status_code == 401


# ---------------- 定时任务设置 ----------------
def test_builtin_specs_cover_all_jobs():
    keys = [spec.key for spec in builtin_specs()]
    # v1.5.0 扩到 11 个；v1.7.0 新增网盘凭据保活 12 个；v1.12.0 新增视频追更与限速时段 14 个；
    # v1.14.0 新增社区站点清单同步 15 个；v1.18.0 新增 RSS 追新 16 个
    assert keys == [
        "subscribe",
        "radar",
        "download",
        "pan_transfer",
        "pan_subscribe",
        "video_subscribe",
        "pan_keepalive",
        "strm_sync",
        "speed_limit",
        "site_health",
        "ranking",
        "zhuiju_sync",
        "rss",
        "scrape",
        "upgrade",
        "library",
    ]


def test_normalize_schedule_validates():
    ok = normalize_schedule("radar", {"trigger": "interval", "minutes": 20})
    assert ok["minutes"] == 20 and ok["trigger"] == "interval"

    cron = normalize_schedule("library", {"trigger": "cron", "cron": "30 3 * * *"})
    assert cron["cron"] == "30 3 * * *"

    for bad in (
        {"trigger": "hourly"},
        {"trigger": "interval", "minutes": 0},
        {"trigger": "interval", "minutes": 99999999},
        {"trigger": "cron", "cron": "bad expr"},
    ):
        try:
            normalize_schedule("radar", bad)
        except ValueError:
            continue
        raise AssertionError(f"非法规则未被拒绝: {bad}")


def test_normalize_schedule_validates_cron_even_for_interval():
    """interval 任务提交的非法 cron 也要当场拒绝。

    真实踩坑：以前只在 trigger==cron 时校验，非法表达式会被静默存下来，
    等用户哪天把 trigger 切成 cron 才起不来，那时早忘了自己填过什么。
    """
    try:
        normalize_schedule("radar", {"trigger": "interval", "minutes": 30, "cron": "这不是 cron"})
    except ValueError:
        pass
    else:
        raise AssertionError("interval 任务的非法 cron 未被拒绝")

    # 不提交 cron 字段时沿用旧值，不该因此报错
    kept = normalize_schedule("radar", {"trigger": "interval", "minutes": 25})
    assert kept["minutes"] == 25


def test_update_and_reset_schedule_persists():
    settings_store.delete_setting(settings_store.KEY_SCHEDULES)
    try:
        default = effective_schedule("radar")
        assert default["customized"] is False

        scheduler_service.update_schedule("radar", {"minutes": 45})
        stored = effective_schedule("radar")
        assert stored["minutes"] == 45
        assert stored["customized"] is True
        # 覆盖值确实落库
        raw = settings_store.get_setting(settings_store.KEY_SCHEDULES)
        assert raw["radar"]["minutes"] == 45

        described = scheduler_service.describe_schedule("radar")
        assert described["id"] == JOB_RADAR
        assert described["default"]["minutes"] == default["minutes"]

        scheduler_service.reset_schedule("radar")
        assert effective_schedule("radar")["customized"] is False
    finally:
        settings_store.delete_setting(settings_store.KEY_SCHEDULES)


def test_schedule_api(client, auth_headers):
    settings_store.delete_setting(settings_store.KEY_SCHEDULES)
    try:
        listing = client.get("/api/v1/schedules", headers=auth_headers)
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert len(items) == 16
        assert {item["key"] for item in items} == {
            "subscribe", "radar", "download", "pan_transfer", "pan_subscribe",
            "video_subscribe", "speed_limit",
            "pan_keepalive", "strm_sync", "site_health", "ranking", "scrape",
            "upgrade", "library", "zhuiju_sync", "rss",
        }

        detail = client.get("/api/v1/schedules/radar", headers=auth_headers)
        assert detail.status_code == 200
        assert detail.json()["data"]["key"] == "radar"
        assert client.get("/api/v1/schedules/nope", headers=auth_headers).status_code == 404

        updated = client.put(
            "/api/v1/schedules/radar",
            json={"trigger": "interval", "minutes": 25, "enabled": True},
            headers=auth_headers,
        )
        assert updated.status_code == 200
        assert updated.json()["data"]["minutes"] == 25
        assert updated.json()["data"]["customized"] is True

        cron = client.put(
            "/api/v1/schedules/library",
            json={"trigger": "cron", "cron": "15 5 * * *"},
            headers=auth_headers,
        )
        assert cron.status_code == 200
        assert cron.json()["data"]["cron"] == "15 5 * * *"

        bad = client.put(
            "/api/v1/schedules/radar",
            json={"trigger": "cron", "cron": "not a cron"},
            headers=auth_headers,
        )
        assert bad.status_code == 400

        reset = client.post("/api/v1/schedules/radar/reset", headers=auth_headers)
        assert reset.status_code == 200
        assert reset.json()["data"]["customized"] is False

        # 测试环境调度器未启动，立即执行应返回 400 而非 500
        run = client.post("/api/v1/schedules/radar/run", headers=auth_headers)
        assert run.status_code in (200, 400)
    finally:
        settings_store.delete_setting(settings_store.KEY_SCHEDULES)
        client.post("/api/v1/schedules/library/reset", headers=auth_headers)


def test_schedule_requires_auth(client):
    assert client.get("/api/v1/schedules").status_code == 401
    assert client.put("/api/v1/schedules/radar", json={"minutes": 30}).status_code == 401


def test_settings_store_roundtrip():
    settings_store.set_setting("unit-test-key", {"a": 1})
    assert settings_store.get_setting("unit-test-key") == {"a": 1}
    settings_store.set_setting("unit-test-key", {"a": 2})
    assert settings_store.get_setting("unit-test-key")["a"] == 2
    assert settings_store.delete_setting("unit-test-key") is True
    assert settings_store.delete_setting("unit-test-key") is False
    assert settings_store.get_setting("unit-test-key", "fallback") == "fallback"
