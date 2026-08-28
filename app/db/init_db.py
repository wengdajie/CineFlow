"""数据库初始化与默认数据。"""

from __future__ import annotations

from app.core.config import settings
from app.core.logger import get_logger
from app.core.security import hash_password
from app.db.base import Base
from app.db.models import SiteConfig, User
from app.db.session import engine, session_scope
from app.schemas.enums import ProviderKind

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


def create_tables() -> None:
    """建表。"""
    Base.metadata.create_all(bind=engine)
    logger.info("数据库表已就绪：%s", settings.DB_URL)


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
    create_superuser()
    create_default_sites()
