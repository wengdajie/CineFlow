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


# ---------------------------------------------------------------- STRM 接口
def test_strm_endpoints(client, auth_headers):
    """STRM 概览 / 记录 / 同步端点可用；没有网盘时也不能 500。"""
    overview = client.get("/api/v1/strm", headers=auth_headers)
    assert overview.status_code == 200, overview.text
    data = overview.json()["data"]
    assert {"total", "alive", "invalid", "link_mode", "strm_dir"} <= set(data)

    records = client.get("/api/v1/strm/records?limit=10", headers=auth_headers)
    assert records.status_code == 200
    assert isinstance(records.json()["items"], list)

    # 没有可用网盘时给出提示而不是报错
    synced = client.post("/api/v1/strm/sync", json={}, headers=auth_headers)
    assert synced.status_code == 200, synced.text
    assert synced.json()["success"] is True

    missing = client.post(
        "/api/v1/strm/sync", json={"site_id": 99999999}, headers=auth_headers
    )
    assert missing.status_code == 200
    assert "不存在" in missing.json()["message"] or "未启用" in missing.json()["message"]


def test_strm_play_is_anonymous_but_404_for_unknown(client):
    """播放端点必须免认证（播放器带不了 JWT），但未知记录要回 404。"""
    response = client.get("/api/v1/strm/play/99999999", follow_redirects=False)
    assert response.status_code == 404, response.text
    # 关键：不是 401，说明确实没挂认证依赖
    assert response.status_code != 401


def test_strm_requires_auth(client):
    """除播放端点外，其余 STRM 接口仍需认证。"""
    assert client.get("/api/v1/strm").status_code == 401
    assert client.get("/api/v1/strm/records").status_code == 401


