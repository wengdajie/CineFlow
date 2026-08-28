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


# ---------------------------------------------------------------- 自定义站点接口
def test_site_presets_endpoint(client, auth_headers):
    """预设模板列表可用，且每个 provider 都真实存在。"""
    response = client.get("/api/v1/sites/presets", headers=auth_headers)
    assert response.status_code == 200
    items = response.json()
    assert items
    ids = {item["id"] for item in items}
    assert {"mukaku", "api_generic", "html_generic"} <= ids

    filtered = client.get(
        "/api/v1/sites/presets", params={"kind": "pan"}, headers=auth_headers
    ).json()
    assert filtered and all(item["kind"] == "pan" for item in filtered)


def test_apply_preset_creates_site(client, auth_headers):
    """套用预设可一键创建站点，默认不启用。"""
    response = client.post(
        "/api/v1/sites/presets/mukaku/apply",
        params={"name": "预设测试站"},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    site = response.json()
    assert site["provider"] == "mukaku"
    assert site["enabled"] is False

    # 重名应拒绝
    again = client.post(
        "/api/v1/sites/presets/mukaku/apply",
        params={"name": "预设测试站"},
        headers=auth_headers,
    )
    assert again.status_code == 409

    assert client.post(
        "/api/v1/sites/presets/不存在/apply", headers=auth_headers
    ).status_code == 404

    client.delete(f"/api/v1/sites/{site['id']}", headers=auth_headers)


def test_custom_api_site_crud(client, auth_headers):
    """自定义 JSON API 站点：options 字段映射应完整存取。"""
    options = {
        "api_base": "https://custom.test/api",
        "search_path": "search",
        "query_key": "kw",
        "list_path": "data.list",
        "item_map": {"title": "name", "link": "magnet"},
    }
    created = client.post(
        "/api/v1/sites",
        json={
            "name": "自定义接口站",
            "kind": "indexer",
            "provider": "api_generic",
            "url": "https://custom.test",
            "options": options,
        },
        headers=auth_headers,
    )
    assert created.status_code == 200, created.text
    site = created.json()
    assert site["options"]["item_map"]["title"] == "name"

    # 更新字段映射
    patched = client.patch(
        f"/api/v1/sites/{site['id']}",
        json={"options": {**options, "limit": 50}},
        headers=auth_headers,
    )
    assert patched.json()["options"]["limit"] == 50

    client.delete(f"/api/v1/sites/{site['id']}", headers=auth_headers)


def test_discover_endpoint(client, auth_headers, monkeypatch):
    """站点发现接口：解析导航站并标记已添加。"""
    from app.services import discovery as discovery_service

    html_text = (
        '<a href="javascript:" data-id="1" data-url="https://movie.test"'
        ' title="高清影视：追剧资源站"><div class="text-sm overflowClip_1"> 影视站 </div></a>'
    )

    async def fake_fetch_text(url, **kwargs):
        return html_text

    monkeypatch.setattr(discovery_service, "fetch_text", fake_fetch_text)

    response = client.get(
        "/api/v1/sites/discover", params={"media_only": True}, headers=auth_headers
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total"] >= 1
    site = data["sites"][0]
    assert site["domain"] == "movie.test"
    assert "already_added" in site
    assert data["directories_builtin"]


def test_radar_endpoints(client, auth_headers, monkeypatch):
    """追新雷达接口：预览与手动触发。"""
    from app.services import radar as radar_service

    async def fake_fetch_feed(limit_per_site=100):
        from app.providers.base import Resource

        return [
            Resource(
                title="Some.Show.S01E01.2160p.WEB-DL",
                link="magnet:?xt=urn:btih:" + "A" * 32,
                site="假站点",
                kind="magnet",
            )
        ]

    monkeypatch.setattr(radar_service, "fetch_feed", fake_fetch_feed)

    feed = client.get(
        "/api/v1/radar/feed", params={"limit_per_site": 5}, headers=auth_headers
    )
    assert feed.status_code == 200, feed.text
    assert feed.json()["data"]["total"] == 1

    run = client.post(
        "/api/v1/radar/run", params={"dry_run": True}, headers=auth_headers
    )
    assert run.status_code == 200, run.text
    assert run.json()["data"]["dry_run"] is True

    jobs = client.get("/api/v1/radar/jobs", headers=auth_headers)
    assert jobs.status_code == 200
    assert "radar_enabled" in jobs.json()["data"]
