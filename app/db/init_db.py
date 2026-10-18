"""数据库初始化与默认数据。"""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.core.config import settings
from app.core.logger import get_logger
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import FilterRuleGroup, SiteConfig, User
from app.db.session import engine, session_scope
from app.schemas.enums import UserRole
from app.services.site_catalog import DEFAULT_SITES

logger = get_logger(__name__)

#: 开箱可用的示例站点（默认关闭，填好地址后启用即可）


#: 内置过滤规则组模板（有序偏好；默认只启用「均衡」那一组）
#: 内置过滤规则组模板。
#: **刻意全部不设为默认组**：规则组会改变搜索结果的排序，
#: 升级后悄悄换掉用户熟悉的排序是很糟糕的体验——让用户自己去「规则组」页点一下设为默认。
DEFAULT_RULE_GROUPS = [
    {
        "name": "画质优先（4K 优先）",
        "description": "先要 4K REMUX/BluRay，其次 4K WEB-DL，再退 1080p；适合大容量存储",
        "levels": [
            {"name": "4K REMUX", "resolution": "2160p", "quality": "REMUX|BluRay"},
            {"name": "4K WEB-DL", "resolution": "2160p", "quality": "WEB-DL|WEBRip"},
            {"name": "1080p 高码", "resolution": "1080p", "quality": "REMUX|BluRay"},
            {"name": "1080p", "resolution": "1080p"},
        ],
        "accept_unmatched": False,
        "is_default": False,
        "enabled": True,
    },
    {
        "name": "均衡（1080p 中字优先）",
        "description": "优先 1080p 且带中文字幕，再退 4K，最后接受其它；日常追剧推荐，"
                       "想全局启用就把它设为默认组",
        "levels": [
            {"name": "1080p 中字", "resolution": "1080p", "include": "中字|简繁|简体|繁体|中文"},
            {"name": "1080p", "resolution": "1080p"},
            {"name": "2160p", "resolution": "2160p"},
            {"name": "720p 兜底", "resolution": "720p"},
        ],
        "accept_unmatched": True,
        "is_default": False,
        "enabled": True,
    },
    {
        "name": "省空间（1080p 以下）",
        "description": "只要 1080p/720p 且排除 REMUX 等大体积版本；适合小容量 NAS",
        "levels": [
            {"name": "1080p WEB", "resolution": "1080p", "quality": "WEB-DL|WEBRip|HDTV"},
            {"name": "720p", "resolution": "720p"},
        ],
        "accept_unmatched": False,
        "is_default": False,
        "enabled": True,
    },
    {
        "name": "动漫（简繁内封优先）",
        "description": "优先内封简繁字幕的 1080p 番剧资源",
        "levels": [
            {"name": "1080p 内封简繁", "resolution": "1080p", "include": "简繁|内封|简日|繁日"},
            {"name": "1080p", "resolution": "1080p"},
            {"name": "其它", "resolution": ""},
        ],
        "accept_unmatched": True,
        "is_default": False,
        "enabled": True,
    },
]


def create_default_rule_groups() -> None:
    """写入内置过滤规则组（按名字补齐，不覆盖用户改动）。

    与示例站点同样的策略（ADR-06）：升级时补新增的模板，
    已存在的同名规则组保持用户自己的调整。
    """
    with session_scope() as session:
        existing = {name for (name,) in session.query(FilterRuleGroup.name).all()}
        added = 0
        for item in DEFAULT_RULE_GROUPS:
            if item["name"] in existing:
                continue
            session.add(FilterRuleGroup(**item))
            added += 1
    if added:
        logger.info("已写入 %d 个内置过滤规则组（共 %d 个模板）", added, len(DEFAULT_RULE_GROUPS))


def create_tables() -> None:
    """建表。"""
    Base.metadata.create_all(bind=engine)
    logger.info("数据库表已就绪：%s", settings.DB_URL)


