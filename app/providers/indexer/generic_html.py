"""通用 HTML 索引器：用正则规则适配没有 JSON API 的网页型资源站。

许多小型 BT 站/资源站只有 HTML 页面，本 Provider 让用户通过
``options`` 里的正则规则描述「行 → 字段」，无需写代码即可接入。

配置示例::

    {
      "search_url": "https://example.com/search?q={keyword}&page={page}",
      "latest_url": "https://example.com/latest",
      "row_pattern": "<tr class=\\"item\\">(.*?)</tr>",
      "field_patterns": {
        "title": "title=\\"([^\\"]+)\\"",
        "link": "href=\\"(magnet:[^\\"]+)\\"",
        "size": "<td class=\\"size\\">([^<]+)</td>",
        "seeders": "<td class=\\"se\\">(\\\\d+)</td>"
      }
    }

若站点把磁力链直接写在页面里，也可以只配 ``magnet_only: true``，
本 Provider 会自动抓取页面内所有磁力链接。

**POST 表单搜索**（``search_method: "POST"``）：不少国内老站（EmpireCMS/帝国
后台居多）的搜索入口只接受 POST，GET 带参数会被无声地返回**首页**。这种站如果
按 GET 配，就会出现「任何关键词都返回同一批结果」——包括乱码关键词也有结果，
因为抓的其实是首页。配置示例::

    {
      "search_url": "https://site/e/search/index.php",
      "search_method": "POST",
      "search_data": {"keyboard": "{keyword}", "classid": "1,2",
                      "show": "title", "tempid": "1"},
      "detail_link_field": "href=\"(/movie/\\d+\\.html)\""
    }

**详情页二段抓取的标题**：``detail_link_field`` 命中后会进详情页抓磁力。
详情页里的磁力多数**不带 ``dn=`` 参数**，此时标题会退化成 ``fallback_title``。
不要把 ``fallback_title`` 设成用户输入的关键词——那会让每条结果的标题都变成
搜索词本身（"流浪地球"搜出 20 条全叫"流浪地球"），既看不出版本/清晰度，
也让「本地关键词过滤」形同虚设。本 Provider 改为从详情页的
``<h1>``/``<title>`` 提取真实片名，配置项 ``detail_title_field`` 可覆盖。
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import quote, urljoin

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.indexer.generic_api import clean_text, guess_kind
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import fetch_text
from app.utils.strings import match_keywords, parse_datetime, parse_size

logger = get_logger(__name__)

_TAG = re.compile(r"<[^>]+>")
_MAGNET = re.compile(r"magnet:\?xt=urn:btih:[0-9a-zA-Z]{32,40}[^\s\"'<>\\]*", re.I)
_TORRENT_HREF = re.compile(r"""href=["']([^"']+\.torrent[^"']*)["']""", re.I)


def strip_tags(text: str) -> str:
    """去掉 HTML 标签并压缩空白，保留纯文本。"""
    return re.sub(r"\s+", " ", clean_text(_TAG.sub(" ", str(text or "")))).strip()


def _compile(pattern: str) -> re.Pattern[str] | None:
    """编译用户提供的正则；非法正则不应让整站失效。"""
    try:
        return re.compile(pattern, re.S | re.I)
    except re.error as exc:
        logger.warning("非法正则 %r: %s", pattern, exc)
        return None


def _first_group(pattern: re.Pattern[str] | None, text: str) -> str:
    """取第一个捕获组（无捕获组时取整段匹配）。"""
    if pattern is None:
        return ""
    match = pattern.search(text)
    if not match:
        return ""
    return clean_text(match.group(1) if match.groups() else match.group(0))


