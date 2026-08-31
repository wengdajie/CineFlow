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
        # 实测：/api/resource?keyword= 搜剧集，再按 id 取详情，含电驴/磁力/网盘
        "name": "人人影视 YYeTs",
        "kind": ProviderKind.INDEXER.value,
        "provider": "yyets",
        "url": "https://yyets.click",
        "enabled": False,
        "priority": 25,
        "options": {
            "note": "内置解析，启用即可用；站点常换域名，失效时改上面的地址即可",
            "detail_limit": 5,
        },
    },
    {
        # 实测：WordPress 搜索 RSS + 详情页抓磁力（单页可达上百条）
        "name": "BD电影首发站",
        "kind": ProviderKind.INDEXER.value,
        "provider": "wp_film",
        "url": "https://www.bdflixs.com",
        "enabled": False,
        "priority": 30,
        "options": {
            "note": "WordPress 站：RSS 搜索 + 详情页抓磁力，已实测可用",
            "article_limit": 5,
            "per_article_limit": 12,
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
        # 同为 MacCMS 结构；实测被 SafeLine WAF 拦在门外（HTTP 468），
        # 换 UA / Accept-Language 均无效，故仅作预设：用户自备代理时可用。
        # 不对抗 WAF（与 ADR-24 同口径：不做风控对抗）。
        "name": "CZ4K（在线）",
        "kind": ProviderKind.INDEXER.value,
        "provider": "maccms",
        "url": "https://www.cz4k.com",
        "enabled": False,
        "priority": 65,
        "options": {
            "note": "实测被 SafeLine WAF 拦截（HTTP 468），直连不可用；"
                    "需自备代理。本项目不做 WAF 对抗，故仅保留预设",
            "max_items": 6,
        },
    },
    {
        # 与 bdflixs 同为 WordPress 结构；实测首页 200 但疑似限流，故仅作预设
        "name": "MJF 美剧站",
        "kind": ProviderKind.INDEXER.value,
        "provider": "wp_film",
        "url": "https://www.mjf2020.com",
        "enabled": False,
        "priority": 35,
        "options": {
            "note": "实测存在限流/SSL 握手超时，建议配代理或降低搜索频率；"
                    "搜索路径为 /ss/?s={keyword}",
            "search_url": "https://www.mjf2020.com/ss/?s={keyword}",
            "article_limit": 3,
        },
    },
    {
        # 电影天堂系：GB2312 编码，搜索走 POST，故用 html_generic + encoding
        "name": "电影天堂 DyGod",
        "kind": ProviderKind.INDEXER.value,
        "provider": "wp_film",
        "url": "https://www.dygod.vip",
        "enabled": False,
        "priority": 40,
        "options": {
            "note": "GB2312 老站：搜索接口为 POST /e/search/index.php（需 gb2312 编码），"
                    "本预设先用 RSS/栏目页兜底；如搜不到请改用「自定义网页站点」配正则",
            "encoding": "gb2312",
            "latest_url": "https://www.dygod.vip/html/gndy/dyzz/index.html",
            "article_limit": 3,
        },
    },
    {
        # 聚合BD：真实搜索路径 /q/?k=（从 /js/front.js 里挖出）
        "name": "聚合BD",
        "kind": ProviderKind.INDEXER.value,
        "provider": "html_generic",
        "url": "https://www.juhebd.com",
        "enabled": False,
        "priority": 45,
        "options": {
            "note": "搜索页可解析条目，但下载链接需关注公众号才显示；"
                    "适合当「有没有这部片」的探针，实际下载请配合其它站点",
            "search_url": "https://www.juhebd.com/q/?k={keyword}",
            "row_pattern": "<a[^>]+href=\"(/(?:mv|tv|acg)/[^\"]+)\"[^>]*title=\"([^\"]+)\"",
            "field_patterns": {
                "page_url": "href=\"(/(?:mv|tv|acg)/[^\"]+)\"",
                "title": "title=\"([^\"]+)\"",
            },
            "max_rows": 40,
        },
    },
    {
        # 蓝光影视：POST /api.php fun=get_video 返回豆瓣级元数据（无直链）
        "name": "蓝光影视 LDYSG",
        "kind": ProviderKind.INDEXER.value,
        "provider": "api_generic",
        "url": "https://www.ldysg.top",
        "enabled": False,
        "priority": 50,
        "options": {
            "note": "注意域名是 .top（.win 已 404）。接口返回单条影片元数据"
                    "（封面/年份/导演），主要用于补元数据而非直链下载",
            "api_base": "https://www.ldysg.top",
            "search_path": "api.php",
            "method": "POST",
            "query_key": "title",
            "fixed_params": {"fun": "get_video"},
            "list_path": "data",
            "item_map": {"title": "title", "year": "year", "poster": "pic"},
        },
    },
    {
        # hdzu 有自定义反爬（非 Cloudflare），必须用户提供登录后 Cookie
        "name": "HDZU 高清组",
        "kind": ProviderKind.INDEXER.value,
        "provider": "html_generic",
        "url": "https://hdzu.org",
        "enabled": False,
        "priority": 55,
        "options": {
            "note": "站点有自定义反爬，直连返回 403。必须在下方 cookie 字段填入"
                    "浏览器登录后的完整 Cookie 才能使用",
            "search_url": "https://hdzu.org/search?q={keyword}",
            "magnet_only": True,
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