#: 版本升级时给已有表补的列：``表名 -> [(列名, SQL 类型与默认值)]``
#: 为什么需要：``create_all`` 只建**缺失的表**，不会给已存在的表加列。
#: 老用户直接升级后，新代码 SELECT 新列会直接 500（v1.4.0 的 quality_score 就是这样）。
#: 用 ALTER TABLE ADD COLUMN 补齐即可——SQLite 对它支持良好且不重写数据。
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "strm_records": [("play_token", "VARCHAR(64)")],
    "download_tasks": [("dispatch_key", "VARCHAR(64)")],
    "library_files": [
        ("quality_score", "FLOAT DEFAULT 0"),
        ("upgrade_count", "INTEGER DEFAULT 0"),
    ],
    # v1.5.0：多用户角色 + 订阅引用规则组
    "users": [
        ("role", "VARCHAR(16) DEFAULT 'admin'"),
        ("note", "VARCHAR(255)"),
    ],
    "subscribes": [
        ("rule_group_id", "INTEGER"),
    ],
}


def migrate_columns() -> None:
    """给已存在的表补齐新版本新增的列（幂等，可反复执行）。"""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    for table, columns in _ADDED_COLUMNS.items():
        if table not in tables:
            continue  # 表本身是新建的，create_all 已经带上全部列
        present = {column["name"] for column in inspector.get_columns(table)}
        for name, ddl in columns:
            if name in present:
                continue
            try:
                with engine.begin() as connection:
                    connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                logger.info("数据库升级：%s 表补充列 %s", table, name)
            except Exception as exc:  # 补列失败不该让服务起不来
                logger.warning("为 %s 补列 %s 失败：%s", table, name, exc)

    if "download_tasks" in tables:
        with engine.begin() as connection:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_dispatch_key ON download_tasks (dispatch_key)"))


def sync_user_roles() -> None:
    """老库升级后把 ``role`` 与 ``is_superuser`` 对齐一次。

    v1.5.0 之前只有 ``is_superuser`` 布尔位。补列时默认值只能是常量，
    所以这里按布尔位回填角色，避免老库里的普通用户被当成管理员
    （补列默认 'admin' 是为了保证**唯一的老管理员**不会把自己锁在外面）。
    """
    with session_scope() as session:
        rows = session.query(User).all()
        fixed = 0
        for user in rows:
            expected = UserRole.ADMIN.value if user.is_superuser else UserRole.OPERATOR.value
            if user.role not in (UserRole.ADMIN.value, UserRole.OPERATOR.value, UserRole.VIEWER.value):
                user.role = expected
                fixed += 1
            elif user.is_superuser and user.role != UserRole.ADMIN.value:
                # is_superuser 为真但角色不是 admin：以角色为准并修正布尔位，
                # 因为角色是 v1.5.0 起的权威来源
                user.is_superuser = False
                fixed += 1
            elif not user.is_superuser and user.role == UserRole.ADMIN.value:
                user.is_superuser = True
                fixed += 1
    if fixed:
        logger.info("已对齐 %d 个用户的角色与超级用户标记", fixed)


def create_superuser() -> None:
    """创建默认管理员。"""
    with session_scope() as session:
        exists = (
            session.query(User).filter(User.username == settings.SUPERUSER).one_or_none()
        )
        if exists:
            return
        session.add(
            User(
                username=settings.SUPERUSER,
                password_hash=hash_password(settings.SUPERUSER_PASSWORD),
                is_superuser=True,
                is_active=True,
            )
        )
    logger.warning(
        "已创建默认管理员 %s，请登录后修改初始密码",
        settings.SUPERUSER,
    )


def create_default_sites() -> None:
    """写入示例站点（全部默认禁用）。

    这里按 ``name`` 逐条补齐而不是"表空才写"，因为**版本升级**会带来
    新的示例站点（例如 1.3.0 新增的网盘存储）。老库如果只判断"表非空就跳过"，
    升级后用户永远看不到新能力的示例配置。
    已存在的同名站点绝不覆盖，避免抹掉用户改过的地址与密钥。
    """
    with session_scope() as session:
        existing = {name for (name,) in session.query(SiteConfig.name).all()}
        added = 0
        for item in DEFAULT_SITES:
            if item["name"] in existing:
                continue
            session.add(SiteConfig(**item))
            added += 1
    if added:
        logger.info("已写入 %d 条示例站点配置（默认禁用，共 %d 条内置示例）", added, len(DEFAULT_SITES))


