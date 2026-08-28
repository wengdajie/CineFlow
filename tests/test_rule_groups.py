"""过滤规则组的单元测试（纯本地，不联网）。"""

from __future__ import annotations

import pytest

from app.core import filters
from app.db.models import FilterRuleGroup, Subscribe
from app.db.session import session_scope
from app.services import rule_groups

PREFIX = "测试规则组"


@pytest.fixture(autouse=True)
def _clean(client):
    """只清理本文件造出来的数据。

    内置的 4 个模板组是 init_db 建的，别删——删了会影响其它用例与门禁计数。
    """
    yield
    with session_scope() as session:
        session.query(Subscribe).filter(Subscribe.title.like(f"{PREFIX}%")).delete(
            synchronize_session=False
        )
        session.query(FilterRuleGroup).filter(
            FilterRuleGroup.name.like(f"{PREFIX}%")
        ).delete(synchronize_session=False)
        # 试算/默认组用例可能把模板组设成默认，恢复成「无默认组」
        for row in session.query(FilterRuleGroup).filter(
            FilterRuleGroup.is_default.is_(True)
        ):
            row.is_default = False


def make_group(name_suffix: str = "A", **kwargs) -> dict:
    payload = {
        "name": f"{PREFIX}{name_suffix}",
        "description": "单测用",
        "levels": [
            {"name": "1080p 中字", "resolution": "1080p", "include": "中字"},
            {"name": "1080p", "resolution": "1080p"},
        ],
    }
    payload.update(kwargs)
    return rule_groups.create(payload)


# ---------------- CRUD ----------------
def test_create_and_get():
    group = make_group()
    assert group["level_count"] == 2
    assert group["enabled"] is True and group["is_default"] is False
    # summary 是给界面看的人类可读说明，最后一行必然是兜底策略
    assert group["summary"][-1].startswith("兜底：")

    fetched = rule_groups.get_group(group["id"])
    assert fetched is not None and fetched["name"] == group["name"]
    assert rule_groups.get_group(999999) is None


def test_create_requires_name_and_levels():
    with pytest.raises(ValueError):
        rule_groups.create({"name": "  ", "levels": [{"resolution": "1080p"}]})
    # 没有层级的规则组不起任何作用，必须拒绝而不是静默存下来
    with pytest.raises(ValueError):
        rule_groups.create({"name": f"{PREFIX}空", "levels": []})
    with pytest.raises(ValueError):
        rule_groups.create({"name": f"{PREFIX}脏", "levels": ["不是字典"]})


def test_create_rejects_duplicate_name():
    make_group("同名")
    with pytest.raises(ValueError):
        make_group("同名")


def test_update_fields():
    group = make_group()
    updated = rule_groups.update(
        group["id"],
        {"name": f"{PREFIX}改名", "description": "新说明", "enabled": False},
    )
    assert updated["name"] == f"{PREFIX}改名"
    assert updated["description"] == "新说明"
    assert updated["enabled"] is False

    # levels 可以整体替换，层数随之变化
    replaced = rule_groups.update(group["id"], {"levels": [{"resolution": "2160p"}]})
    assert replaced["level_count"] == 1
    assert rule_groups.update(999999, {"description": "x"}) is None


def test_update_rejects_bad_values():
    group = make_group()
    other = make_group("B")
    with pytest.raises(ValueError):
        rule_groups.update(group["id"], {"name": ""})
    with pytest.raises(ValueError):
        rule_groups.update(group["id"], {"levels": []})
    with pytest.raises(ValueError):
        rule_groups.update(group["id"], {"name": other["name"]})


def test_list_puts_default_first():
    make_group("普通")
    starred = make_group("默认", is_default=True)
    names = [item["name"] for item in rule_groups.list_groups()]
    assert names[0] == starred["name"]
    # enabled_only 会过滤掉停用组
    rule_groups.update(starred["id"], {"enabled": False})
    assert starred["name"] not in [
        item["name"] for item in rule_groups.list_groups(enabled_only=True)
    ]


# ---------------- 默认组唯一性 ----------------
def test_only_one_default_group():
    first = make_group("旧默认", is_default=True)
    second = make_group("新默认", is_default=True)
    assert rule_groups.get_group(first["id"])["is_default"] is False
    assert rule_groups.get_group(second["id"])["is_default"] is True

    defaults = [item for item in rule_groups.list_groups() if item["is_default"]]
    assert len(defaults) == 1


def test_update_can_move_and_clear_default():
    first = make_group("甲", is_default=True)
    second = make_group("乙")
    rule_groups.update(second["id"], {"is_default": True})
    assert rule_groups.get_group(first["id"])["is_default"] is False
    assert rule_groups.get_group(second["id"])["is_default"] is True

    rule_groups.update(second["id"], {"is_default": False})
    assert not [item for item in rule_groups.list_groups() if item["is_default"]]


# ---------------- load_group / default_group ----------------
def test_default_group_none_when_unset():
    make_group()
    # 没有任何默认组时返回 None，即「完全不改变既有搜索行为」
    assert rule_groups.default_group() is None
    assert rule_groups.load_group(None) is None


def test_load_group_by_id():
    group = make_group()
    loaded = rule_groups.load_group(group["id"])
    assert loaded is not None and loaded.name == group["name"]
    assert len(loaded.levels) == 2


def test_load_group_falls_back_to_default():
    fallback = make_group("兜底默认", is_default=True)
    target = make_group("停用的", enabled=False)

    # 停用的组不能生效，退回默认组
    assert rule_groups.load_group(target["id"]).name == fallback["name"]
    # 不存在的 ID 同样退回默认组，而不是报错
    assert rule_groups.load_group(999999).name == fallback["name"]


