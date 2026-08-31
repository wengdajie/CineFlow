"""运行期可编辑配置（``config_store``）的单元测试。

核心要验的是：**能改的必须真生效**、非法值必须整体拒绝、重置能回到静态默认值。
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import config_store, settings_store


@pytest.fixture(autouse=True)
def _clean(client):
    """每个用例前后都清空覆盖，避免互相污染 settings 单例。"""
    settings_store.delete_setting(config_store.KEY_RUNTIME)
    config_store.reset()
    yield
    config_store.reset()
    settings_store.delete_setting(config_store.KEY_RUNTIME)


# ---------------- 白名单 ----------------
def test_editable_whitelist_excludes_restart_only_keys():
    """目录/端口/密钥/数据库这些改了必须重启的项不能进白名单（ADR-18）。"""
    for key in ("DATA_DIR", "LIBRARY_DIR", "PORT", "HOST", "SECRET_KEY", "DB_URL", "API_TOKEN"):
        assert not config_store.is_editable(key), f"{key} 不该可在线修改"
    # 运行期读取即生效的项必须在白名单里
    for key in ("TRANSFER_MODE", "SEARCH_TIMEOUT", "SUBSCRIBE_INTERVAL_MINUTES",
                "DOWNLOADER_STRATEGY", "RANKING_MAX_PER_RUN", "SITE_HEALTH_ENABLED"):
        assert config_store.is_editable(key), f"{key} 应该可在线修改"


def test_every_editable_key_exists_on_settings():
    """白名单里的键必须真的是配置项，否则界面会渲染出改不了的幽灵字段。"""
    for key in config_store.EDITABLE:
        assert hasattr(settings, key), key


def test_describe_reports_metadata():
    meta = config_store.describe("DOWNLOADER_STRATEGY")
    assert meta["editable"] is True
    assert meta["type"] == "choice"
    assert "least_tasks" in meta["choices"]
    assert config_store.describe("SECRET_KEY") == {"editable": False}


# ---------------- coerce 类型与边界 ----------------
@pytest.mark.parametrize(
    ("key", "raw", "expected"),
    [
        ("SCRAPE_ENABLED", "true", True),
        ("SCRAPE_ENABLED", "关闭", False),
        ("SCRAPE_ENABLED", 1, True),
        ("SEARCH_TIMEOUT", "45", 45),
        ("UPGRADE_SCORE_DELTA", "12.5", 12.5),
        ("PREFER_RESOLUTIONS", "2160p, 1080p", ["2160p", "1080p"]),
        ("PREFER_RESOLUTIONS", ["2160p", " 1080p "], ["2160p", "1080p"]),
        ("EXCLUDE_KEYWORDS", "枪版、抢先版", ["枪版", "抢先版"]),
        ("DOWNLOADER_STRATEGY", "least_tasks", "least_tasks"),
        ("MOVIE_TEMPLATE", "  {title}  ", "{title}"),
    ],
)
def test_coerce_valid(key, raw, expected):
    assert config_store.coerce(key, raw) == expected


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("SCRAPE_ENABLED", "maybe"),      # 非布尔
        ("SEARCH_TIMEOUT", "abc"),        # 非数字
        ("SEARCH_TIMEOUT", "1.5"),        # 整数项给了小数
        ("SEARCH_TIMEOUT", 1),            # 低于下限 3
        ("SEARCH_TIMEOUT", 99999),        # 高于上限 300
        ("DOWNLOADER_STRATEGY", "random"),  # 非法枚举
        ("LIBRARY_SCAN_CRON", "not a cron"),  # 非法 cron
        ("SCRAPE_CRON", "60 25 * * *"),   # 越界 cron
        ("NOT_A_KEY", "x"),               # 不在白名单
    ],
)
def test_coerce_rejects(key, raw):
    with pytest.raises(ValueError):
        config_store.coerce(key, raw)


def test_cron_validation_shares_scheduler_rules():
    """cron 校验必须复用调度器的解析器，否则设置页放过、调度器起不来。"""
    assert config_store.coerce("LIBRARY_SCAN_CRON", "30 3 * * *") == "30 3 * * *"
    assert config_store.coerce("SCRAPE_CRON", "") == ""


# ---------------- update / 立即生效 / 持久化 ----------------
def test_update_takes_effect_immediately_and_persists():
    original = settings.SEARCH_TIMEOUT
    try:
        config_store.update({"SEARCH_TIMEOUT": 44, "AUTO_DOWNLOAD_BEST": "true"})
        # 立即生效：所有读 settings.X 的既有代码零改动就拿到新值
        assert settings.SEARCH_TIMEOUT == 44
        assert settings.AUTO_DOWNLOAD_BEST is True
        # 落库：重启后仍在
        stored = settings_store.get_setting(config_store.KEY_RUNTIME)
        assert stored["SEARCH_TIMEOUT"] == 44
        assert config_store.overrides()["AUTO_DOWNLOAD_BEST"] is True
    finally:
        config_store.reset(["SEARCH_TIMEOUT", "AUTO_DOWNLOAD_BEST"])
    assert original == settings.SEARCH_TIMEOUT


def test_update_validates_all_before_writing_anything():
    """一项非法就整体拒绝，不留"改了 5 项第 3 项报错"的半吊子状态。"""
    before = settings.SEARCH_TIMEOUT
    with pytest.raises(ValueError):
        config_store.update({"SEARCH_TIMEOUT": 50, "DOWNLOADER_STRATEGY": "nope"})
    assert before == settings.SEARCH_TIMEOUT
    assert "SEARCH_TIMEOUT" not in config_store.overrides()


def test_update_rejects_empty_payload():
    with pytest.raises(ValueError):
        config_store.update({})


def test_apply_overrides_restores_after_restart():
    """模拟重启：先落库，再把 settings 改回去，apply_overrides 应重新套上。"""
    config_store.update({"SEARCH_MAX_RESULTS": 123})
    object.__setattr__(settings, "SEARCH_MAX_RESULTS", 999)
    applied = config_store.apply_overrides()
    assert applied["SEARCH_MAX_RESULTS"] == 123
    assert settings.SEARCH_MAX_RESULTS == 123
    config_store.reset(["SEARCH_MAX_RESULTS"])


def test_apply_overrides_ignores_broken_values(caplog):
    """老库里的非法值不能阻塞启动。"""
    settings_store.set_setting(config_store.KEY_RUNTIME, {"SEARCH_TIMEOUT": "abc"})
    applied = config_store.apply_overrides()
    assert "SEARCH_TIMEOUT" not in applied


def test_overrides_drops_unknown_keys():
    """降级再升级会留下白名单外的残留键，读取时必须忽略。"""
    settings_store.set_setting(
        config_store.KEY_RUNTIME, {"SEARCH_TIMEOUT": 30, "GONE_KEY": 1}
    )
    assert set(config_store.overrides()) == {"SEARCH_TIMEOUT"}


# ---------------- reset ----------------
def test_reset_returns_to_static_default():
    default = settings.MIN_SEEDERS
    config_store.update({"MIN_SEEDERS": 77})
    assert settings.MIN_SEEDERS == 77
    assert config_store.reset(["MIN_SEEDERS"]) == ["MIN_SEEDERS"]
    assert default == settings.MIN_SEEDERS
    assert "MIN_SEEDERS" not in config_store.overrides()


def test_reset_all_and_noop():
    config_store.update({"MIN_SEEDERS": 5, "SEARCH_CONCURRENCY": 7})
    keys = config_store.reset()
    assert set(keys) == {"MIN_SEEDERS", "SEARCH_CONCURRENCY"}
    assert config_store.reset() == []          # 没有覆盖时是 no-op
    assert config_store.reset(["MIN_SEEDERS"]) == []


def test_reschedule_triggered_only_for_period_keys(monkeypatch):
    """只有周期类配置改动才该重建触发器（否则每次保存都白折腾一遍调度）。"""
    calls = []
    monkeypatch.setattr(config_store, "_reschedule", lambda: calls.append(1))
    config_store.update({"MIN_SEEDERS": 3})
    assert calls == []
    config_store.update({"SUBSCRIBE_INTERVAL_MINUTES": 40})
    assert calls == [1]
    config_store.reset(["SUBSCRIBE_INTERVAL_MINUTES", "MIN_SEEDERS"])
    assert len(calls) == 2


# ---------------- API ----------------
def test_settings_api_exposes_editable_metadata(client, auth_headers):
    response = client.get("/api/v1/system/settings", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["editable_total"] > 30
    flat = {item["key"]: item for group in data["groups"] for item in group["items"]}
    assert flat["TRANSFER_MODE"]["editable"] is True
    assert flat["TRANSFER_MODE"]["type"] == "choice"
    assert flat["DATA_DIR"]["editable"] is False
    # 敏感项既脱敏也不回传原始值
    assert flat["SECRET_KEY"]["secret"] is True
    assert flat["SECRET_KEY"]["raw"] is None


def test_settings_api_update_and_reset(client, auth_headers):
    response = client.put(
        "/api/v1/system/settings",
        json={"values": {"SEARCH_MAX_RESULTS": 111}},
        headers=auth_headers,
    )
    assert response.status_code == 200, response.text
    assert settings.SEARCH_MAX_RESULTS == 111

    bad = client.put(
        "/api/v1/system/settings",
        json={"values": {"SEARCH_MAX_RESULTS": 5}},
        headers=auth_headers,
    )
    assert bad.status_code == 400
    assert settings.SEARCH_MAX_RESULTS == 111   # 被拒绝的请求不改任何东西

    reset = client.post(
        "/api/v1/system/settings/reset",
        json={"keys": ["SEARCH_MAX_RESULTS"]},
        headers=auth_headers,
    )
    assert reset.status_code == 200
    assert reset.json()["keys"] == ["SEARCH_MAX_RESULTS"]


def test_settings_api_rejects_non_whitelisted_key(client, auth_headers):
    response = client.put(
        "/api/v1/system/settings",
        json={"values": {"SECRET_KEY": "hacked"}},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert settings.SECRET_KEY != "hacked"


def test_settings_groups_cover_every_editable_key() -> None:
    """白名单里能改的项，设置页必须有入口。

    实测踩过的静默缺口：``SEARCH_MAX_PER_SITE`` 与三个熔断项都在
    ``EDITABLE`` 里（后端接受 PUT），但 ``SETTING_GROUPS`` 从没引用它们 ——
    界面上根本找不到，用户只能去猜环境变量名。这类缺口不会抛异常、
    也不影响任何既有用例，只能靠这条覆盖断言钉住。
    """
    from app.api.routers.system import SETTING_GROUPS

    shown = {key for group in SETTING_GROUPS for key in group["keys"]}
    missing = sorted(key for key in config_store.EDITABLE if key not in shown)
    assert missing == [], f"这些项能在线改却没有设置页入口：{missing}"


def test_settings_api_returns_breaker_keys(client, auth_headers) -> None:
    """熔断三项要真的出现在接口返回里，且标记为可改。"""
    response = client.get("/api/v1/system/settings", headers=auth_headers)
    assert response.status_code == 200
    items = {
        item["key"]: item
        for group in response.json()["groups"]
        for item in group["items"]
    }
    for key in (
        "SEARCH_BREAKER_ENABLED",
        "SEARCH_BREAKER_THRESHOLD",
        "SEARCH_BREAKER_COOLDOWN_MINUTES",
    ):
        assert key in items, key
        assert items[key]["editable"] is True