# ------------------------------------------------------------ 分享追更接口
def test_pan_subscribe_crud_api(client, auth_headers):
    """分享追更 CRUD 全链路。"""
    created = client.post(
        "/api/v1/pan-subscribes",
        json={
            "name": "接口测试追更",
            "share_url": "https://pan.quark.cn/s/api-test",
            "exclude_regex": "预告",
            "weekdays": [0, 2, 4],
        },
        headers=auth_headers,
    )
    assert created.status_code == 200, created.text
    record = created.json()["data"]
    assert record["weekdays"] == [0, 2, 4]
    assert record["invalid"] is False

    listed = client.get("/api/v1/pan-subscribes", headers=auth_headers).json()
    assert listed["success"] is True
    assert any(item["id"] == record["id"] for item in listed["items"])

    patched = client.patch(
        f"/api/v1/pan-subscribes/{record['id']}",
        json={"name": "接口测试追更（改）", "status": "paused"},
        headers=auth_headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["status"] == "paused"

    # 暂停状态巡检要被跳过而不是报错
    checked = client.post(
        f"/api/v1/pan-subscribes/{record['id']}/check", headers=auth_headers
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["skipped"] is True

    all_checked = client.post("/api/v1/pan-subscribes/check-all", headers=auth_headers)
    assert all_checked.status_code == 200
    assert "checked" in all_checked.json()

    assert (
        client.delete(
            f"/api/v1/pan-subscribes/{record['id']}", headers=auth_headers
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/v1/pan-subscribes/{record['id']}", headers=auth_headers
        ).status_code
        == 404
    )


def test_pan_subscribe_unknown_id_returns_404(client, auth_headers):
    assert (
        client.patch(
            "/api/v1/pan-subscribes/99999999", json={"name": "x"}, headers=auth_headers
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/pan-subscribes/99999999/check", headers=auth_headers
        ).status_code
        == 404
    )


# ------------------------------------------------------------- 刮削与洗版接口
def test_library_scrape_endpoint(client, auth_headers, tmp_media):
    """补刮接口：对空库也要返回统计而不是报错。"""
    response = client.post(
        "/api/v1/library/scrape",
        json={"limit": 5, "overwrite": False},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {"scanned", "scraped", "skipped", "degraded"} <= set(body)


def test_subscribe_upgrade_endpoint(client, auth_headers):
    """洗版试算：未开最优版本的订阅要明确说明，不存在的回 404。"""
    created = client.post(
        "/api/v1/subscribes",
        json={"title": "洗版接口测试剧", "media_type": "tv", "season": 1},
        headers=auth_headers,
    )
    assert created.status_code == 200, created.text
    sub_id = created.json()["id"]

    result = client.post(
        f"/api/v1/subscribes/{sub_id}/upgrade",
        json={"dry_run": True},
        headers=auth_headers,
    )
    assert result.status_code == 200, result.text
    assert "最优版本" in result.json()["message"]

    missing = client.post(
        "/api/v1/subscribes/99999999/upgrade",
        json={"dry_run": True},
        headers=auth_headers,
    )
    assert missing.status_code == 404

    batch = client.post(
        "/api/v1/subscribes/upgrade-all", json={"dry_run": False}, headers=auth_headers
    )
    assert batch.status_code == 200
    assert "未启用" in batch.json()["message"]

    client.delete(f"/api/v1/subscribes/{sub_id}", headers=auth_headers)


def test_settings_groups_include_new_features(client, auth_headers):
    """新增的配置组必须出现在设置页数据里，否则用户不知道怎么开关。"""
    body = client.get("/api/v1/system/settings", headers=auth_headers).json()
    titles = {group["title"] for group in body["groups"]}
    assert {"刮削与分类", "STRM 同步", "分享追更与洗版"} <= titles
    keys = {item["key"] for group in body["groups"] for item in group["items"]}
    assert {"SCRAPE_ENABLED", "STRM_LINK_MODE", "UPGRADE_ENABLED", "CATEGORY_ENABLED"} <= keys


def test_migrate_columns_upgrades_legacy_table(tmp_path):
    """老版本数据库缺 v1.4.0 新增列时，必须能自动补齐。

    这是真实踩过的坑：``create_all`` 只建缺失的**表**，不会给已存在的表加列，
    老用户升级后一 SELECT 新列就 500。
    """
    import sqlite3

    from sqlalchemy import create_engine, inspect

    from app.db import init_db as init_module

    legacy = tmp_path / "legacy.db"
    connection = sqlite3.connect(legacy)
    # 造一个 v1.3.0 时期的 library_files（没有 quality_score / upgrade_count）
    connection.execute(
        "CREATE TABLE library_files (id INTEGER PRIMARY KEY, path TEXT, title TEXT)"
    )
    connection.commit()
    connection.close()

    engine = create_engine(f"sqlite:///{legacy}")
    original = init_module.engine
    init_module.engine = engine
    try:
        init_module.migrate_columns()
        columns = {item["name"] for item in inspect(engine).get_columns("library_files")}
        assert {"quality_score", "upgrade_count"} <= columns
        # 幂等：再跑一次不能报错
        init_module.migrate_columns()
    finally:
        init_module.engine = original
        engine.dispose()


# ---------------- v1.5.0 老库升级与新端点 ----------------
def test_migrate_columns_upgrades_users_and_subscribes(tmp_path):
    """v1.5.0 给 users 加 role/note、给 subscribes 加 rule_group_id。

    ``users.role`` 的默认值必须是 ``admin``：老库里唯一的那个管理员
    如果被补成 viewer，用户升级后就再也进不了后台了。
    """
    import sqlite3

    from sqlalchemy import create_engine, inspect, text

    from app.db import init_db as init_module

    legacy = tmp_path / "legacy_users.db"
    connection = sqlite3.connect(legacy)
    # v1.4.0 时期的 users / subscribes：没有 role、note、rule_group_id
    connection.execute(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, username TEXT, "
        "password_hash TEXT, is_superuser INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1)"
    )
    connection.execute(
        "CREATE TABLE subscribes (id INTEGER PRIMARY KEY, title TEXT, media_type TEXT)"
    )
    connection.execute(
        "INSERT INTO users (username, password_hash, is_superuser) VALUES ('old', 'h', 1)"
    )
    connection.commit()
    connection.close()

    engine = create_engine(f"sqlite:///{legacy}")
    original = init_module.engine
    init_module.engine = engine
    try:
        init_module.migrate_columns()
        inspector = inspect(engine)
        user_columns = {item["name"] for item in inspector.get_columns("users")}
        assert {"role", "note"} <= user_columns
        sub_columns = {item["name"] for item in inspector.get_columns("subscribes")}
        assert "rule_group_id" in sub_columns

        with engine.begin() as conn:
            role = conn.execute(text("SELECT role FROM users WHERE username='old'")).scalar()
        assert role == "admin", "老管理员不能被降级，否则会被锁在系统外"

        init_module.migrate_columns()  # 幂等
    finally:
        init_module.engine = original
        engine.dispose()


def test_settings_groups_include_v150_features(client, auth_headers):
    """v1.5.0 三组新配置必须出现在设置页分组里。"""
    response = client.get("/api/v1/system/settings", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    keys = {item["key"] for group in body["groups"] for item in group["items"]}
    assert {
        "SITE_HEALTH_ENABLED",
        "SITE_AUTO_DISABLE",
        "DOWNLOADER_STRATEGY",
        "DOWNLOADER_FAILOVER",
        "RANKING_INTERVAL_MINUTES",
        "RANKING_MAX_PER_RUN",
    } <= keys
    # 每一项都要带出界面渲染所需的元信息，否则设置页没法画控件
    sample = next(
        item for group in body["groups"] for item in group["items"] if item["key"] == "DOWNLOADER_STRATEGY"
    )
    assert sample["editable"] is True
    assert sample["choices"], "选项型配置必须给出可选值"
    assert body["editable_total"] > 0


def test_v150_endpoints_smoke(client, auth_headers):
    """新增的四个子系统端点都能正常返回（不联网）。"""
    for path in (
        "/api/v1/site-health",
        "/api/v1/site-health/records",
        "/api/v1/ranking-rules",
        "/api/v1/rule-groups",
        "/api/v1/users",
    ):
        response = client.get(path, headers=auth_headers)
        assert response.status_code == 200, f"{path} -> {response.text}"
        assert response.json()["success"] is True


def test_settings_update_and_reset(client, auth_headers):
    """在线改配置要真生效，并且能恢复默认。"""
    from app.core.config import settings

    original = settings.RANKING_MAX_PER_RUN
    try:
        response = client.put(
            "/api/v1/system/settings",
            headers=auth_headers,
            json={"values": {"RANKING_MAX_PER_RUN": original + 3}},
        )
        assert response.status_code == 200, response.text
        # 关键点：改完 settings 单例要立刻是新值，否则"能改"只是假象
        assert original + 3 == settings.RANKING_MAX_PER_RUN

        reset = client.post(
            "/api/v1/system/settings/reset",
            headers=auth_headers,
            json={"keys": ["RANKING_MAX_PER_RUN"]},
        )
        assert reset.status_code == 200
        assert original == settings.RANKING_MAX_PER_RUN
    finally:
        client.post(
            "/api/v1/system/settings/reset", headers=auth_headers, json={"keys": None}
        )


def test_settings_update_rejects_unknown_and_invalid(client, auth_headers):
    """白名单外的键与非法值都要整体拒绝。"""
    assert (
        client.put(
            "/api/v1/system/settings",
            headers=auth_headers,
            json={"values": {"SECRET_KEY": "偷偷改密钥"}},
        ).status_code
        == 400
    )
    assert (
        client.put(
            "/api/v1/system/settings",
            headers=auth_headers,
            json={"values": {"DOWNLOADER_STRATEGY": "不存在的策略"}},
        ).status_code
        == 400
    )


def test_subscribe_accepts_rule_group(client, auth_headers):
    """订阅可以绑定规则组。"""
    group = client.post(
        "/api/v1/rule-groups",
        headers=auth_headers,
        json={"name": "接口订阅用规则组", "levels": [{"resolution": "1080p"}]},
    )
    assert group.status_code == 200, group.text
    group_id = group.json()["data"]["id"]

    created = client.post(
        "/api/v1/subscribes",
        headers=auth_headers,
        json={"title": "绑定规则组的剧", "media_type": "tv", "rule_group_id": group_id},
    )
    assert created.status_code == 200, created.text
    sub_id = created.json()["id"]
    assert created.json()["rule_group_id"] == group_id

    # 删掉规则组后订阅要自动解绑，而不是留悬空 ID
    assert client.delete(f"/api/v1/rule-groups/{group_id}", headers=auth_headers).status_code == 200
    detail = client.get(f"/api/v1/subscribes/{sub_id}", headers=auth_headers).json()
    assert detail["rule_group_id"] is None
    client.delete(f"/api/v1/subscribes/{sub_id}", headers=auth_headers)


def test_schedules_expose_v150_jobs(client, auth_headers):
    """站点健康与榜单订阅两个新任务要出现在调度列表里。"""
    response = client.get("/api/v1/schedules", headers=auth_headers)
    assert response.status_code == 200
    keys = {item["key"] for item in response.json()["items"]}
    assert {"site_health", "ranking"} <= keys
