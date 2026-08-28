"""API 接口测试。"""

from __future__ import annotations


def test_health_no_auth(client):
    """健康检查无需认证。"""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_login_success(client):
    """登录成功返回令牌。"""
    response = client.post(
        "/api/v1/auth/login", data={"username": "admin", "password": "cineflow"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
    assert body["is_superuser"] is True
    assert body["access_token"]


def test_login_wrong_password(client):
    """错误口令返回 401。"""
    response = client.post(
        "/api/v1/auth/login", data={"username": "admin", "password": "bad"}
    )
    assert response.status_code == 401


def test_protected_requires_auth(client):
    """未认证访问受保护接口返回 401。"""
    assert client.get("/api/v1/system/info").status_code == 401
    assert client.get("/api/v1/subscribes").status_code == 401


def test_invalid_token(client):
    """伪造令牌返回 401。"""
    response = client.get(
        "/api/v1/system/info", headers={"Authorization": "Bearer forged.token.value"}
    )
    assert response.status_code == 401


def test_system_info(client, auth_headers):
    """系统信息包含关键字段。"""
    body = client.get("/api/v1/system/info", headers=auth_headers).json()
    assert body["success"] is True
    assert body["version"]
    assert "library" in body["directories"]


def test_default_sites_seeded(client, auth_headers):
    """首次启动写入示例站点且默认禁用。"""
    sites = client.get("/api/v1/sites", headers=auth_headers).json()
    assert len(sites) >= 5
    assert all(item["enabled"] is False for item in sites)


def test_providers_listed(client, auth_headers):
    """Provider 列表非空。"""
    providers = client.get("/api/v1/sites/providers", headers=auth_headers).json()
    assert len(providers) >= 15


def test_recognize_endpoint(client, auth_headers):
    """识别接口返回结构化元数据。"""
    response = client.get(
        "/api/v1/media/recognize",
        params={"name": "Some.Show.S02E07.2160p.WEB-DL.H265-Group.mkv"},
        headers=auth_headers,
    )
    meta = response.json()["meta"]
    assert meta["season"] == 2
    assert meta["episodes"] == [7]
    assert meta["resolution"] == "2160p"


def test_site_crud(client, auth_headers):
    """站点增删改查完整流程。"""
    created = client.post(
        "/api/v1/sites",
        json={
            "name": "临时测试站",
            "kind": "indexer",
            "provider": "torznab",
            "url": "http://127.0.0.1:9999/api",
            "priority": 20,
            "enabled": False,
        },
        headers=auth_headers,
    )
    assert created.status_code == 200
    site_id = created.json()["id"]

    # 重名冲突
    duplicate = client.post(
        "/api/v1/sites",
        json={"name": "临时测试站", "kind": "indexer", "provider": "torznab"},
        headers=auth_headers,
    )
    assert duplicate.status_code == 409

    # 未知 provider
    invalid = client.post(
        "/api/v1/sites",
        json={"name": "非法", "kind": "indexer", "provider": "no-such-provider"},
        headers=auth_headers,
    )
    assert invalid.status_code == 400

    updated = client.patch(
        f"/api/v1/sites/{site_id}", json={"priority": 5}, headers=auth_headers
    )
    assert updated.json()["priority"] == 5

    deleted = client.delete(f"/api/v1/sites/{site_id}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.delete(f"/api/v1/sites/{site_id}", headers=auth_headers).status_code == 404


def test_subscribe_lifecycle(client, auth_headers):
    """订阅创建、查询缺集、暂停、删除。"""
    created = client.post(
        "/api/v1/subscribes",
        json={"title": "接口测试剧", "media_type": "tv", "season": 1, "total_episodes": 4},
        headers=auth_headers,
    )
    assert created.status_code == 200
    sub_id = created.json()["id"]
    assert created.json()["status"] == "active"

    duplicate = client.post(
        "/api/v1/subscribes",
        json={"title": "接口测试剧", "media_type": "tv", "season": 1},
        headers=auth_headers,
    )
    assert duplicate.status_code == 400

    missing = client.get(f"/api/v1/subscribes/{sub_id}/missing", headers=auth_headers)
    assert missing.json()["missing"] == [1, 2, 3, 4]

    paused = client.patch(
        f"/api/v1/subscribes/{sub_id}", json={"status": "paused"}, headers=auth_headers
    )
    assert paused.json()["status"] == "paused"

    assert client.delete(f"/api/v1/subscribes/{sub_id}", headers=auth_headers).status_code == 200
    assert client.get(f"/api/v1/subscribes/{sub_id}", headers=auth_headers).status_code == 404


def test_search_without_sites(client, auth_headers):
    """无启用站点时搜索返回空结果而非报错。"""
    response = client.post(
        "/api/v1/search", json={"keyword": "不存在的片名"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_dashboard_and_library(client, auth_headers):
    """仪表盘与媒体库统计可用。"""
    dashboard = client.get("/api/v1/system/dashboard", headers=auth_headers).json()
    assert "subscribes" in dashboard and "library" in dashboard

    stats = client.get("/api/v1/library/stats", headers=auth_headers).json()
    assert stats["success"] is True
    assert "files" in stats["data"]


def test_logs_and_jobs(client, auth_headers):
    """日志与任务接口可用。"""
    logs = client.get("/api/v1/system/logs?limit=10", headers=auth_headers).json()
    assert logs["success"] is True

    jobs = client.get("/api/v1/system/jobs", headers=auth_headers).json()
    assert jobs["success"] is True  # 测试环境调度器关闭，列表为空


def test_plugins_endpoint(client, auth_headers):
    """插件列表可用。"""
    body = client.get("/api/v1/plugins", headers=auth_headers).json()
    assert body["success"] is True
    assert isinstance(body["items"], list)


def test_transfer_dry_run_api(client, auth_headers, tmp_media):
    """整理接口支持试运行。"""
    source = tmp_media("接口整理剧/Show.S01E01.1080p.WEB-DL.mkv")
    response = client.post(
        "/api/v1/library/transfer",
        json={"source": str(source.parent), "dry_run": True},
        headers=auth_headers,
    )
    body = response.json()
    assert body["success"] is True
    assert body["total"] == 1
    assert body["items"][0]["target"] is not None
