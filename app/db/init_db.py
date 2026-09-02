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
        # MacCMS 在线影视站。实测首页 30 部片 / 53 个播放源的分布：
        # qq 21 / qiyi 12 / youku 10 / mgtv 6 / bilibili 3 / rrmj 1
        # → 约 92% 是长视频平台会员正片，按 ADR-24 会在下载入口如实拒绕。
        # 预设仍然提供：它能当「这部片在哪个平台有」的索引，
        # 其中 B 站/UP 主自制/官方免费片那部分是真能下的。
        "name": "西瓜影院（在线）",
        "kind": ProviderKind.INDEXER.value,
        "provider": "maccms",
        "url": "https://www.bzzdyy.com",
        "enabled": False,
        "priority": 60,
        "options": {
            "note": "MacCMS 在线站：产出播放源指向的【平台原始地址】，交给 yt-dlp。"
                    "实测约 92% 是腾讯/爱奇艺/优酷/苒果的会员正片，"
                    "这部分会被如实拒绕（不接入任何 VIP 解析网关）；"
                    "能下的主要是 B 站与官方免费内容",
            "max_items": 6,
        },
    },
    {
        # B 站搜索无需任何配置（内部会先摸首页拿 buvid Cookie 破 412），
        # 但产出的是"视频网页"而非影视正片，故默认禁用，按需开启
        "name": "Bilibili 视频搜索",
        "kind": ProviderKind.INDEXER.value,
        "provider": "bilibili",
        "url": "https://www.bilibili.com",
        "enabled": False,
        "priority": 70,
        "options": {
            "note": "搜索 B 站公开视频，下载交由 yt-dlp；仅公开内容，不解析大会员正片。"
                    "填入自己的 Cookie 可提升配额稳定性（非必需）",
            "limit": 20,
        },
    },
    {
        # YouTube 搜索复用 yt-dlp 的 ytsearch，国内网络通常需要代理
        "name": "YouTube 视频搜索",
        "kind": ProviderKind.INDEXER.value,
        "provider": "youtube",
        "url": "https://www.youtube.com",
        "enabled": False,
        "priority": 75,
        "options": {
            "note": "复用 yt-dlp 的 ytsearch；国内网络请在 proxy 里填代理地址，"
                    "如 http://127.0.0.1:7890",
            "limit": 20,
            "proxy": "",
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
        # v1.14.0：由 awesome-zhuiju-free 清单实测筛出的两个「搜索即能拿到
        # 可用网盘链接」的站（20 个候选里只有这 4 个过关，另两个是我们已覆盖的
        # acg.rip/nyaa 与 btsj6）。两站同模板，共用 kkso Provider。
        # 默认启用：无需填地址账号，且实测稳定（庆余年 5/10 条、凡人 10/10 条）。
        "name": "KK 网盘搜",
        "kind": ProviderKind.PAN.value,
        "provider": "kkso",
        "url": "https://kkso.net",
        "enabled": True,
        "priority": 25,
        "options": {"note": "开箱可用的网盘搜索（夸克/百度/迅雷）。"
                            "来自 awesome-zhuiju-free 清单，经本地搜索探测实测可用"},
    },
    {
        "name": "追剧 zhuiju.us",
        "kind": ProviderKind.PAN.value,
        "provider": "kkso",
        "url": "https://www.zhuiju.us",
        "enabled": True,
        "priority": 26,
        "options": {"note": "与 KK 网盘搜同模板，资源以百度/迅雷为主且更新更勤"
                            "（实测提取码拼在链接 ?pwd= 上，已自动提取）"},
    },
    {
        # 实测 ?s= 搜索页直出 20 条磁力且标题含季集信息；/s/ 形态是 404
        "name": "BT 世界网",
        "kind": ProviderKind.INDEXER.value,
        "provider": "html_generic",
        "url": "https://www.btsj6.com",
        "enabled": False,
        "priority": 48,
        "options": {
            "note": "来自 awesome-zhuiju-free 清单，实测「凡人修仙传」直出 20 条磁力；"
                    "注意搜索路径是 ?s=（/s/ 会 404）。热门剧命中好，冷门片可能 0 条",
            "search_url": "https://www.btsj6.com/?s={keyword}",
            "magnet_only": True,
            "max_rows": 60,
        },
    },
    {
        # v1.16.0：awesome-zhuiju-free 清单里 `reachable_only` 的 12 个站，
        # 上游只探首页状态码，因此全部标成"仅可达"。逐站跟到**详情页**实测后，
        # 只有这两个真能产出可用链接（其余是搜索页有片名、详情页零链接，
        # 或链接被公众号/登录墙挡住 —— 详见 ADR-74）。
        #
        # 两站的共同点：搜索页**只有详情页地址**，磁力在详情页里。
        # 所以必须用 detail_link_field 做二段抓取；只配 magnet_only 会得 0 条
        # （这正是最初写错的地方，实测 raw=0 才发现）。
        "name": "磁力熊 Cilixiong",
        "kind": ProviderKind.INDEXER.value,
        "provider": "html_generic",
        "url": "https://www.cilixiong.org",
        "enabled": True,
        "priority": 46,
        "options": {
            "note": "来自 awesome-zhuiju-free。⚠️ 搜索必须走 POST（EmpireCMS）："
                    "GET ?s= 会静默返回首页，于是任何关键词都返回同一批结果、"
                    "连乱码关键词都有 20 条（这个坑实测才发现）。"
                    "磁力在详情页，用 detail_link_field 二段抓取；"
                    "实测「流浪地球」命中 3 部，逐部拿到 4~8 条真实磁力",
            "search_url": "https://www.cilixiong.org/e/search/index.php",
            "search_method": "POST",
            "search_data": {
                "keyboard": "{keyword}",
                "classid": "1,2",
                "show": "title",
                "tempid": "1",
            },
            "detail_link_field": "href=\"(/(?:movie|tv)/\\d+\\.html)\"",
            "max_detail_items": 6,
            # 站内 POST 搜索已按片名匹配，标题取自详情页真实片名；
            # 再按关键词过一遍会把「流浪地球2」这类合法结果误杀
            "local_filter": False,
        },
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
    {
        # yt-dlp 是本地库调用，不需要地址与账号，装了依赖就能用，故默认启用
        "name": "yt-dlp 视频下载",
        "kind": ProviderKind.DOWNLOADER.value,
        "provider": "ytdlp",
        "url": "",
        "enabled": True,
        "priority": 60,
        "options": {
            "note": "抓取公开视频页面（B 站/YouTube/抖音/TikTok 等 1700+ 站点）；"
                    "仅支持公开内容，不解析会员/付费正片",
            "max_height": 1080,
            "write_subtitles": True,
            "write_thumbnail": True,
            "rate_limit": 0,
        },
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
        "name": "115 网盘",
        "kind": ProviderKind.PANSTORAGE.value,
        "provider": "pan115",
        "url": "https://115.com",
        "enabled": False,
        "priority": 21,
        "options": {
            "note": "在「网盘管理 → 登录网盘」扫码即可自动填 Cookie（115 官方扫码接口，"
                    "本项目支持得最好的网盘）；也可手工粘贴 Cookie",
            "cookie": "",
            "root_path": "/",
        },
    },
    {
        "name": "百度网盘",
        "kind": ProviderKind.PANSTORAGE.value,
        "provider": "baidu",
        "url": "https://pan.baidu.com",
        "enabled": False,
        "priority": 22,
        "options": {
            "note": "在「网盘管理 → 登录网盘」扫码登录（passport 通道）；"
                    "百度风控较严，扫码换不到 Cookie 时改用 Cookie 导入",
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