#: v1.16.0 下线的内置示例站点：``站点名 -> (历史上随版本发布过的地址, 下线原因)``
#:
#: 为什么需要这张表：``create_default_sites`` 只会**新增**同名不存在的站点，
#: 从来不删。于是老用户升级后，v1.14.0 写进库里的这些站点会**继续留在界面上**，
#: 并可能处于启用状态，表现为「搜索很慢，而且这些站永远 0 条」。
#: 判据是「详情页能否取到真链接」，不是首页状态码（详见 docs/04 ADR-75）。
_RETIRED_SITES: dict[str, tuple[tuple[str, ...], str]] = {
    "人人影视 YYeTs": (("https://yyets.click",), "搜索页不含关键词，只有站点元数据"),
    "BD电影首发站": (("https://www.bdflixs.com",), "详情页抓到的全是 css 链接"),
    "CZ4K（在线）": (("https://www.cz4k.com",), "SafeLine WAF 拦截，返回 468"),
    "MJF 美剧站": (("https://www.mjf2020.com",), "搜索页不含关键词"),
    "电影天堂 DyGod": (("https://www.dygod.vip",), "搜索页不含关键词"),
    "聚合BD": (("https://www.juhebd.com",), "84 个详情页全部零链接（公众号墙）"),
    "蓝光影视 LDYSG": (("https://www.ldysg.top",), "搜索页不含关键词"),
    "HDZU 高清组": (("https://hdzu.org",), "详情页 403"),
    "自定义 JSON API 站点（示例）": (("https://example.com",), "纯占位示例，已由站点预设模板替代"),
    "自定义网页站点（示例）": (("https://example.com",), "纯占位示例，已由站点预设模板替代"),
}

#: 已执行过的下线清理（记住站点名，避免用户手工重建后又被反复删掉）
KEY_RETIRED_SEEDS = "retired_seed_sites"


def _looks_untouched(site: SiteConfig, shipped_urls: tuple[str, ...]) -> bool:
    """判断这条站点是否还是「出厂状态」。

    只有出厂状态才允许自动删除。用户一旦改过地址（例如换成了能用的镜像域名）
    或者填过账号 / Cookie / API Key，就说明他在这条记录上投入过配置，
    这时删掉属于**擅自丢弃用户数据**，只能保留并在日志里提示。
    """
    shipped = tuple((item or "").rstrip("/") for item in shipped_urls)
    if (site.url or "").rstrip("/") not in shipped:
        return False
    return not any((site.api_key, site.cookie, site.username, site.password))


def retire_removed_sites() -> None:
    """清理已下线的内置示例站点（一次性，幂等）。

    ``create_default_sites`` 只增不删，所以「下线」必须单独做一次迁移，
    否则只有全新安装的用户能得到干净的站点清单，升级用户会一直留着一堆
    「启用了也永远搜不到东西」的站 —— 这正是 v1.16.0 清单瘦身没覆盖到的场景。

    只删**没被用户碰过**的记录，并把已处理过的站点名记进 ``settings`` 表，
    这样用户日后自己手工重建同名站点时不会被反复删除。
    """
    from app.services import settings_store

    done = settings_store.get_setting(KEY_RETIRED_SEEDS, []) or []
    if not isinstance(done, list):
        done = []
    pending = {name: spec for name, spec in _RETIRED_SITES.items() if name not in done}
    if not pending:
        return

    removed: list[str] = []
    kept: list[str] = []
    with session_scope() as session:
        for name, (shipped_urls, reason) in pending.items():
            site = (
                session.query(SiteConfig).filter(SiteConfig.name == name).one_or_none()
            )
            if site is None:
                continue
            if _looks_untouched(site, shipped_urls):
                session.delete(site)
                removed.append(f"{name}（{reason}）")
            else:
                kept.append(name)

    settings_store.set_setting(KEY_RETIRED_SEEDS, sorted(set(done) | set(pending)))
    if removed:
        logger.info("已清理 %d 个下线的示例站点：%s", len(removed), "；".join(removed))
    if kept:
        logger.warning(
            "以下示例站点已被下线（实测取不到可用链接），但检测到你改过配置，"
            "因此保留，请自行确认是否还需要：%s",
            "、".join(kept),
        )


def init_db() -> None:
    """初始化数据库。"""
    create_tables()
    migrate_columns()
    create_superuser()
    sync_user_roles()
    create_default_sites()
    retire_removed_sites()
    create_default_rule_groups()
