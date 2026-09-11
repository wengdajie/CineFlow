"""Bitsearch 公开 BT 搜索：**唯一提供做种数的免鉴权中文可用磁力源**。

## 为什么在 BT4G 之外还要加这一个

BT4G（:mod:`app.providers.indexer.bt4g`）解决了「有没有 BT 结果」，
但它**不给做种数**（RSS 无该字段，只能如实写 0）。而做种数是用户判断
「这个种还能不能下下来」的唯一依据 —— 一个 5 年前的 9GB 蓝光原盘和
一个昨天发布的 4GB WEB-DL，在没有 seeders 的列表里长得一模一样。

实测对比（关键词「喜剧之王单口季」，2026-09-04）：

| 源 | 条数 | 做种数 | 相关性 |
|---|---|---|---|
| BT4G | 15 | **全部 0**（字段缺失） | 15/15 相关 |
| Bitsearch | **20** | **真实值 0~11** | **20/20 相关** |

两者结果**互补而非重复**：BT4G 命中的 15 条以「高清剧集网」发布组为主，
Bitsearch 额外带出「不太灵影视」「8i2.fit」等源，且**只有它收录了第三季**。

## 实测凭据（2026-09-04）

| 场景 | 结果 |
|---|---|
| `喜剧之王单口季` | `200`，20 张卡片，做种数 0~11，体积 237KB~9.12GB |
| 同一 URL 连打 10 次 | **10/10 均 200/20 条**，无速率限制 |
| `zzzzqqqqxxxxyyyy`（不存在） | `200` + **0 卡片** + `No results found`（45964 字节） |
| `&page=2` | `200`，15 条（分页有效） |
| `&sort=seeders` | `200`，20 条且首条换成高做种条目（排序有效） |

## 两个必须注意的实测陷阱

**1. `500` 不代表「无结果」，而是偶发上游错误。**
首轮并发探测时 `zzzzqqqqxxxxyyyy` 稳定返回 `500`/588 字节，看起来像是
「站点用 500 表达查无此项」。改成**串行 + 间隔 1.5s** 后，同一关键词
14 次请求**全部 200**，无结果时返回的是完整的 `No results found` 页面。
那批 500 是并发压测触发的限流。因此本 Provider：
非 200 一律**抛** :class:`FetchError`（可能是限流，重试有意义），
**只有** 200 才解析；「真的没有」由页面内容判定。
若把 500 当成空结果，用户看到的是「这个站没这部片」，而实际该做的是稍后重试。

**2. `magnet:` 链接在 HTML 里是 HTML 转义过的。**
源码里是 `magnet:?xt&#x3D;urn:btih:...&amp;dn&#x3D;...`，
`=` 被写成 `&#x3D;`、`&` 被写成 `&amp;`。**不反转义直接用会得到一个无效磁力**，
下载器收下后静默失败。这也是为什么本模块不用正则直接抓 ``magnet:`` 后面的裸串。

## 已知缺陷（如实写明）

**收录含大量网盘引流假种。** 实测第三季的 6 条结果体积在 288KB~4.5MB，
下载 `.torrent` 后 bdecode 内容是「网盘链接在这.txt」+ 封面图，
标题却写着 `[4K1080P][国语中字]`。本 Provider **如实返回体积**，
由 :func:`app.core.filters._is_fake_torrent` 按「体积 <500MB + 高清标签」拦掉。
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from urllib.parse import quote_plus

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import BROWSER_UA, FetchError, fetch_text_result, is_cf_challenge
from app.utils.strings import parse_size

logger = get_logger(__name__)

#: 官方域名。``bitsearch.to`` 实测持续 500（该域名已废），
#: ``bitsearch.eu`` 才是可用的；页面里的 canonical 仍指向 .to，属站点自身未更新。
DEFAULT_BASE = "https://bitsearch.eu"

#: 备用域名，主域名故障时依次尝试。
#:
#: **为什么必须有**：v1.27.0 实测 BT4G 全部 5 个镜像都上了 Cloudflare 人机验证，
#: Bitsearch 成了本项目**唯一**还能覆盖华语综艺/剧集的通用磁力源。
#: 单点故障就是「BT 搜索整体归零」，而该站实测在并发下会返回 500/读超时。
#:
#: 实测（2026-09-04，关键词「喜剧之王单口季」「流浪地球」）：
#: 三个域名返回的卡片**逐条一致**（同 infohash、同做种数），
#: 说明是同一后端的镜像，可以安全互换。
FALLBACK_BASES: tuple[str, ...] = (
    "https://solidtorrents.to",
    "https://bitsearch.to",
)

#: 每张结果卡片的起始标记。站点用 Tailwind，类名长但**稳定**；
#: 按它切分比构造嵌套 div 的正则可靠得多（HTML 不是正则语言）。
_CARD_MARKER = 'class="bg-white rounded-lg shadow-sm border border-gray-200 p-6'

#: 「确实没有结果」的页面标志。必须靠它区分「零结果」与「解析失效」：
#: 站点改版后卡片标记失配同样会得到 0 条，那时要报错而不是说「没这部片」（ADR-82）。
_NO_RESULT_MARKER = "No results found"

_TITLE_RE = re.compile(r'<a href="/torrent/([0-9a-f]+)"[^>]*>\s*(.*?)\s*</a>', re.DOTALL)
_MAGNET_RE = re.compile(r'href="(magnet:\?xt[^"]+)"')
_INFOHASH_RE = re.compile(r"/download/torrent/([0-9a-fA-F]{40})")
_TAG_RE = re.compile(r"<[^>]+>")

#: 默认翻几页。
#:
#: 实测（2026-09-04）：「喜剧之王单口季」第 1 页 20 条、第 2 页 15 条，
#: 第 3 页读超时 —— 即 2 页能拿到 35 条（单页的 1.75 倍），
#: 再往后该站自己就撑不住了。设 2 是「多拿一页」与「别把站点打挂」的折中。
_DEFAULT_MAX_PAGES = 2

#: Swarm 统计块：图标 class 决定含义，紧跟的 ``<span class="font-medium">`` 是数值。
#: 不靠出现顺序取值 —— 站点把做种/下载数的顺序换一下就会静默取错。
_SEEDERS_RE = re.compile(
    r'fa-arrow-up"[^>]*></i>\s*<span[^>]*class="font-medium"[^>]*>\s*([\d,]+)\s*</span>'
)
_LEECHERS_RE = re.compile(
    r'fa-arrow-down"[^>]*></i>\s*<span[^>]*class="font-medium"[^>]*>\s*([\d,]+)\s*</span>'
)
_SIZE_RE = re.compile(
    r'fa-download"[^>]*></i>\s*<span[^>]*>\s*([\d.]+\s*[KMGT]?B)\s*</span>', re.IGNORECASE
)
_DATE_RE = re.compile(r'fa-calendar"[^>]*></i>\s*<span[^>]*>\s*([\d/]+)\s*</span>')
_CATEGORY_RE = re.compile(r'fa-video[^>]*></i>\s*<span[^>]*>\s*([^<]{1,40}?)\s*</span>')


def _clean_text(raw: str) -> str:
    """去标签 + HTML 反转义。标题里含 ``【】``、``&amp;`` 等，两步都不能省。"""
    return html.unescape(_TAG_RE.sub("", str(raw or ""))).strip()


def _parse_date(raw: str) -> datetime | None:
    """解析 ``9/2/2026`` 形式的日期（美式 M/D/YYYY）。

    实测站点输出 ``9/2/2026``、``10/20/2024`` —— **月在前且不补零**。
    解析不出返回 ``None``（缺失），绝不拿 ``datetime.now()`` 顶替：
    那会让所有条目看起来都是"今天刚发布"，按时间排序彻底失效。
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        month, day, year = (int(part) for part in text.split("/"))
        return datetime(year, month, day)
    except (ValueError, TypeError):
        return None


