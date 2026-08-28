"""数据库初始化与默认数据。"""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.core.config import settings
from app.core.logger import get_logger
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import FilterRuleGroup, SiteConfig, User
from app.db.session import engine, session_scope
from app.schemas.enums import ProviderKind, UserRole

logger = get_logger(__name__)

#: 开箱可用的示例站点（默认关闭，填好地址后启用即可）
DEFAULT_SITES = [
    {
        "name": "Jackett 聚合",
        "kind": ProviderKind.INDEXER.value,
        "provider": "torznab",
        "url": "http://127.0.0.1:9117/api/v2.0/indexers/all/results/torznab",
        "enabled": False,
        "priority": 10,
        "options": {"note": "填入 Jackett/Prowlarr 的 Torznab 地址与 API Key"},
    },
    {
        "name": "Nyaa 动漫",
        "kind": ProviderKind.INDEXER.value,
        "provider": "nyaa",
        "url": "https://nyaa.si",
        "enabled": False,
        "priority": 30,
        "options": {"category": "1_2"},
    },
    {
        "name": "Mukaku 影视站",
        "kind": ProviderKind.INDEXER.value,
        "provider": "mukaku",
        "url": "https://web5.mukaku.com",
        "enabled": False,
        "priority": 20,
        "options": {
            "note": "内置字段映射，启用后即可搜索（磁力+网盘），支持最新流追新；"
                    "搜索请使用中文片名"
        },
    },
    {
        "name": "自定义 JSON API 站点（示例）",
        "kind": ProviderKind.INDEXER.value,
        "provider": "api_generic",
        "url": "https://example.com",
        "enabled": False,
        "priority": 40,
        "options": {
            "note": "参考 README「自定义站点接入」填写 api_base/search_path/字段映射",
            "api_base": "https://example.com/api/v1",
            "search_path": "search",
            "query_key": "keyword",
            "list_path": "data.list",
            "item_map": {"title": "name", "link": "magnet", "size": "size"},
        },
    },
    {
        "name": "自定义网页站点（示例）",
        "kind": ProviderKind.INDEXER.value,
        "provider": "html_generic",
        "url": "https://example.com",
        "enabled": False,
        "priority": 45,
        "options": {
            "note": "用正则描述行与字段；只需磁力可开 magnet_only",
            "search_url": "https://example.com/search?q={keyword}",
            "magnet_only": True,
        },
    },
    {
        "name": "PanSou 盘搜",
        "kind": ProviderKind.PAN.value,
        "provider": "pansou",
        "url": "http://127.0.0.1:8888",
        "enabled": False,
        "priority": 20,
        "options": {"note": "部署 pansou 服务后填写其地址"},
    },
    {
        "name": "qBittorrent",
        "kind": ProviderKind.DOWNLOADER.value,
        "provider": "qbittorrent",
        "url": "http://127.0.0.1:8080",
        "username": "admin",
        "enabled": False,
        "priority": 10,
        "options": {"category": "CineFlow", "tags": "CineFlow"},
    },
    # ---- 网盘存储（转存/浏览，区别于上面的「盘搜」搜索器）----
    {
        "name": "AList 网盘（推荐）",
        "kind": ProviderKind.PANSTORAGE.value,
        "provider": "alist",
        "url": "http://127.0.0.1:5244",
        "username": "admin",
        "enabled": False,
        "priority": 10,
        "options": {
            "note": "AList 一套接入 20+ 网盘，填地址与账号（或 api_key）即可；"
                    "转存走离线下载接口",
            "root_path": "/",
            "offline_tool": "SimpleHttp",
        },
    },
    {
        "name": "夸克网盘",
        "kind": ProviderKind.PANSTORAGE.value,
        "provider": "quark",
        "url": "https://drive-pc.quark.cn",
        "enabled": False,
        "priority": 20,
        "options": {
            "note": "在 cookie 字段填浏览器里的完整 Cookie；支持分享链接直接转存",
            "cookie": "",
            "root_path": "/",
        },
    },
    {
        "name": "WebDAV 网盘",
        "kind": ProviderKind.PANSTORAGE.value,
        "provider": "webdav",
        "url": "http://127.0.0.1:5005/dav",
        "username": "",
        "enabled": False,
        "priority": 30,
        "options": {
            "note": "一份实现覆盖 Nextcloud/坚果云/群晖/TeraCLOUD/AList 的 DAV 端点；"
                    "填 URL + 账号密码即可浏览并生成 STRM",
            "root_path": "/",
        },
    },
    {
        "name": "本地/挂载目录",
        "kind": ProviderKind.PANSTORAGE.value,
        "provider": "local_dir",
        "url": "file:///",
        "enabled": False,
        "priority": 90,
        "options": {
            "note": "把 rclone/CloudDrive 挂载出来的目录当网盘浏览，无需联网即可试用",
            "root_path": "/",
        },
    },
    {
        "name": "Emby",
        "kind": ProviderKind.MEDIASERVER.value,
        "provider": "emby",
        "url": "http://127.0.0.1:8096",
        "enabled": False,
        "priority": 50,
    },
]


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
        "已创建默认管理员 %s，请尽快修改密码（默认口令：%s）",
        settings.SUPERUSER,
        settings.SUPERUSER_PASSWORD,
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


def init_db() -> None:
    """初始化数据库。"""
    create_tables()
    migrate_columns()
    create_superuser()
    sync_user_roles()
    create_default_sites()
    create_default_rule_groups()
