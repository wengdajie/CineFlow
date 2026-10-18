"""调度配置迁移及兼容设置入口的一致性。"""

import pytest

from app.services import config_store, scheduler, settings_store


@pytest.fixture(autouse=True)
def clean_schedule_settings(client):
    keys = [config_store.KEY_RUNTIME, settings_store.KEY_SCHEDULES, "legacy_schedule_config"]
    previous = {key: settings_store.get_setting(key) for key in keys}
    for key in keys:
        settings_store.delete_setting(key)
    yield
    for key, value in previous.items():
        if value is None:
            settings_store.delete_setting(key)
        else:
            settings_store.set_setting(key, value)


def test_compatibility_setting_writes_the_actual_schedule():
    scheduler.scheduler_service.update_schedule("subscribe", {"minutes": 90})
    config_store.update({"SUBSCRIBE_INTERVAL_MINUTES": 5})
    assert scheduler.effective_schedule("subscribe")["minutes"] == 5
    assert config_store.value_for("SUBSCRIBE_INTERVAL_MINUTES") == 5
    assert "SUBSCRIBE_INTERVAL_MINUTES" not in config_store.overrides()
    scheduler.scheduler_service.update_schedule("subscribe", {"minutes": 30})
    assert config_store.value_for("SUBSCRIBE_INTERVAL_MINUTES") == 30


def test_legacy_migration_preserves_executed_value_and_conflict():
    settings_store.set_setting(config_store.KEY_RUNTIME, {"SUBSCRIBE_INTERVAL_MINUTES": 5})
    settings_store.set_setting(settings_store.KEY_SCHEDULES, {"subscribe": {"minutes": 90}})
    config_store.apply_overrides()
    config_store.apply_overrides()
    assert config_store.value_for("SUBSCRIBE_INTERVAL_MINUTES") == 90
    assert settings_store.get_setting("legacy_schedule_config")["SUBSCRIBE_INTERVAL_MINUTES"] == 5
    assert "SUBSCRIBE_INTERVAL_MINUTES" not in settings_store.get_setting(config_store.KEY_RUNTIME)


def test_interval_zero_disables_and_reset_removes_override():
    config_store.update({"RSS_INTERVAL_MINUTES": 0})
    assert not scheduler.effective_schedule("rss")["enabled"]
    assert config_store.value_for("RSS_INTERVAL_MINUTES") == 0
    assert config_store.reset(["RSS_INTERVAL_MINUTES"]) == ["RSS_INTERVAL_MINUTES"]
    assert not scheduler.effective_schedule("rss")["customized"]


def test_reliability_columns_upgrade_old_database_twice(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, inspect, text

    from app.db import init_db

    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE download_tasks (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE strm_records (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(init_db, "engine", engine)
    init_db.migrate_columns()
    init_db.migrate_columns()
    assert "play_token" in {c["name"] for c in inspect(engine).get_columns("strm_records")}
    assert "dispatch_key" in {c["name"] for c in inspect(engine).get_columns("download_tasks")}
    assert any(i["unique"] for i in inspect(engine).get_indexes("download_tasks"))
    engine.dispose()
