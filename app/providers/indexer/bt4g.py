"""BT4G 公开 BT 搜索：**免部署、免鉴权、中文关键词直接可用**的磁力源。

## 为什么加这个 Provider

用户反馈「大部分内容搜不到 BT 资源，返回的几乎全是网盘」。实测 v1.25.0
在关键词「喜剧之王单口季 第三季」上的结果分布是 **42 条网盘 + 1 条网页视频 +
0 条 BT** —— 已有的 BT 源全军覆没：

| 已有 BT 源 | 实测结果 | 为什么没用 |
|---|---|---|
| `dmhy`/`nyaa`/`animetosho`/`acgrip` | `empty` | **只收动漫**，华语综艺/剧集天然不在收录范围 |
| `torrents_csv` | `empty` | 数据集以英文站为主，华语新番覆盖差 |
| `bdflixs` | 68 条但**全部无关** | 详情页共用标题，搜「喜剧之王单口季」返回 1999 年电影《喜剧之王》 |
| `cilixiong`/`btsj6` | `empty` | 偏欧美电影 |
| `jackett` | `502` | 需用户自建，未部署等于零覆盖 |

结论：**缺的不是"再多几个动漫站"，而是一个收录面覆盖华语综艺/剧集的通用磁力源。**

## 实测凭据（2026-09-04）

接口 ``GET /search/{关键词}/rss``，返回标准 RSS 2.0：

| 关键词 | 结果 |
|---|---|
| `喜剧之王单口季` | **15 条真实磁力**，标题含发布组/集数/分辨率，`<size>` 字段完整 |
| `单口季` | 15 条（其中 12 条为目标剧集） |
| 连续 4 次请求 | 均 `200`，`items=15`，**无速率限制** |

对比 HTML 端点 ``/search?q=``：返回 **403**（Cloudflare）。
**只有 RSS 端点可用**，这也是为什么本 Provider 走 RSS 而不是解析页面。

## 为什么不复用 `RssIndexer`

体积/分类/infohash **不在独立标签里，而是塞进 ``<description>`` 的 CDATA**，
用 ``<br>`` 分隔：

```
<![CDATA[标题<br>2.32GB<br>Movie<br>8cc170d44591c91cfc672977ddc0155fbb773073]]>
```

通用方言层只会把这段当成描述文本，于是**全部资源 `size=0`**，
「按体积排序/筛选」整个失效。这就是必须单独写解析的唯一理由。

## 两个必须注意的实测细节

1. **必须跟随重定向**：``/search/<kw>/rss`` 会 301 到
   ``/search?q=<kw>&p=rss&page=rss``。不跟随的话拿到的是 17 字节的
   ``Moved Permanently``，解析出 0 条 —— 而这**看起来就像"这个站没有这部片"**。
2. **关键词用 ``quote`` 而非 ``quote_plus``**：它是路径段，``+`` 不会被解成空格。

## 已知缺陷（如实写明，不粉饰）

**不提供做种数**。RSS 里没有 seeders 字段，因此 `seeders=0`。
这不代表"没人做种"，但会让它在通用评分里天然吃亏 —— 这正是
:func:`app.services.search.enforce_site_share` 存在的理由。

**收录以"曾在 DHT 见过"为准，不保证仍有活跃 peer**。体积 <100MB 且
标题含剧集名的条目，常常是网盘引流用的**假种**（内含
「网盘链接在这.txt」+ 封面图）。本 Provider 如实返回体积，
由上层按体积过滤 —— 见 :mod:`app.core.filters` 的 `min_size`。
"""

from __future__ import annotations

import re
from urllib.parse import quote
from xml.etree import ElementTree as ET

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import FetchError, fetch_text

logger = get_logger(__name__)

#: 官方站点。BT4G 有多个镜像域名，允许用户覆盖。
#: 实测 ``bt4g.com`` 会 302 到 ``bt4gprx.com``，这里直接写最终域名少一跳。
DEFAULT_BASE = "https://bt4gprx.com"

#: 浏览器 UA。实测缺省 httpx UA 也能拿到 RSS，但 HTML 端点会 403；
#: 统一带上浏览器 UA，避免站点将来对 RSS 也收紧。
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[0-9a-fA-F]{32,40}")

#: ``<description>`` 的 CDATA 形如 ``标题<br>2.32GB<br>Movie<br><40位infohash>``。
#: 体积单位大小写混用（实测有 ``GB``/``MB``），故忽略大小写。
_SIZE_RE = re.compile(r"([\d.]+)\s*([KMGT]?B)\b", re.IGNORECASE)

_UNIT_FACTOR = {
    "B": 1,
    "KB": 1024,
    "MB": 1024 ** 2,
    "GB": 1024 ** 3,
    "TB": 1024 ** 4,
}


def _parse_description(raw: str) -> tuple[int, str]:
    """从 ``<description>`` 抽出（体积字节数, 分类）。

    取**第一个**体积样式的匹配：标题里也可能出现「4K」这类数字+字母，
    但真正的体积字段固定紧跟在标题之后，所以按 ``<br>`` 切分再逐段找
    比全串正则更稳。解析不出体积时返回 0 —— 与"体积为零"不可区分，
    但 BT4G 不存在 0 字节的种子，所以 0 在这里等价于"未知"。
    """
    text = str(raw or "").strip()
    if not text:
        return 0, ""
    # ElementTree 已把 CDATA 解包，这里只需按 <br> 切段
    parts = [p.strip() for p in re.split(r"<br\s*/?>", text) if p.strip()]
    size = 0
    category = ""
    for part in parts[1:]:  # parts[0] 是标题，跳过
        if not size:
            found = _SIZE_RE.fullmatch(part)
            if found:
                number = float(found.group(1))
                unit = found.group(2).upper()
                size = int(number * _UNIT_FACTOR.get(unit, 1))
                continue
        # 体积之后紧跟分类（Movie/Audio/…），infohash 是 40 位纯 hex，排除掉
        if not category and not re.fullmatch(r"[0-9a-fA-F]{40}", part):
            category = part[:40]
    return size, category