# ---------------- 删除与解绑 ----------------
def test_delete_unbinds_subscribes():
    group = make_group()
    with session_scope() as session:
        session.add(
            Subscribe(title=f"{PREFIX}的订阅", media_type="tv", rule_group_id=group["id"])
        )

    assert rule_groups.delete(group["id"]) is True
    assert rule_groups.get_group(group["id"]) is None
    with session_scope() as session:
        sub = session.query(Subscribe).filter(
            Subscribe.title == f"{PREFIX}的订阅"
        ).one()
        # 组被删掉后订阅解绑，而不是留一个指向空气的 ID
        assert sub.rule_group_id is None


def test_delete_missing_returns_false():
    assert rule_groups.delete(999999) is False


# ---------------- 试算 ----------------
SAMPLES = [
    {"title": "剧名 S01E01 1080p WEB-DL 中字", "size": "2GB", "seeders": 30},
    {"title": "剧名 S01E01 2160p WEB-DL", "size": "8GB", "seeders": 50},
    {"title": "剧名 S01E01 480p TVRip", "size": "300MB", "seeders": 2},
]


def test_preview_orders_by_level():
    group = make_group()
    result = rule_groups.preview(group["id"], SAMPLES)
    assert result["success"] is True
    assert result["group"] == group["name"]
    assert result["total"] == 3 and result["dropped"] == 0
    # 第 0 层「1080p 中字」必须排在最前
    assert result["items"][0]["rule_level"] == 0
    assert result["items"][0]["rule_level_name"] == "1080p 中字"
    # 未命中的资源被兜底接受，排在最后
    assert result["items"][-1]["rule_level"] > 0


def test_preview_drops_unmatched_when_strict():
    group = make_group(accept_unmatched=False)
    result = rule_groups.preview(group["id"], SAMPLES)
    # 只有两条 1080p/2160p 里命中层级的会留下：2160p 不在任何层 → 被剔除
    assert result["dropped"] >= 1
    assert all(item["rule_level"] < 9999 for item in result["items"])


def test_preview_missing_group():
    result = rule_groups.preview(999999, SAMPLES)
    assert result["success"] is False and result["items"] == []


def test_preview_does_not_mutate_input():
    group = make_group()
    payload = [dict(item) for item in SAMPLES]
    rule_groups.preview(group["id"], payload)
    # 试算必须在副本上做，否则调用方的数据会被塞满 score/rule_level
    assert all("rule_level" not in item for item in payload)


def test_filter_and_rank_uses_group():
    """规则组接进既有过滤链后，排序应由层级主导而非单纯看分数。"""
    group = rule_groups.load_group(make_group()["id"])
    resources = [dict(item) for item in SAMPLES]
    ranked = filters.filter_and_rank(resources, None, group)
    assert ranked[0]["rule_level"] == 0
    assert ranked[0]["rule_group"] == group.name


# ---------------- API ----------------
def test_api_list_and_detail(client, auth_headers):
    group = make_group()
    resp = client.get("/api/v1/rule-groups", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True and body["total"] >= 1
    assert group["name"] in [item["name"] for item in body["items"]]

    detail = client.get(f"/api/v1/rule-groups/{group['id']}", headers=auth_headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["name"] == group["name"]
    assert client.get("/api/v1/rule-groups/999999", headers=auth_headers).status_code == 404


def test_api_crud_roundtrip(client, auth_headers):
    created = client.post(
        "/api/v1/rule-groups",
        headers=auth_headers,
        json={
            "name": f"{PREFIX}接口",
            "levels": [{"name": "4K", "resolution": "2160p"}],
            "accept_unmatched": False,
        },
    )
    assert created.status_code == 200, created.text
    group_id = created.json()["data"]["id"]

    patched = client.patch(
        f"/api/v1/rule-groups/{group_id}",
        headers=auth_headers,
        json={"description": "接口改的", "is_default": True},
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["is_default"] is True

    deleted = client.delete(f"/api/v1/rule-groups/{group_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/rule-groups/{group_id}", headers=auth_headers).status_code == 404
    assert client.delete(f"/api/v1/rule-groups/{group_id}", headers=auth_headers).status_code == 404


def test_api_rejects_empty_levels(client, auth_headers):
    resp = client.post(
        "/api/v1/rule-groups",
        headers=auth_headers,
        json={"name": f"{PREFIX}没层", "levels": []},
    )
    assert resp.status_code == 400


def test_api_rejects_duplicate_name(client, auth_headers):
    make_group("接口同名")
    resp = client.post(
        "/api/v1/rule-groups",
        headers=auth_headers,
        json={
            "name": f"{PREFIX}接口同名",
            "levels": [{"resolution": "1080p"}],
        },
    )
    assert resp.status_code == 400


def test_api_patch_missing_returns_404(client, auth_headers):
    resp = client.patch(
        "/api/v1/rule-groups/999999", headers=auth_headers, json={"description": "x"}
    )
    assert resp.status_code == 404


def test_api_preview(client, auth_headers):
    group = make_group()
    resp = client.post(
        f"/api/v1/rule-groups/{group['id']}/preview",
        headers=auth_headers,
        json={"resources": SAMPLES},
    )
    assert resp.status_code == 200
    assert resp.json()["items"][0]["rule_level"] == 0

    missing = client.post(
        "/api/v1/rule-groups/999999/preview", headers=auth_headers, json={"resources": []}
    )
    assert missing.status_code == 404


def test_api_requires_auth(client):
    assert client.get("/api/v1/rule-groups").status_code == 401
    assert client.post("/api/v1/rule-groups", json={"name": "x"}).status_code == 401
