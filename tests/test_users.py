"""用户与角色权限的单元测试（v1.5.0）。

重点验证三件事：
1. 三档角色的鉴权边界（viewer 只读、operator 能跑业务、admin 能改配置）；
2. 自我保护规则（不能删/停自己、最后一个管理员不能降级）；
3. ``role_of`` 对脏数据的兜底。
"""

from __future__ import annotations

import pytest

from app.api.deps import ROLE_LABELS, role_of
from app.db.models import User
from app.db.session import session_scope
from app.schemas.enums import UserRole

VIEWER = ("cf_test_viewer", "viewer-pass")
OPERATOR = ("cf_test_operator", "operator-pass")
TEST_USERS = {"cf_test_viewer", "cf_test_operator", "cf_test_extra", "cf_test_admin2"}


def _purge() -> None:
    """删掉本文件造出来的账号。

    必须清理干净：``test_users`` 的用户计数与其它模块的 admin 数量断言都依赖它。
    """
    with session_scope() as session:
        session.query(User).filter(User.username.in_(TEST_USERS)).delete(
            synchronize_session=False
        )


def _create(client, headers, username, password, role, **extra):
    payload = {"username": username, "password": password, "role": role}
    payload.update(extra)
    return client.post("/api/v1/users", headers=headers, json=payload)


