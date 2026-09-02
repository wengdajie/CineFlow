"""已下线内置站点的升级清理测试（``retire_removed_sites``）。

为什么专门为它写一组用例：``create_default_sites`` **只增不删**，
所以「站点清单瘦身」对**升级用户**是完全无效的——只有全新安装才干净。
这组用例锁死三件事：

1. 出厂状态的下线站点会被删掉（否则升级用户界面上一直挂着搜不到东西的站）
2. **用户改过的记录绝不能删**（擅自丢用户配置比留个废站严重得多）
3. 幂等：跑第二遍不再动手，用户手工重建同名站点也不会被反复删除
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.init_db import (
    _RETIRED_SITES,
    DEFAULT_SITES,
    KEY_RETIRED_SEEDS,
    retire_removed_sites,
)
from app.db.models import SiteConfig
from app.db.session import session_scope
from app.schemas.enums import ProviderKind
from app.services import settings_store


@pytest.fixture
def sandbox(client):
    """站点表 + 清理记账键的快照-还原。

    测试库是 session 级共享的：本用例会造出**已启用**的站点，留下痕迹会让
    搜索类用例拿到完全不同的结果，所以整表快照后原样还原。
    """
    def snapshot():
        with session_scope() as session:
            return [
                {
                    "name": row.name,
                    "kind": row.kind,
                    "provider": row.provider,
                    "url": row.url,
                    "enabled": row.enabled,
                    "cookie": row.cookie,
                    "api_key": row.api_key,
                    "username": row.username,
                    "password": row.password,
                    "priority": row.priority,
                    "options": dict(row.options or {}),
                }
                for row in session.execute(select(SiteConfig)).scalars()
            ]

    before = snapshot()
    before_key = settings_store.get_setting(KEY_RETIRED_SEEDS, None)
    yield
    with session_scope() as session:
        for row in session.execute(select(SiteConfig)).scalars().all():
            session.delete(row)
    with session_scope() as session:
        for item in before:
            session.add(SiteConfig(**item))
    if before_key is None:
        settings_store.delete_setting(KEY_RETIRED_SEEDS)
    else:
        settings_store.set_setting(KEY_RETIRED_SEEDS, before_key)


def _add(name: str, url: str, **kwargs) -> None:
    with session_scope() as session:
        session.add(
            SiteConfig(
                name=name,
                kind=ProviderKind.INDEXER.value,
                provider="html_generic",
                url=url,
                enabled=True,
                **kwargs,
            )
        )


def _exists(name: str) -> bool:
    with session_scope() as session:
        return (
            session.execute(
                select(SiteConfig).where(SiteConfig.name == name)
            ).scalar_one_or_none()
            is not None
        )


def test_retired_names_are_not_in_seed():
    """下线清单与内置清单不能有交集。

    这条是防「一手删一手又加回来」：如果哪天有人把站点重新写进
    ``DEFAULT_SITES``，迁移会在每次启动时删掉刚写入的记录，
    表现为「站点列表少一条却查不出原因」。
    """
    seeded = {item["name"] for item in DEFAULT_SITES}
    overlap = seeded & set(_RETIRED_SITES)
    assert not overlap, f"这些站点同时出现在内置清单和下线清单里：{overlap}"


def test_looks_untouched_normalizes_both_sides():
    """出厂地址**自己带**结尾斜杠时也要能匹配上。

    单独测 ``_looks_untouched``：现有下线清单里的地址都不带结尾斜杠，
    所以「归一化 shipped 一侧」这段防御在整体流程里跑不到，
    只走上层用例的话把它改坏也不会转红（实测确实是假绿）。
    以后往清单里补一条带斜杠的地址时，这条能兜住。
    """
    from app.db.init_db import _looks_untouched

    site = SiteConfig(
        name="X",
        kind=ProviderKind.INDEXER.value,
        provider="html_generic",
        url="https://x.example",
    )
    assert _looks_untouched(site, ("https://x.example/",))


def test_retire_removes_untouched_sites(sandbox):
    """出厂状态（地址没改、无凭据）的下线站点会被清掉。"""
    settings_store.delete_setting(KEY_RETIRED_SEEDS)
    for name, (urls, _reason) in _RETIRED_SITES.items():
        if not _exists(name):
            _add(name, urls[0])

    retire_removed_sites()

    for name in _RETIRED_SITES:
        assert not _exists(name), f"{name} 是出厂状态，应该被清理"


@pytest.mark.parametrize(
    "field, value",
    [
        ("cookie", "session=abc"),
        ("api_key", "key-123"),
        ("username", "someone"),
        ("password", "secret"),
    ],
)
def test_retire_keeps_sites_with_credentials(sandbox, field, value):
    """填过凭据的记录必须保留——那是用户的劳动成果。"""
    settings_store.delete_setting(KEY_RETIRED_SEEDS)
    name = "HDZU 高清组"
    urls, _reason = _RETIRED_SITES[name]
    with session_scope() as session:
        row = session.execute(
            select(SiteConfig).where(SiteConfig.name == name)
        ).scalar_one_or_none()
        if row is not None:
            session.delete(row)
    _add(name, urls[0], **{field: value})

    retire_removed_sites()

    assert _exists(name), f"填了 {field} 的站点被误删了"


def test_retire_keeps_site_with_changed_url(sandbox):
    """用户换成可用镜像域名的记录必须保留。"""
    settings_store.delete_setting(KEY_RETIRED_SEEDS)
    name = "聚合BD"
    with session_scope() as session:
        row = session.execute(
            select(SiteConfig).where(SiteConfig.name == name)
        ).scalar_one_or_none()
        if row is not None:
            session.delete(row)
    _add(name, "https://my-own-mirror.example.net")

    retire_removed_sites()

    assert _exists(name), "用户改过地址的站点被误删了"


def test_retire_ignores_trailing_slash(sandbox):
    """出厂地址只差一个结尾斜杠，仍算出厂状态。

    否则「看着一模一样的两条记录，一条被清理一条没有」会非常费解。
    """
    settings_store.delete_setting(KEY_RETIRED_SEEDS)
    name = "MJF 美剧站"
    urls, _reason = _RETIRED_SITES[name]
    with session_scope() as session:
        row = session.execute(
            select(SiteConfig).where(SiteConfig.name == name)
        ).scalar_one_or_none()
        if row is not None:
            session.delete(row)
    _add(name, urls[0] + "/")

    retire_removed_sites()

    assert not _exists(name), "结尾斜杠导致出厂站点没被识别"


def test_retire_does_not_delete_user_recreated_site(sandbox):
    """记账后用户手工重建同名站点，不会在下次启动时被删。"""
    settings_store.delete_setting(KEY_RETIRED_SEEDS)
    name = "电影天堂 DyGod"
    urls, _reason = _RETIRED_SITES[name]
    if not _exists(name):
        _add(name, urls[0])
    retire_removed_sites()
    assert not _exists(name)

    _add(name, urls[0])          # 用户自己又加了回来
    retire_removed_sites()       # 第二次启动

    assert _exists(name), "用户手工重建的站点被反复删除了"


def test_retire_records_bookkeeping(sandbox):
    """处理过的站点名会记进 settings 表，避免每次启动都扫一遍。"""
    settings_store.delete_setting(KEY_RETIRED_SEEDS)
    retire_removed_sites()
    done = settings_store.get_setting(KEY_RETIRED_SEEDS, [])
    assert set(done) == set(_RETIRED_SITES)


def test_init_db_runs_the_migration():
    """``init_db`` 必须真的调用它。

    只测函数本身很容易出现「逻辑正确但没人调用」的假绿：
    迁移不接进启动流程，对升级用户等于不存在。
    """
    import inspect

    from app.db import init_db as module

    source = inspect.getsource(module.init_db)
    assert "retire_removed_sites()" in source
