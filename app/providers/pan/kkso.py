"""KK 系网盘搜索站（`kkso.net` / `zhuiju.us` 同一套模板）。

**为什么单独写一个 Provider 而不是用 `html_generic`**：这类站把标题、分享链接
与提取码全塞在一个 Vue 的 `@click.stop="copyText($event,'标题','链接','')"`
行内表达式里，用「行正则 + 字段正则」两段式描述非常别扭（行边界不在标签上）；
而它们的模板高度一致——实测 `kkso.net` 与 `zhuiju.us` 的搜索页结构完全相同，
一份实现覆盖两站，还能覆盖后续同模板的站点。

来源：这两个站点由 [awesome-zhuiju-free](https://github.com/laoma2053/awesome-zhuiju-free)
清单收录，并且是我们逐站实测后**唯一两个「搜索即能拿到可用网盘链接」的网盘搜索站**
（其余 18 个候选站里 12 个「首页能开但搜不到链接」、4 个 403 反爬，见
`app/services/zhuiju.py` 模块注释）。

实测要点（都会让「看起来能用」的实现其实是错的）：

1. 提取码**不在** `copyText()` 的第三个参数里（实测该位恒为空串），
   而是拼在链接的 `?pwd=` 上——只读第三参会导致所有资源都「没有提取码」；
2. 标题里带 HTML 实体（`&nbsp;` / `&amp;`），不反转义会污染后续的季集识别；
3. 搜索路径是 `/s/{关键词}?p={页}`，`?s=` 那种 WordPress 写法在这两站是 404。
"""

from __future__ import annotations

import html
import re
from urllib.parse import quote, urlparse

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.pan.pansou import detect_pan_type
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import fetch_text
from app.utils.strings import parse_datetime

logger = get_logger(__name__)

#: 行内的 copyText('标题','链接','提取码')，三个参数都可能含转义单引号
_COPY_RE = re.compile(
    r"copyText\(\s*\$event\s*,\s*'((?:[^'\\]|\\.)*)'\s*,"
    r"\s*'((?:[^'\\]|\\.)*)'\s*,\s*'((?:[^'\\]|\\.)*)'\s*\)"
)
#: 分享链接自带的提取码（实测提取码在这里，不在 copyText 第三参）
_PWD_RE = re.compile(r"[?&]pwd=([0-9a-zA-Z]{4,8})")
#: 结果行上的日期（用于补 publish_at）
_DATE_RE = re.compile(r'class="type time"\s*>\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\s*<')

#: 已知采用该模板的站点（仅用于默认站点与文档，不限制用户自行填）
KNOWN_SITES = ("https://kkso.net", "https://www.zhuiju.us")


def _clean_title(raw: str) -> str:
    """反转义并压掉空白。"""
    text = html.unescape(str(raw or "")).replace("\\'", "'")
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split())


@register
class KksoProvider(SearchProvider):
    """KK 系网盘搜索（HTML 抓取）。"""

    name = "kkso"
    kind = ProviderKind.PAN.value
    display_name = "KK 网盘搜（kkso / zhuiju.us）"

    def _base(self) -> str:
        return str(self.config.get("url") or "").strip().rstrip("/")

    def _search_url(self, keyword: str, page: int) -> str:
        base = self._base()
        template = str(self.option("search_url") or "{base}/s/{keyword}?p={page}")
        return template.format(base=base, keyword=quote(keyword), page=max(1, page + 1))

    async def search(
        self,
        keyword: str,
        *,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        page: int = 0,
    ) -> list[Resource]:
        base = self._base()
        if not base or not keyword:
            return []
        text = await fetch_text(
            self._search_url(keyword, page), timeout=self.config.get("timeout")
        )
        if not text:
            return []
        return self._parse(text)

    def _parse(self, text: str) -> list[Resource]:
        dates = _DATE_RE.findall(text)
        rows = _COPY_RE.findall(text)
        resources: list[Resource] = []
        seen: set[str] = set()
        for index, (raw_title, raw_link, raw_pwd) in enumerate(rows):
            link = _clean_title(raw_link)
            if not link.startswith("http") or link in seen:
                continue
            seen.add(link)
            title = _clean_title(raw_title)
            if not title:
                continue
            # 提取码：优先 copyText 第三参，实测多为空，则从链接 ?pwd= 取
            password = _clean_title(raw_pwd)
            if not password:
                found = _PWD_RE.search(link)
                if found:
                    password = found.group(1)
            pan_label = detect_pan_type(link)
            published = dates[index] if index < len(dates) else None
            resources.append(
                Resource(
                    title=title,
                    link=link,
                    site=f"{self.site_name}·{pan_label}",
                    kind=ResourceKind.PAN.value,
                    page_url=link,
                    publish_at=parse_datetime(published) if published else None,
                    priority=self.priority,
                    password=password or None,
                    extra={"pan_type": pan_label},
                )
            )
        return resources

    async def health_check(self) -> tuple[bool, str]:
        base = self._base()
        if not base:
            return False, "未配置站点地址"
        host = urlparse(base).netloc
        text = await fetch_text(self._search_url("庆余年", 0), timeout=15)
        if text is None:
            return False, f"无法访问 {host}"
        found = len(_COPY_RE.findall(text))
        if not found:
            # 区分「站点活着但搜不到」与「站点挂了」——前者多半是模板变了
            return False, "页面可访问但未解析到分享链接（站点模板可能已变更）"
        return True, f"连接正常，示例关键词命中 {found} 条"
