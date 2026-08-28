"""榜单自动订阅的单元测试（不联网）。"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import settings
from app.db.models import RankingRule, Subscribe
from app.db.session import session_scope
from app.services import ranking


@pytest.fixture(autouse=True)
def _clean(client):
    yield
    with session_scope() as session:
        session.query(RankingRule).delete()
        session.query(Subscribe).filter(Subscribe.title.like("榜单测试%")).delete(
            synchronize_session=False
        )


def make_rule(**kwargs):
    payload = {"name": "测试榜单规则", "source": "tmdb_trending", "media_type": "tv"}
    payload.update(kwargs)
    return ranking.create(payload)


def run(coro):
    return asyncio.run(coro)


def fake_candidates(monkeypatch, items):
    async def _fetch(rule):
        return items

    monkeypatch.setattr(ranking, "fetch_candidates", _fetch)


# ---------------- CRUD ----------------
def test_create_and_list():
    rule = make_rule(limit=7, min_vote=7.5)
    assert rule["limit"] == 7 and rule["min_vote"] == 7.5
    assert rule["source_label"] == ranking.SOURCES["tmdb_trending"]
    assert [item["id"] for item in ranking.list_rules()] == [rule["id"]]


def test_create_rejects_bad_input():
    with pytest.raises(ValueError):
        ranking.create({"name": ""})
    with pytest.raises(ValueError):
        ranking.create({"name": "x", "source": "douban_top"})
    make_rule()
    with pytest.raises(ValueError):
        make_rule()  # 同名


def test_limit_is_clamped():
    assert make_rule(limit=9999)["limit"] == 100
    assert ranking.update(ranking.list_rules()[0]["id"], {"limit": 500})["limit"] == 100


def test_update_and_delete():
    rule = make_rule()
    updated = ranking.update(rule["id"], {"name": "改名了", "enabled": False, "min_year": 2024})
    assert updated["name"] == "改名了" and updated["enabled"] is False
    assert updated["min_year"] == 2024
    assert ranking.update(99999, {"name": "x"}) is None
    assert ranking.delete(rule["id"]) is True
    assert ranking.delete(rule["id"]) is False


def test_update_rejects_bad_source():
    rule = make_rule()
    with pytest.raises(ValueError):
        ranking.update(rule["id"], {"source": "nope"})


# ---------------- 过滤（纯函数） ----------------
def test_filter_candidates_by_vote_year_keywords():
    rule = {"min_vote": 7.0, "min_year": 2020, "include": "", "exclude": "综艺"}
    candidates = [
        {"title": "好剧", "vote_average": 8.2, "year": 2024},
        {"title": "低分剧", "vote_average": 5.0, "year": 2024},
        {"title": "老剧", "vote_average": 9.0, "year": 2010},
        {"title": "某综艺", "vote_average": 8.0, "year": 2024},
        {"title": "", "vote_average": 9.9, "year": 2024},   # 无标题直接丢
    ]
    passed, rejected = ranking.filter_candidates(rule, candidates)
    assert [item["title"] for item in passed] == ["好剧"]
    reasons = {item["title"]: item["reason"] for item in rejected}
    assert "评分" in reasons["低分剧"]
    assert "年份" in reasons["老剧"]
    assert "排除词" in reasons["某综艺"]


def test_filter_candidates_include_keyword():
    rule = {"min_vote": 0, "min_year": None, "include": "剧|片", "exclude": ""}
    passed, rejected = ranking.filter_candidates(
        rule, [{"title": "好剧"}, {"title": "纪录"}]
    )
    assert [item["title"] for item in passed] == ["好剧"]
    assert "未包含" in rejected[0]["reason"]


def test_filter_candidates_no_conditions_passes_all():
    rule = {"min_vote": 0, "min_year": None, "include": "", "exclude": ""}
    passed, rejected = ranking.filter_candidates(rule, [{"title": "A"}, {"title": "B"}])
    assert len(passed) == 2 and rejected == []


# ---------------- run_rule ----------------
def test_run_rule_creates_subscribes_and_respects_cap(monkeypatch):
    monkeypatch.setattr(settings, "RANKING_MAX_PER_RUN", 2)
    rule = make_rule()
    fake_candidates(monkeypatch, [
        {"title": f"榜单测试剧{i}", "year": 2024, "media_type": "tv", "tmdb_id": 9000 + i,
         "vote_average": 8.0}
        for i in range(5)
    ])
    result = run(ranking.run_rule(rule["id"]))
    assert result["created"] == 2, "单次上限必须生效，否则会一次刷进上百个订阅"
    after = ranking.get_rule(rule["id"])
    assert after["created_count"] == 2
    assert after["handled_count"] == 2
    assert "新增 2 个订阅" in after["last_result"]


def test_run_rule_does_not_resubscribe_handled_items(monkeypatch):
    """用户删掉的订阅不该被自动加回来——这是最让人恼火的自动化。"""
    monkeypatch.setattr(settings, "RANKING_MAX_PER_RUN", 5)
    rule = make_rule()
    fake_candidates(monkeypatch, [
        {"title": "榜单测试独苗", "year": 2024, "media_type": "tv", "tmdb_id": 9100, "vote_average": 8}
    ])
    assert run(ranking.run_rule(rule["id"]))["created"] == 1

    # 用户手动删掉订阅
    with session_scope() as session:
        session.query(Subscribe).filter(Subscribe.title == "榜单测试独苗").delete()

    second = run(ranking.run_rule(rule["id"]))
    assert second["created"] == 0
    assert any("此前已处理" in item["reason"] for item in second["skipped"])


def test_run_rule_skips_existing_subscribes(monkeypatch):
    monkeypatch.setattr(settings, "RANKING_MAX_PER_RUN", 5)
    rule = make_rule()
    with session_scope() as session:
        session.add(Subscribe(title="榜单测试已订阅", media_type="tv", season=1, tmdb_id=9200))
    fake_candidates(monkeypatch, [
        {"title": "榜单测试已订阅", "year": 2024, "media_type": "tv", "tmdb_id": 9200, "vote_average": 8}
    ])
    result = run(ranking.run_rule(rule["id"]))
    assert result["created"] == 0
    assert any("已有订阅" in item["reason"] for item in result["skipped"])


def test_reset_handled_allows_rescan(monkeypatch):
    monkeypatch.setattr(settings, "RANKING_MAX_PER_RUN", 5)
    rule = make_rule()
    fake_candidates(monkeypatch, [
        {"title": "榜单测试重扫", "year": 2024, "media_type": "tv", "tmdb_id": 9300, "vote_average": 8}
    ])
    run(ranking.run_rule(rule["id"]))
    assert ranking.get_rule(rule["id"])["handled_count"] == 1
    assert ranking.update(rule["id"], {"reset_handled": True})["handled_count"] == 0


def test_dry_run_creates_nothing(monkeypatch):
    monkeypatch.setattr(settings, "RANKING_MAX_PER_RUN", 3)
    rule = make_rule(min_vote=7)
    fake_candidates(monkeypatch, [
        {"title": "榜单测试试算高分", "year": 2024, "media_type": "tv", "tmdb_id": 9400, "vote_average": 9},
        {"title": "榜单测试试算低分", "year": 2024, "media_type": "tv", "tmdb_id": 9401, "vote_average": 4},
    ])
    result = run(ranking.run_rule(rule["id"], dry_run=True))
    assert result["dry_run"] is True and result["created"] == 0
    assert [item["title"] for item in result["items"]] == ["榜单测试试算高分"]
    assert result["rejected"][0]["title"] == "榜单测试试算低分"
    with session_scope() as session:
        assert session.query(Subscribe).filter(Subscribe.tmdb_id == 9400).count() == 0


def test_run_rule_without_tmdb_key_returns_clear_message():
    """没配 TMDB Key 时必须给一句人能看懂的话，而不是静默空转。"""
    rule = make_rule(source="tmdb_popular")
    result = run(ranking.run_rule(rule["id"]))
    assert result["created"] == 0
    assert "TMDB_API_KEY" in result["message"]
    assert "本地资源热度榜" in result["message"]


def test_local_trending_source_needs_no_network(monkeypatch):
    rule = make_rule(source="local_trending")
    candidates = run(ranking.fetch_candidates(ranking.get_rule(rule["id"])))
    assert isinstance(candidates, list)   # 空库时是空列表，但不能抛错


def test_disabled_rule_is_skipped():
    rule = make_rule(enabled=False)
    result = run(ranking.run_rule(rule["id"]))
    assert result["created"] == 0 and "禁用" in result["message"]


def test_run_rule_missing():
    result = run(ranking.run_rule(99999))
    assert result["success"] is False


def test_run_all_only_enabled(monkeypatch):
    monkeypatch.setattr(settings, "RANKING_MAX_PER_RUN", 1)
    on = make_rule(name="启用的")
    make_rule(name="停用的", enabled=False)
    fake_candidates(monkeypatch, [
        {"title": "榜单测试全跑", "year": 2024, "media_type": "tv", "tmdb_id": 9500, "vote_average": 8}
    ])
    result = run(ranking.run())
    assert result["rules"] == 1
    assert result["results"][0]["rule_id"] == on["id"]


def test_run_all_without_rules():
    result = run(ranking.run())
    assert result["rules"] == 0 and "没有启用" in result["message"]


# ---------------- API ----------------
def test_ranking_api_crud(client, auth_headers):
    listing = client.get("/api/v1/ranking-rules", headers=auth_headers)
    assert listing.status_code == 200
    assert {item["value"] for item in listing.json()["sources"]} == set(ranking.SOURCES)

    created = client.post(
        "/api/v1/ranking-rules",
        json={"name": "API 榜单规则", "source": "local_trending", "media_type": "tv", "limit": 5},
        headers=auth_headers,
    )
    assert created.status_code == 200, created.text
    rule_id = created.json()["data"]["id"]

    dup = client.post(
        "/api/v1/ranking-rules",
        json={"name": "API 榜单规则", "source": "local_trending"},
        headers=auth_headers,
    )
    assert dup.status_code == 400

    patched = client.patch(
        f"/api/v1/ranking-rules/{rule_id}", json={"min_vote": 6.5}, headers=auth_headers
    )
    assert patched.json()["data"]["min_vote"] == 6.5

    preview = client.post(f"/api/v1/ranking-rules/{rule_id}/preview", headers=auth_headers)
    assert preview.status_code == 200

    assert client.patch("/api/v1/ranking-rules/99999", json={}, headers=auth_headers).status_code == 404
    assert client.delete(f"/api/v1/ranking-rules/{rule_id}", headers=auth_headers).status_code == 200
    assert client.delete(f"/api/v1/ranking-rules/{rule_id}", headers=auth_headers).status_code == 404


def test_ranking_api_rejects_bad_source(client, auth_headers):
    response = client.post(
        "/api/v1/ranking-rules", json={"name": "x", "source": "imdb"}, headers=auth_headers
    )
    assert response.status_code == 400


def test_ranking_requires_auth(client):
    assert client.get("/api/v1/ranking-rules").status_code == 401