@register
class GenericHtmlIndexer(SearchProvider):
    """正则映射式 HTML 站点索引器（自定义网页资源站通用适配器）。"""

    name = "html_generic"
    kind = ProviderKind.INDEXER.value
    display_name = "自定义网页站点（正则）"

    def _root(self) -> str:
        return str(self.config.get("url") or self.option("site_url", "")).rstrip("/") + "/"

    def _headers(self) -> dict[str, str]:
        headers = dict(self.option("headers", {}) or {})
        if self.config.get("cookie"):
            headers.setdefault("Cookie", str(self.config["cookie"]))
        if self.option("referer"):
            headers.setdefault("Referer", str(self.option("referer")))
        return headers

    def _format_url(self, template: str, keyword: str, page: int) -> str:
        """填充 URL 模板中的 ``{keyword}`` / ``{page}`` 占位符。"""
        url = str(template or "")
        if not url:
            return ""
        url = url.replace("{keyword}", quote(keyword)).replace(
            "{page}", str(page + int(self.option("page_base", 1)))
        )
        if url.startswith("/"):
            url = urljoin(self._root(), url.lstrip("/"))
        return url

    async def _load(self, url: str, *, data: dict[str, str] | None = None) -> str:
        """取页面。给了 ``data`` 就走 POST 表单提交。

        为什么需要 POST：EmpireCMS 一类站点的搜索只认 POST，用 GET 带参数
        会**静默返回首页**（HTTP 200、内容看着正常），于是"任何关键词都有结果"。
        这种失败模式比报错更危险，所以必须支持按站点声明的方法提交。
        """
        if not url:
            return ""
        text = await fetch_text(
            url,
            method="POST" if data else "GET",
            data=data or None,
            headers=self._headers(),
            timeout=self.config.get("timeout"),
        )
        return text or ""

    def _search_form(self, keyword: str, page: int) -> dict[str, str] | None:
        """按 ``search_method``/``search_data`` 组装 POST 表单。"""
        method = str(self.option("search_method", "GET") or "GET").upper()
        if method != "POST":
            return None
        raw = self.option("search_data", {}) or {}
        if not isinstance(raw, dict):
            return None
        form: dict[str, str] = {}
        for key, value in raw.items():
            form[str(key)] = (
                str(value)
                .replace("{keyword}", keyword)
                .replace("{page}", str(page + int(self.option("page_base", 1))))
            )
        # 没写 search_data 时给个最常见的默认键，省去逐站猜字段
        if not form:
            form = {"keyboard": keyword}
        return form

    def _absolute(self, link: str) -> str:
        """把相对链接补全为绝对地址（磁力链保持原样）。"""
        if not link or link.startswith("magnet:") or link.startswith("http"):
            return link
        return urljoin(self._root(), link.lstrip("/"))

    def _parse(self, html_text: str, *, fallback_title: str = "") -> list[Resource]:
        """按配置的正则规则把页面解析成资源列表。"""
        if not html_text:
            return []

        row_pattern = self.option("row_pattern")
        if not row_pattern or self.option("magnet_only", False):
            return self._parse_bare_links(html_text, fallback_title)

        compiled_row = _compile(str(row_pattern))
        if compiled_row is None:
            return []

        patterns = {
            key: _compile(str(value))
            for key, value in (self.option("field_patterns", {}) or {}).items()
        }
        limit = int(self.option("max_rows", 100))
        resources: list[Resource] = []

        for match in compiled_row.finditer(html_text):
            row = match.group(1) if match.groups() else match.group(0)
            link = _first_group(patterns.get("link"), row)
            if not link:
                found = _MAGNET.search(row) or _TORRENT_HREF.search(row)
                link = clean_text(found.group(0) if found else "")
            if not link:
                continue

            title = _first_group(patterns.get("title"), row)
            title = strip_tags(title) or fallback_title
            if not title:
                continue

            resources.append(
                Resource(
                    title=title,
                    link=self._absolute(link),
                    site=self.site_name,
                    kind=guess_kind(link, self.option("kind")),
                    page_url=self._absolute(_first_group(patterns.get("page_url"), row)) or None,
                    description=strip_tags(_first_group(patterns.get("description"), row))[:500]
                    or None,
                    size=parse_size(_first_group(patterns.get("size"), row)),
                    seeders=_digits(_first_group(patterns.get("seeders"), row)),
                    leechers=_digits(_first_group(patterns.get("leechers"), row)),
                    publish_at=parse_datetime(_first_group(patterns.get("publish_at"), row)),
                    priority=self.priority,
                    password=_first_group(patterns.get("password"), row) or None,
                    extra={"provider": self.name},
                )
            )
            if len(resources) >= limit:
                break
        return resources

    def _parse_bare_links(self, html_text: str, fallback_title: str) -> list[Resource]:
        """兜底策略：直接抓取页面内所有磁力链/种子链接。"""
        resources: list[Resource] = []
        seen: set[str] = set()
        for match in _MAGNET.finditer(html_text):
            link = match.group(0)
            infohash = _infohash(link)
            if infohash in seen:
                continue
            seen.add(infohash)
            # 磁力链的 dn 参数常带资源名
            name = ""
            dn = re.search(r"[?&]dn=([^&]+)", link)
            if dn:
                from urllib.parse import unquote

                name = unquote(dn.group(1)).replace("+", " ")
            resources.append(
                Resource(
                    title=clean_text(name) or fallback_title or f"magnet-{infohash[:12]}",
                    link=link,
                    site=self.site_name,
                    kind=ResourceKind.MAGNET.value,
                    priority=self.priority,
                    extra={"provider": self.name},
                )
            )
        return resources

    async def search(
        self,
        keyword: str,
        *,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        page: int = 0,
    ) -> list[Resource]:
        template = str(self.option("search_url", "") or "")
        if not keyword or not template:
            return []

        url = self._format_url(template, keyword, page)
        form = self._search_form(keyword, page)
        # POST 搜索时 search_url 就是表单 action，不需要把关键词拼进 query
        html_text = await self._load(url, data=form)
        resources = self._parse(html_text, fallback_title=keyword)

        # 需要进详情页才能拿到磁力的站点
        if self.option("detail_link_field") and not resources:
            resources = await self._follow_details(url, keyword, form=form)

        if self.option("local_filter", True) and keyword:
            resources = [
                item
                for item in resources
                if match_keywords(item.title, [re.escape(keyword)], mode="any")
            ] or resources
        return resources

    async def _follow_details(
        self, list_url: str, keyword: str, *, form: dict[str, str] | None = None
    ) -> list[Resource]:
        """从列表页提取详情页地址，再进详情页抓磁力。"""
        pattern = _compile(str(self.option("detail_link_field")))
        if pattern is None:
            return []
        html_text = await self._load(list_url, data=form)
        links = [
            self._absolute(clean_text(m.group(1) if m.groups() else m.group(0)))
            for m in list(pattern.finditer(html_text))[: int(self.option("max_detail_items", 5))]
        ]
        pages = await asyncio.gather(
            *(self._load(link) for link in links), return_exceptions=True
        )
        collected: list[Resource] = []
        for link, page_html in zip(links, pages, strict=False):
            if isinstance(page_html, BaseException) or not page_html:
                continue
            # 用详情页的真实片名兜底，**不要用搜索关键词**：详情页磁力普遍不带
            # dn= 参数，拿关键词兜底会让所有结果标题都等于搜索词，
            # 用户既看不出清晰度/版本，本地关键词过滤也变成恒真。
            title = self._detail_title(page_html) or keyword
            for resource in self._parse_bare_links(page_html, title):
                resource.page_url = link
                collected.append(resource)
        return collected

    def _detail_title(self, html_text: str) -> str:
        """从详情页取真实片名（``detail_title_field`` 可覆盖，默认 h1 → title）。"""
        custom = self.option("detail_title_field")
        if custom:
            found = _first_group(_compile(str(custom)), html_text)
            if found:
                return strip_tags(found)
        for pattern in (r"<h1[^>]*>(.{0,200}?)</h1>", r"<title>(.{0,200}?)</title>"):
            found = _first_group(_compile(pattern), html_text)
            if found:
                # 站点常在 <title> 里挂后缀（"片名(2023) ... - 站名"），切掉
                text = strip_tags(found)
                for sep in (" - ", " | ", " – "):
                    if sep in text:
                        text = text.split(sep)[0].strip()
                return text
        return ""

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        template = str(self.option("latest_url", "") or "")
        if not template:
            return []
        pages = max(int(self.option("latest_pages", 1)), 1)
        htmls = await asyncio.gather(
            *(self._load(self._format_url(template, "", page)) for page in range(pages)),
            return_exceptions=True,
        )
        collected: list[Resource] = []
        for html_text in htmls:
            if isinstance(html_text, BaseException):
                continue
            collected.extend(self._parse(html_text))
            if len(collected) >= limit:
                break
        return collected[:limit]

    async def health_check(self) -> tuple[bool, str]:
        if not self.config.get("url") and not self.option("site_url"):
            return False, "未配置站点地址"
        if not self.option("search_url") and not self.option("latest_url"):
            return False, "未配置 search_url / latest_url"

        probe = str(self.option("health_keyword", "") or "")
        if probe and self.option("search_url"):
            results = await self.search(probe)
            return (
                (True, f"搜索连通，返回 {len(results)} 条资源")
                if results
                else (False, f"搜索「{probe}」未解析到资源，请检查正则")
            )
        if self.option("latest_url"):
            latest = await self.fetch_latest(limit=5)
            return (
                (True, f"最新页连通，返回 {len(latest)} 条资源")
                if latest
                else (False, "最新页未解析到资源，请检查正则")
            )
        return True, "配置已就绪（未设置 health_keyword，跳过实测）"


def _infohash(link: str) -> str:
    """从磁力链中取出 infohash（忽略 dn/tr 等附加参数）。"""
    match = re.search(r"btih:([0-9a-z]{32,40})", str(link), re.I)
    return match.group(1).lower() if match else str(link).lower()


def _digits(value: str) -> int:
    """从文本中提取整数。"""
    digits = re.sub(r"[^\d]", "", str(value or ""))
    return int(digits) if digits else 0