def _parse_pub_date(raw: str):
    """解析 ``<pubDate>``。

    BT4G 的格式是 ``Fri,3 Oct 2025 16:51:50 -0000`` ——
    **逗号后没有空格**，不符合 RFC 822，``parsedate_to_datetime``
    在部分 Python 版本上会抛。补一个空格再交给标准库，失败就返回 None
    （发布时间缺失只影响排序，不该让整条资源被丢掉）。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    from email.utils import parsedate_to_datetime

    normalized = re.sub(r"^(\w{3}),(\S)", r"\1, \2", text)
    for candidate in (normalized, text):
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError):
            continue
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    return None


@register
class Bt4gIndexer(SearchProvider):
    """BT4G 公开磁力搜索（RSS 端点，零配置）。"""

    name = "bt4g"
    kind = ProviderKind.INDEXER.value
    display_name = "BT4G 磁力搜索"

    def _base(self) -> str:
        return str(
            self.option("base_url", "") or self.config.get("url") or DEFAULT_BASE
        ).rstrip("/")

    def _search_url(self, keyword: str) -> str:
        """RSS 搜索地址。

        关键词走 ``quote`` 而**不是** ``quote_plus``：BT4G 把它当路径段，
        ``+`` 不会被解成空格，会变成字面加号从而搜不到东西（实测踩过）。
        """
        order = str(self.option("orderby", "") or "").strip()
        url = f"{self._base()}/search/{quote(keyword, safe='')}/rss"
        if order:
            url = f"{url}?orderby={quote(order, safe='')}"
        return url

    def _parse(self, text: str) -> list[Resource]:
        """解析 BT4G 的 RSS。

        用 ``ElementTree`` 而不是正则：标题里含 ``&amp;``/``<``  等转义，
        正则取出来还得自己反转义一遍，容易漏。
        """
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            # 解析失败要报错而不是返回空：空会被上层当成"这个站没有这部片"，
            # 而实际是"站点改版了/返回了错误页"，两者的处置完全不同（ADR-82）。
            # 实测踩到的真实案例：不跟随 301 时拿到 17 字节的
            # ``Moved Permanently`` 纯文本 —— 解析成 0 条就成了静默失败。
            raise FetchError(f"BT4G 返回的不是合法 RSS（站点可能已改版）：{exc}") from exc

        # 光「能被 XML 解析」不够：HTML 错误页往往**也是合法 XML**
        # （``<html>Moved Permanently</html>`` 就能过 ET.fromstring）。
        # 必须确认根节点真的是 rss，否则同样是把"站点坏了"错报成"没有结果"。
        if root.tag.lower() != "rss":
            raise FetchError(
                f"BT4G 返回的根节点是 <{root.tag}> 而不是 <rss>（站点可能已改版或返回了错误页）"
            )

        collected: list[Resource] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            if not title or not _MAGNET_RE.match(link):
                # 没有磁力的条目留下来等于给用户一条点了没反应的结果
                continue
            size, category = _parse_description(item.findtext("description") or "")
            infohash = ""
            found = _MAGNET_RE.search(link)
            if found:
                infohash = found.group(0).rsplit(":", 1)[-1].lower()
            extra: dict[str, str] = {"infohash": infohash, "source": "bt4g"}
            if category:
                extra["category"] = category
            collected.append(
                Resource(
                    title=title,
                    link=link,
                    site=self.site_name,
                    kind=ResourceKind.MAGNET.value,
                    size=size,
                    # RSS 不给做种数，如实写 0，不编造一个看起来健康的数字
                    seeders=0,
                    leechers=0,
                    publish_at=_parse_pub_date(item.findtext("pubDate") or ""),
                    page_url=(item.findtext("guid") or "").strip() or None,
                    priority=self.priority,
                    extra=extra,
                )
            )
        return collected

    async def search(
        self,
        keyword: str,
        *,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        page: int = 0,
    ) -> list[Resource]:
        query = str(keyword or "").strip()
        if not query:
            return []
        text = await fetch_text(
            self._search_url(query),
            headers={"User-Agent": BROWSER_UA},
            timeout=self.config.get("timeout"),
        )
        # 空响应与"没有这部片"必须分开：前者要报错让用户去查网络，
        # 后者是正常的零结果（ADR-82）

        if not text:
            raise FetchError("BT4G 无响应（检查网络/代理，或在站点配置里换一个镜像域名）")
        resources = self._parse(text)
        logger.info("BT4G 搜索「%s」：%d 条", query, len(resources))
        return resources

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        """BT4G 没有「全站最新」RSS 端点，如实返回空而不是伪造一批。"""
        return []

    async def health_check(self) -> tuple[bool, str]:
        text = await fetch_text(
            self._search_url("1080p"), headers={"User-Agent": BROWSER_UA}
        )
        if not text:
            return False, "无法连接 BT4G（检查网络/代理，或换一个镜像域名）"
        resources = self._parse(text)
        if not resources:
            return False, "接口连通但未解析到磁力（站点可能已改版）"
        return True, f"连通正常，搜索「1080p」返回 {len(resources)} 条"
