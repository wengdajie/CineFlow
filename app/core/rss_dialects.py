"""RSS 方言解析：把各站点的 RSS 差异收敛成统一条目。

**为什么需要单独一层**：``feedparser`` 只保证 RSS 的**骨架**（title/link/
pubDate）一致，各站点真正有用的字段却塞在自己的命名空间里，谁也不一样。
本轮实测三种主流番剧/BT 源，差异大到不做适配就会静默丢字段：

| 站点 | 下载链接在哪 | 大小在哪 | 做种数在哪 |
| --- | --- | --- | --- |
| Mikan | ``<enclosure url>``（.torrent） | ``enclosure/@length`` + ``torrent:contentLength`` | 没有 |
| Nyaa | ``<link>``（.torrent，**不是** enclosure） | ``nyaa:size``（"1.4 GiB" 文本） | ``nyaa:seeders`` |
| dmhy | ``<enclosure url>``（**magnet**） | ``enclosure/@length`` 或 description 文本 | 没有 |

🔴 **这里藏着一个真缺陷**（本轮实测发现）：Nyaa 的 ``<enclosure>`` 是**空的**，
而 :class:`~app.providers.indexer.rss.RssIndexer` 只从 enclosure 取 size/seeders。
于是所有 Nyaa 结果的 ``size=0``、``seeders=0``：

* 用户一旦设了 ``MIN_SEEDERS>0``，Nyaa 的结果会被**整站过滤光**，
  而界面上只显示"0 条"，看不出是被自己的过滤规则砍掉的；
* 评分里的体积项恒为 0，Nyaa 资源永远排在最后。

这类"不报错、只是结果变少/变差"的问题最难发现，所以本模块的字段提取
必须逐站点写死并有回归用例兜住。

设计取舍：

* **只做「读」，不做网络**。方言判定与字段提取都是纯函数，便于穷举测试。
* **判定方言看 feed 自述而不是看用户填的 URL**。用户可能填了反向代理/镜像域名
  （Mikan 的镜像站极多），按域名判定会全部退化成 generic。
* **认不出就走 generic 且照样能用**。任何 RSS 都必须能出结果，
  方言只是"锦上添花地多拿几个字段"，不能变成"不认识就不给结果"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.utils.strings import parse_datetime, parse_size

#: 已知方言标识。``generic`` 是兜底，任何 RSS 都能用。
DIALECTS = ("mikan", "nyaa", "dmhy", "acgnx", "generic")

#: 各方言的字段差异说明。放在这里而不是前端硬编码：这些差异是**解析行为的
#: 事实描述**，界面上要提示用户"这个站拿不到做种数"，只能由解析层说。
DIALECT_NOTES: dict[str, str] = {
    "mikan": (
        "蜜柑计划：种子在 <enclosure> 里，<link> 是详情页；"
        "体积取 enclosure/@length，不提供做种数。"
        "推荐用「我的番组」聚合地址（RSS/MyBangumi?token=...）"
    ),
    "nyaa": (
        "Nyaa / Sukebei：种子地址就是 <link>（enclosure 是空的），"
        "体积/做种数在 nyaa: 命名空间里（nyaa:size / nyaa:seeders）"
    ),
    "dmhy": (
        "动漫花园：<enclosure> 给的是磁力链，体积多数只写在正文里"
        "（如 <strong>Size</strong>: 456.7MB），不提供做种数"
    ),
    "acgnx": "末日动漫 / acg.rip：结构接近标准 RSS，体积取 enclosure/@length",
    "generic": (
        "通用解析：认不出站点时的兜底，只用标准 RSS 字段"
        "（title / link / enclosure / pubDate），能出结果但字段可能不全"
    ),
}

#: feed 标题/链接里出现这些词就判定为对应方言。
#: 顺序有意义：先匹配到的赢（mikan 的镜像站标题里也带 "Mikan"）。
_DIALECT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mikan", ("mikan", "蜜柑")),
    ("nyaa", ("nyaa", "sukebei")),
    ("dmhy", ("dmhy", "動漫花園", "动漫花园")),
    ("acgnx", ("acgnx", "acg.rip", "share.acgnx")),
)

#: 从 description/summary 里抠体积，如 ``<strong>Size</strong>: 456.7MB``
#: 或 ``[563.9MB]``。dmhy / 部分聚合源只在正文里写大小。
_SIZE_IN_TEXT = re.compile(
    r"(?:size|大小|體積|体积)\s*[:：]?\s*([\d.]+\s*[KMGT]i?B)", re.IGNORECASE
)
#: description 里几乎总是 HTML 片段（dmhy 写成 ``<strong>Size</strong>: 456.7MB``），
#: 标签夹在标签词与冒号之间，不先剥标签的话上面那条正则永远匹配不上
#: —— 这是本轮自己写错又被实测抓到的一处（症状是 dmhy 全站 size=0）。
_HTML_TAG = re.compile(r"<[^>]{1,80}>")
_SIZE_IN_BRACKET = re.compile(r"[\[【(]\s*([\d.]+\s*[KMGT]i?B)\s*[\]】)]", re.IGNORECASE)

#: 磁力/种子链接判定
_MAGNET_PREFIX = "magnet:"


@dataclass
class RssEntry:
    """一条 RSS 条目（已抹平站点差异）。"""

    title: str
    link: str
    #: 详情页（Mikan 的 ``link`` 就是详情页，种子在 enclosure 里）
    homepage: str | None = None
    size: int = 0
    seeders: int = 0
    leechers: int = 0
    grabs: int = 0
    publish_at: datetime | None = None
    #: 站内唯一标识（guid / infohash），做增量去重用
    guid: str = ""
    description: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_magnet(self) -> bool:
        return self.link.lower().startswith(_MAGNET_PREFIX)


def detect_dialect(feed_title: str = "", feed_link: str = "", url: str = "") -> str:
    """判断这条 RSS 属于哪个站点方言。

    判定依据优先用 **feed 自述**（``<channel><title>`` / ``<link>``），
    其次才看用户填的 URL —— 因为 Mikan/Nyaa 的镜像与反代域名极多，
    只看 URL 会让镜像站全部退化成 generic（然后静默丢掉 size/seeders）。
    """
    haystack = " ".join(
        str(part or "").lower() for part in (feed_title, feed_link, url)
    )
    for dialect, hints in _DIALECT_HINTS:
        if any(hint.lower() in haystack for hint in hints):
            return dialect
    return "generic"


def _size_from_text(*texts: str | None) -> int:
    """从正文里抠体积（dmhy 一类只在 description 写大小）。"""
    for text in texts:
        if not text:
            continue
        plain = _HTML_TAG.sub(" ", str(text))
        for pattern in (_SIZE_IN_TEXT, _SIZE_IN_BRACKET):
            match = pattern.search(plain)
            if match:
                size = parse_size(match.group(1))
                if size:
                    return size
    return 0


def _int_of(value: Any) -> int:
    """把 ``"120"`` / ``120`` / ``None`` / ``"N/A"`` 统一成 int。"""
    try:
        return max(int(str(value).strip()), 0)
    except (TypeError, ValueError):
        return 0


def _pick_enclosure(entry: Any) -> tuple[str, int]:
    """从 enclosures 里取下载链接与体积。

    刻意**优先磁力**：同一条目同时给出 .torrent 和 magnet 时（部分聚合源如此），
    磁力不需要再请求一次站点就能投给下载器，对需要登录的站尤其重要。
    """
    best_link, best_size = "", 0
    for enclosure in getattr(entry, "enclosures", None) or []:
        if not isinstance(enclosure, dict):
            continue
        href = str(enclosure.get("href") or enclosure.get("url") or "").strip()
        if not href:
            continue
        size = parse_size(enclosure.get("length") or 0)
        if href.lower().startswith(_MAGNET_PREFIX):
            return href, size or best_size
        if not best_link:
            best_link, best_size = href, size
    return best_link, best_size


def parse_entry(entry: Any, dialect: str = "generic") -> RssEntry | None:
    """把一个 feedparser 条目转成 :class:`RssEntry`，无标题/无链接则返回 ``None``。"""
    title = str(getattr(entry, "title", "") or "").strip()
    if not title:
        return None

    page = str(getattr(entry, "link", "") or "").strip()
    enclosure_link, enclosure_size = _pick_enclosure(entry)

    link = enclosure_link or page
    if not link:
        return None

    # ---- 体积：各站点位置完全不同，逐个来源兜
    size = enclosure_size
    if not size:
        # Mikan：torrent:contentLength（命名空间前缀被 feedparser 拍平成下划线）
        size = parse_size(getattr(entry, "torrent_contentlength", 0) or 0)
    if not size and dialect == "nyaa":
        # 🔴 Nyaa 的 enclosure 是空的，体积只在 nyaa:size 里，且是 "1.4 GiB" 文本
        size = parse_size(getattr(entry, "nyaa_size", 0) or 0)
    if not size:
        size = parse_size(getattr(entry, "size", 0) or 0)
    if not size:
        size = _size_from_text(
            getattr(entry, "summary", None), getattr(entry, "description", None)
        )

    # ---- 做种数：只有 Nyaa 系给
    seeders = _int_of(getattr(entry, "nyaa_seeders", 0))
    leechers = _int_of(getattr(entry, "nyaa_leechers", 0))
    grabs = _int_of(getattr(entry, "nyaa_downloads", 0))
    if not seeders:
        seeders = _int_of(getattr(entry, "seeders", 0))
    if not leechers:
        leechers = _int_of(getattr(entry, "leechers", 0))

    published = (
        getattr(entry, "published", None)
        or getattr(entry, "updated", None)
        or getattr(entry, "torrent_pubdate", None)
    )
    publish_at = parse_datetime(published)
    if publish_at is None and getattr(entry, "published_parsed", None):
        # feedparser 已经解析成 struct_time 的情况（RFC822 带时区）
        try:
            publish_at = datetime(*entry.published_parsed[:6])
        except (TypeError, ValueError):  # pragma: no cover - 结构异常时留空
            publish_at = None

    # ---- 唯一标识：优先 guid/infohash，退回链接
    guid = str(
        getattr(entry, "id", "")
        or getattr(entry, "guid", "")
        or getattr(entry, "nyaa_infohash", "")
        or link
    ).strip()

    extra: dict[str, Any] = {"rss_dialect": dialect}
    category = str(getattr(entry, "nyaa_category", "") or "").strip()
    if category:
        extra["category"] = category
    infohash = str(getattr(entry, "nyaa_infohash", "") or "").strip()
    if infohash:
        extra["infohash"] = infohash

    return RssEntry(
        title=title,
        link=link,
        # Mikan 的 link 是详情页而下载在 enclosure，此时 page 才是 homepage；
        # Nyaa 的 link 本身就是种子文件，homepage 留空免得点开是下载
        homepage=page if enclosure_link and page != enclosure_link else None,
        size=size,
        seeders=seeders,
        leechers=leechers,
        grabs=grabs,
        publish_at=publish_at,
        guid=guid,
        description=str(getattr(entry, "summary", "") or "")[:500] or None,
        extra=extra,
    )


def parse_feed(text: str, *, url: str = "") -> tuple[str, str, list[RssEntry]]:
    """解析一整篇 RSS。

    Returns:
        ``(feed_title, dialect, entries)``。解析失败返回 ``("", "generic", [])``
        而不是抛异常 —— 一条坏 RSS 不该让整轮巡检/搜索崩掉。
    """
    if not str(text or "").strip():
        return "", "generic", []
    try:
        import feedparser

        parsed = feedparser.parse(text)
    except Exception:  # pragma: no cover - feedparser 极少抛，兜住免得整轮挂掉
        return "", "generic", []

    feed = getattr(parsed, "feed", None)
    feed_title = str((feed or {}).get("title") or "").strip() if feed else ""
    feed_link = str((feed or {}).get("link") or "").strip() if feed else ""
    dialect = detect_dialect(feed_title, feed_link, url)

    entries: list[RssEntry] = []
    for raw in getattr(parsed, "entries", None) or []:
        item = parse_entry(raw, dialect)
        if item is not None:
            entries.append(item)
    return feed_title, dialect, entries