def _login(client, username, password):
    resp = client.post(
        "/api/v1/auth/login", data={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def accounts(client, auth_headers):
    """建一个 viewer 和一个 operator，模块结束时清理。"""
    _purge()
    assert _create(client, auth_headers, *VIEWER, "viewer").status_code == 200
    assert _create(client, auth_headers, *OPERATOR, "operator").status_code == 200
    tokens = {
        "viewer": _headers(_login(client, *VIEWER)["access_token"]),
        "operator": _headers(_login(client, *OPERATOR)["access_token"]),
    }
    yield tokens
    _purge()


@pytest.fixture(autouse=True)
def _cleanup_extras(client):
    """每个用例结束后清掉临时账号，只留 accounts fixture 建的两个。"""
    yield
    with session_scope() as session:
        session.query(User).filter(
            User.username.in_({"cf_test_extra", "cf_test_admin2"})
        ).delete(synchronize_session=False)


# ---------------- 角色模型 ----------------
def test_role_rank_is_ordered():
    assert UserRole.VIEWER.rank < UserRole.OPERATOR.rank < UserRole.ADMIN.rank
    assert set(ROLE_LABELS) == {item.value for item in UserRole}


def test_role_of_falls_back_on_dirty_value():
    """历史脏值不能让鉴权 500，也不能误放行。"""
    dirty_admin = User(username="x", password_hash="x", role="不认识的角色", is_superuser=True)
    dirty_plain = User(username="y", password_hash="y", role=None, is_superuser=False)
    assert role_of(dirty_admin) is UserRole.ADMIN
    assert role_of(dirty_plain) is UserRole.VIEWER


def test_role_of_reads_role_column():
    for role in UserRole:
        user = User(username="z", password_hash="z", role=role.value)
        assert role_of(user) is role


# ---------------- 登录与 /auth/me 带角色 ----------------
def test_login_returns_role(client, accounts):
    assert _login(client, *VIEWER)["role"] == UserRole.VIEWER.value
    assert _login(client, *OPERATOR)["role"] == UserRole.OPERATOR.value


def test_admin_login_returns_admin_role(client):
    body = _login(client, "admin", "cineflow")
    assert body["role"] == UserRole.ADMIN.value and body["is_superuser"] is True


def test_me_returns_role_detail(client, accounts):
    body = client.get("/api/v1/auth/me", headers=accounts["operator"]).json()
    assert body["role"] == UserRole.OPERATOR.value
    assert body["role_label"] == ROLE_LABELS[UserRole.OPERATOR.value]
    assert body["rank"] == UserRole.OPERATOR.rank


# ---------------- 鉴权边界 ----------------
def test_viewer_can_read(client, accounts):
    for path in ("/api/v1/subscribes", "/api/v1/rule-groups", "/api/v1/schedules"):
        assert client.get(path, headers=accounts["viewer"]).status_code == 200, path


def test_viewer_cannot_write(client, accounts):
    """viewer 调运营类写接口应当 403，且提示里要说明缺什么权限。"""
    resp = client.post(
        "/api/v1/subscribes", headers=accounts["viewer"], json={"title": "不该建成功"}
    )
    assert resp.status_code == 403
    assert "操作员" in resp.json()["detail"]


def test_operator_cannot_touch_admin_endpoints(client, accounts):
    """operator 能跑业务，但改不了系统配置与用户。"""
    assert client.get("/api/v1/users", headers=accounts["operator"]).status_code == 403
    assert (
        client.put(
            "/api/v1/system/settings",
            headers=accounts["operator"],
            json={"values": {"SEARCH_TIMEOUT": 30}},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/rule-groups",
            headers=accounts["operator"],
            json={"name": "operator 不该能建", "levels": [{"resolution": "1080p"}]},
        ).status_code
        == 403
    )


def test_operator_can_do_business_writes(client, accounts):
    """operator 必须真的能干活，否则角色分级就成了摆设。"""
    resp = client.post(
        "/api/v1/rule-groups/1/preview",
        headers=accounts["operator"],
        json={"resources": [{"title": "剧名 1080p WEB-DL", "size": "2GB"}]},
    )
    assert resp.status_code in (200, 404)  # 404 只代表规则组 #1 不存在，不是权限问题


def test_viewer_cannot_list_users(client, accounts):
    assert client.get("/api/v1/users", headers=accounts["viewer"]).status_code == 403


def test_admin_passes(client, auth_headers):
    resp = client.get("/api/v1/users", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert [item["value"] for item in body["roles"]] == [item.value for item in UserRole]


def test_users_requires_auth(client):
    assert client.get("/api/v1/users").status_code == 401
    assert client.post("/api/v1/users", json={}).status_code == 401


# ---------------- 用户 CRUD ----------------
def test_create_derives_is_superuser_from_role(client, auth_headers):
    """``is_superuser`` 不由前端传，而是从 role 推导，保证两者永远一致。"""
    admin = _create(client, auth_headers, "cf_test_admin2", "admin-pass", "admin").json()
    assert admin["data"]["is_superuser"] is True
    assert admin["data"]["rank"] == UserRole.ADMIN.rank

    plain = _create(client, auth_headers, "cf_test_extra", "extra-pass", "operator").json()
    assert plain["data"]["is_superuser"] is False
    assert plain["data"]["role_label"] == ROLE_LABELS["operator"]


def test_create_defaults_to_viewer(client, auth_headers):
    resp = client.post(
        "/api/v1/users",
        headers=auth_headers,
        json={"username": "cf_test_extra", "password": "extra-pass"},
    )
    # 默认给最小权限，避免手滑开出一个管理员
    assert resp.json()["data"]["role"] == UserRole.VIEWER.value


def test_create_rejects_duplicate_username(client, auth_headers, accounts):
    resp = _create(client, auth_headers, VIEWER[0], "another-pass", "viewer")
    assert resp.status_code == 400
    assert "已存在" in resp.json()["detail"]


def test_create_validates_password_length(client, auth_headers):
    resp = _create(client, auth_headers, "cf_test_extra", "123", "viewer")
    assert resp.status_code == 422


def test_update_role_and_note(client, auth_headers):
    user_id = _create(
        client, auth_headers, "cf_test_extra", "extra-pass", "viewer"
    ).json()["data"]["id"]
    resp = client.patch(
        f"/api/v1/users/{user_id}",
        headers=auth_headers,
        json={"role": "admin", "note": "临时提权"},
    )
    data = resp.json()["data"]
    assert data["role"] == "admin" and data["is_superuser"] is True
    assert data["note"] == "临时提权"

    # 降级回去时 is_superuser 也要跟着落下来
    downgraded = client.patch(
        f"/api/v1/users/{user_id}", headers=auth_headers, json={"role": "viewer"}
    ).json()["data"]
    assert downgraded["is_superuser"] is False


def test_update_password_takes_effect(client, auth_headers):
    user_id = _create(
        client, auth_headers, "cf_test_extra", "extra-pass", "viewer"
    ).json()["data"]["id"]
    assert (
        client.patch(
            f"/api/v1/users/{user_id}", headers=auth_headers, json={"password": "new-pass-1"}
        ).status_code
        == 200
    )
    assert _login(client, "cf_test_extra", "new-pass-1")["username"] == "cf_test_extra"


def test_disabled_user_cannot_login(client, auth_headers):
    user_id = _create(
        client, auth_headers, "cf_test_extra", "extra-pass", "operator"
    ).json()["data"]["id"]
    client.patch(f"/api/v1/users/{user_id}", headers=auth_headers, json={"is_active": False})
    resp = client.post(
        "/api/v1/auth/login", data={"username": "cf_test_extra", "password": "extra-pass"}
    )
    assert resp.status_code == 403


def test_update_and_delete_missing_user(client, auth_headers):
    assert (
        client.patch("/api/v1/users/999999", headers=auth_headers, json={"note": "x"}).status_code
        == 404
    )
    assert client.delete("/api/v1/users/999999", headers=auth_headers).status_code == 404


def test_delete_user(client, auth_headers):
    user_id = _create(
        client, auth_headers, "cf_test_extra", "extra-pass", "viewer"
    ).json()["data"]["id"]
    resp = client.delete(f"/api/v1/users/{user_id}", headers=auth_headers)
    assert resp.status_code == 200 and "已删除" in resp.json()["message"]
    assert client.delete(f"/api/v1/users/{user_id}", headers=auth_headers).status_code == 404


# ---------------- 自我保护 ----------------
def test_cannot_delete_self(client, auth_headers):
    me = client.get("/api/v1/users", headers=auth_headers).json()["items"]
    admin_id = next(item["id"] for item in me if item["username"] == "admin")
    resp = client.delete(f"/api/v1/users/{admin_id}", headers=auth_headers)
    assert resp.status_code == 400 and "自己" in resp.json()["detail"]


def test_cannot_disable_self(client, auth_headers):
    items = client.get("/api/v1/users", headers=auth_headers).json()["items"]
    admin_id = next(item["id"] for item in items if item["username"] == "admin")
    # 先造第二个管理员，排除"最后一个管理员"规则，确保命中的是"不能停用自己"
    _create(client, auth_headers, "cf_test_admin2", "admin-pass", "admin")
    resp = client.patch(
        f"/api/v1/users/{admin_id}", headers=auth_headers, json={"is_active": False}
    )
    assert resp.status_code == 400 and "自己" in resp.json()["detail"]


def test_last_admin_cannot_be_downgraded(client, auth_headers):
    items = client.get("/api/v1/users", headers=auth_headers).json()["items"]
    admins = [item for item in items if item["role"] == "admin" and item["is_active"]]
    assert len(admins) == 1, "前置条件：此时应当只有一个启用中的管理员"
    resp = client.patch(
        f"/api/v1/users/{admins[0]['id']}", headers=auth_headers, json={"role": "operator"}
    )
    assert resp.status_code == 400 and "最后一个" in resp.json()["detail"]


def test_second_admin_can_be_downgraded(client, auth_headers):
    """有两个管理员时，降级其中一个是允许的。"""
    user_id = _create(
        client, auth_headers, "cf_test_admin2", "admin-pass", "admin"
    ).json()["data"]["id"]
    resp = client.patch(
        f"/api/v1/users/{user_id}", headers=auth_headers, json={"role": "viewer"}
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["role"] == "viewer"


def test_second_admin_can_be_deleted(client, auth_headers):
    user_id = _create(
        client, auth_headers, "cf_test_admin2", "admin-pass", "admin"
    ).json()["data"]["id"]
    assert client.delete(f"/api/v1/users/{user_id}", headers=auth_headers).status_code == 200


def test_disabled_admin_does_not_count_as_last_admin(client, auth_headers):
    """停用的管理员不算"还有管理员"，否则会出现没人能登录的死局。"""
    other_id = _create(
        client, auth_headers, "cf_test_admin2", "admin-pass", "admin"
    ).json()["data"]["id"]
    client.patch(f"/api/v1/users/{other_id}", headers=auth_headers, json={"is_active": False})

    items = client.get("/api/v1/users", headers=auth_headers).json()["items"]
    admin_id = next(item["id"] for item in items if item["username"] == "admin")
    resp = client.patch(
        f"/api/v1/users/{admin_id}", headers=auth_headers, json={"role": "operator"}
    )
    assert resp.status_code == 400 and "最后一个" in resp.json()["detail"]