def _to_int(raw: str | None) -> int:
    """``"1,234"`` -> ``1234``；取不到返回 0。"""
    if not raw:
        return 0
    try:
        return int(str(raw).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0


@register
class BitsearchIndexer(SearchProvider):
    """Bitsearch HTML 搜索索引器（免鉴权，带做种数）。"""

    name = "bitsearch"
    kind = ProviderKind.INDEXER.value
    display_name = "Bitsearch 磁力搜索"

    def _base(self) -> str:
        return str(self.config.get("url") or DEFAULT_BASE).rstrip("/")

    def _bases(self) -> list[str]:
        """要依次尝试的域名：用户配的（或默认）在前，备用镜像在后。

        用户显式配了 ``url`` 时**仍然**追加备用域名 —— 该站单点故障
        等于整个 BT 搜索归零（见 FALLBACK_BASES 文档），
        为一个不可达的地址守节没有任何用户价值。
        """
        primary = self._base()
        bases = [primary]
        for candidate in FALLBACK_BASES:
            if candidate.rstrip("/") != primary:
                bases.append(candidate)
        return bases

    def _search_url(self, keyword: str, page: int = 0, base: str | None = None) -> str:
        """搜索地址。

        关键词走 ``quote_plus``：它是 **query 参数**（``?q=``），
        这里 ``+`` 正是空格的正确编码 —— 与 BT4G 的路径段情形相反，
        两者不能互换（BT4G 用 quote，见该模块注释）。
        """
        url = f"{(base or self._base()).rstrip('/')}/search?q={quote_plus(keyword)}"
        order = str(self.option("sort", "") or "").strip()
        if order:
            url = f"{url}&sort={quote_plus(order)}"
        # 站点页码从 1 开始，内部 page 从 0 开始
        if page and page > 0:
            url = f"{url}&page={page + 1}"
        return url

    def _parse(self, text: str) -> list[Resource]:
        """解析搜索结果页。

        零结果与解析失效必须分开：前者页面带 ``No results found``，
        后者是站点改版导致卡片标记失配 —— 都表现为 0 条，处置却相反（ADR-82）。
        """
        # 人机验证页也没有卡片标记，会落进下面「站点可能已改版」的分支。
        # 两者的下一步完全不同（改版=改代码，验证=换镜像/等它撤），必须分开报。
        if is_cf_challenge(text):
            raise FetchError(
                "Bitsearch 返回 Cloudflare 人机验证页（无法绕过，按 ADR-34 不做逆向）。"
                "请稍后重试或在站点配置里换一个镜像域名"
            )
        cards = text.split(_CARD_MARKER)[1:]
        if not cards:
            if _NO_RESULT_MARKER in text:
                return []
            raise FetchError(
                "Bitsearch 页面里既没有结果卡片，也没有「无结果」提示（站点可能已改版）"
            )

        collected: list[Resource] = []
        for card in cards:
            title_match = _TITLE_RE.search(card)
            magnet_match = _MAGNET_RE.search(card)
            if not title_match or not magnet_match:
                continue
            title = _clean_text(title_match.group(2))
            # 磁力链在 HTML 里是转义过的（``&#x3D;`` / ``&amp;``），
            # 不反转义会得到一个下载器静默拒收的无效链接
            link = html.unescape(magnet_match.group(1)).strip()
            # 不再重复检查 link 前缀：_MAGNET_RE 的 ``magnet:?xt`` 是**字面量**，
            # 匹配上就必然以 magnet: 开头，反转义也改不了它（实测验证过三种
            # 转义写法：``&#x6D;agnet:`` / ``magnet&#x3A;`` 都直接失配）。
            # 留着会是一段**永不执行**的分支 —— 注入测试无法使其转红，
            # 反而让人误以为这里有一层真实防护。
            if not title:
                continue
            infohash_match = _INFOHASH_RE.search(card)
            size_match = _SIZE_RE.search(card)
            category_match = _CATEGORY_RE.search(card)
            extra: dict[str, str] = {"source": "bitsearch"}
            if infohash_match:
                extra["infohash"] = infohash_match.group(1).lower()
            if category_match:
                extra["category"] = _clean_text(category_match.group(1))
            detail_id = title_match.group(1)
            collected.append(
                Resource(
                    title=title,
                    link=link,
                    site=self.site_name,
                    kind=ResourceKind.MAGNET.value,
                    # parse_size 认得 ``4.52 GB`` 这类带单位的字符串；
                    # 取不到时留 0 表示"未知"（不是"零字节"）
                    size=parse_size(size_match.group(1)) if size_match else 0,
                    seeders=_to_int(_SEEDERS_RE.search(card) and _SEEDERS_RE.search(card).group(1)),
                    leechers=_to_int(
                        _LEECHERS_RE.search(card) and _LEECHERS_RE.search(card).group(1)
                    ),
                    publish_at=_parse_date(_DATE_RE.search(card).group(1))
                    if _DATE_RE.search(card)
                    else None,
                    page_url=f"{self._base()}/torrent/{detail_id}",
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
        collected: list[Resource] = []
        seen: set[str] = set()
        #: 只有**第一页**失败才算站点故障。翻页失败（实测 500/读超时很常见）
        #: 不能让已经拿到的第一页结果一起丢掉 —— 那是把「部分成功」降级成
        #: 「彻底失败」，比不翻页更糟。
        first_error: FetchError | None = None
        pages = max(int(self.option("max_pages", _DEFAULT_MAX_PAGES) or 1), 1)
        for offset in range(pages):
            current = page + offset
            try:
                text = await self._fetch_page(query, current)
            except FetchError as exc:
                if offset == 0:
                    first_error = exc
                else:
                    logger.info(
                        "Bitsearch 第 %d 页失败（保留前 %d 条）：%s",
                        current + 1,
                        len(collected),
                        exc,
                    )
                break
            batch = self._parse(text)
            fresh = [r for r in batch if self._dedupe_key(r) not in seen]
            for item in batch:
                seen.add(self._dedupe_key(item))
            collected.extend(fresh)
            # 空页或整页都是重复 → 已经翻到底，再翻只是白等
            if not batch or not fresh:
                break
        if first_error is not None:
            raise first_error
        logger.info(
            "Bitsearch 搜索「%s」：%d 条（翻 %d 页）", query, len(collected), pages
        )
        return collected

    def _dedupe_key(self, resource: Resource) -> str:
        """跨页去重用的键。

        用 infohash 而不是标题：同一个种在不同页可能带不同的站点前缀
        （实测标题里含 ``【高清剧集网发布 www.XXX.com】``，域名部分会变）。
        infohash 缺失时退回链接，绝不退回标题。
        """
        return str((resource.extra or {}).get("infohash") or resource.link or "")

    async def _fetch_page(self, query: str, page: int) -> str:
        """取一页，逐个域名尝试。

        用 fetch_text_result 而非 fetch_text：非 200 必须抛错。
        实测该站在并发压力下会返回 500，那是**限流**不是「查无此项」，
        当成空结果会让用户以为站点没收录（ADR-82）。
        """
        last: FetchError | None = None
        for base in self._bases():
            try:
                text = await fetch_text_result(
                    self._search_url(query, page, base=base),
                    headers={
                        "User-Agent": BROWSER_UA,
                        "Accept-Language": "zh-CN,zh;q=0.9",
                    },
                    timeout=self.config.get("timeout"),
                )
            except FetchError as exc:
                last = exc
                logger.info("Bitsearch 域名 %s 失败，尝试下一个：%s", base, exc)
                continue
            if not text:
                last = FetchError("Bitsearch 返回空响应（检查网络/代理）")
                continue
            return text
        raise last or FetchError("Bitsearch 返回空响应（检查网络/代理）")

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        """Bitsearch 没有「全站最新」端点，如实返回空而不是伪造一批。"""
        return []

    async def health_check(self) -> tuple[bool, str]:
        try:
            text = await fetch_text_result(
                self._search_url("1080p"), headers={"User-Agent": BROWSER_UA}
            )
        except FetchError as exc:
            return False, f"无法连接 Bitsearch：{exc}"
        if not text:
            return False, "无法连接 Bitsearch（检查网络/代理）"
        # 契约是返回 (bool, str) 而不是抛异常：调用方直接解包，
        # 抛出去会让 /sites/{id}/test 变成 HTTP 500（掩盖真实原因）。
        try:
            resources = self._parse(text)
        except FetchError as exc:
            return False, str(exc)[:255]
        if not resources:
            return False, "接口连通但未解析到磁力（站点可能已改版）"
        return True, f"连通正常，搜索「1080p」返回 {len(resources)} 条"
