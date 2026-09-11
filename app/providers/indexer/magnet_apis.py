"""公开聚合磁力 JSON API 源：apibay（TPB）/ therarbg / knaben。

## 为什么在 BT4G/Bitsearch 之外还要加这三个

v1.27.0 的现状是：BT4G 全部镜像被 Cloudflare 人机验证拦截（已默认禁用），
Bitsearch 是唯一还能覆盖华语内容的通用磁力源 —— **单点故障，它一挂
BT 搜索就整体归零**。

magnet-finder（https://github.com/blueslmj/magnet-finder）给出的出路：
**不抓 HTML 站，改走不挂 Cloudflare 的公开 JSON API**。这些接口直接返回
做种数，比解析 HTML 又快又准，且与 Bitsearch 互补（华语 vs 全球英文内容）。

## 实测凭据（2026-09-11）

| 源 | 请求 | 实测结果 |
|---|---|---|
| apibay | ``GET /q.php?q=dune 2024&cat=0`` | 100 条，最高 947 做种，字段全 |
| therarbg | ``GET /get-posts/keywords:dune%202024/?format=json`` | 50 条/页，``links.next`` 分页有效 |
| knaben | ``POST /v1 {"query":"流浪地球",...}`` | 7 条，首条即目标（DBD-Raws BDRip，9 做种） |

## 必须注意的实测细节

1. **apibay 的数字字段全是字符串**（``"seeders": "947"``），不转 int
   排序就全错；没结果时返回一条 ``id="0"`` 的占位记录，必须过滤。
2. **therarbg 多词查询的空格必须是 ``%20`` 而不是 ``+``**，用 ``+``
   会返回 0 条（magnet-finder 实测坑，本模块用 ``quote`` 规避）。
3. **knaben 的 ``magnetUrl`` 自带 tracker 列表**，比自己拼的裸磁力
   更容易连上 peer，能用就用。
4. 坏掉（非 200 / 非 JSON）一律抛 :class:`FetchError`，不返回空列表 ——
   空会被上层当成「这部片没资源」，而实际该做的是换源/重试（ADR-82）。

## 已知缺陷（如实写明）

三个源都以全球/英文内容为主，**华语关键词覆盖弱于 Bitsearch**
（实测「流浪地球」：apibay/therarbg 相关性差，knaben 尚可）。
它们是 Bitsearch 的**备份与互补**，不是替代。

EZTV（API 只支持按 imdb_id 查，不支持关键词）与 rargb（无 API，
需逐条进详情页抓磁力）按 YAGNI 不接入。
"""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import quote

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import BROWSER_UA, FetchError, fetch_text_result
from app.utils.strings import build_magnet

logger = get_logger(__name__)

APIBAY_BASE = "https://apibay.org"
THERARBG_BASE = "https://therarbg.com"
KNABEN_BASE = "https://api.knaben.org"


def _ts_to_datetime(raw: object) -> datetime | None:
    """unix 秒转 naive datetime，非法值返回 None（发布时间缺失只影响排序）。"""
    try:
        seconds = int(str(raw or "0"))
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds)
    except (OverflowError, OSError, ValueError):
        return None


async def _fetch_json(url: str, *, json_body: object = None, timeout: object = None) -> object:
    """请求并解析 JSON；非 JSON 响应抛 FetchError（坏了 ≠ 没有，ADR-82）。"""
    text = await fetch_text_result(
        url,
        method="POST" if json_body is not None else "GET",
        json_body=json_body,
        headers={"User-Agent": BROWSER_UA},
        timeout=timeout,
    )
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FetchError(f"{url} 返回的不是合法 JSON（站点可能已改版或被拦截）：{exc}") from exc


@register
class TpbIndexer(SearchProvider):
    """The Pirate Bay 官方 API（apibay），一次返回全部命中，不分页。"""

    name = "tpb"
    kind = ProviderKind.INDEXER.value
    display_name = "TPB 磁力搜索（apibay）"

    def _base(self) -> str:
        return str(
            self.option("base_url", "") or self.config.get("url") or APIBAY_BASE
        ).rstrip("/")

    def _parse(self, payload: object) -> list[Resource]:
        if not isinstance(payload, list):
            raise FetchError("apibay 返回的不是结果列表（站点可能已改版或被限流）")
        collected: list[Resource] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            infohash = str(row.get("info_hash") or "").lower()
            # 没结果时 apibay 返回 id=0 / 全零 hash 的占位记录
            if str(row.get("id")) == "0" or not infohash or set(infohash) == {"0"}:
                continue
            title = str(row.get("name") or "").strip()
            if not title:
                continue
            extra: dict[str, str] = {"infohash": infohash, "source": "apibay"}
            if row.get("imdb"):
                extra["imdb"] = str(row["imdb"])
            collected.append(
                Resource(
                    title=title,
                    link=build_magnet(infohash, title),
                    site=self.site_name,
                    kind=ResourceKind.MAGNET.value,
                    # apibay 的数字字段全是字符串，不转 int 排序全错
                    size=int(str(row.get("size") or 0)),
                    seeders=int(str(row.get("seeders") or 0)),
                    leechers=int(str(row.get("leechers") or 0)),
                    publish_at=_ts_to_datetime(row.get("added")),
                    page_url=(
                        f"https://thepiratebay.org/description.php?id={row['id']}"
                        if row.get("id")
                        else None
                    ),
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
        payload = await _fetch_json(
            f"{self._base()}/q.php?q={quote(query, safe='')}&cat=0",
            timeout=self.config.get("timeout"),
        )
        resources = self._parse(payload)
        logger.info("apibay 搜索「%s」：%d 条", query, len(resources))
        return resources

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        """apibay 的搜索接口不支持空关键词拉最新，如实返回空。"""
        return []

    async def health_check(self) -> tuple[bool, str]:
        # 验的是**接口结构**而不是结果数：实测 apibay 会对某个关键词短时返回
        # 占位记录（id=0）却对另一个关键词返回 100 条——0 条不等于站点坏了。
        # 改版/被墙时拿到的是非 JSON，_fetch_json 会抛 FetchError。
        try:
            payload = await _fetch_json(
                f"{self._base()}/q.php?q=1080p&cat=0",
                timeout=self.config.get("timeout"),
            )
        except FetchError as exc:
            return False, str(exc)[:255]
        if not isinstance(payload, list):
            return False, "接口返回结构异常（站点可能已改版）"
        count = len(self._parse(payload))
        if count:
            return True, f"连通正常，搜索「1080p」返回 {count} 条"
        return True, "接口连通正常（本次无结果，可能为上游短时波动）"


@register
class TherarbgIndexer(SearchProvider):
    """therarbg（RARBG 数据库延续），``?format=json`` 即 REST 接口，带分页。"""

    name = "therarbg"
    kind = ProviderKind.INDEXER.value
    display_name = "therarbg 磁力搜索"

    def _base(self) -> str:
        return str(
            self.option("base_url", "") or self.config.get("url") or THERARBG_BASE
        ).rstrip("/")

    def _search_url(self, keyword: str) -> str:
        """搜索地址。

        空格必须 ``%20`` 而不是 ``+``：多词查询用 ``+`` 时 therarbg
        返回 0 条（实测坑）。``quote`` 正好满足。
        """
        return f"{self._base()}/get-posts/keywords:{quote(keyword, safe='')}/?format=json"

    def _parse_rows(self, rows: object) -> list[Resource]:
        collected: list[Resource] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            infohash = str(row.get("h") or "").lower()
            title = str(row.get("n") or "").strip()
            if not infohash or not title:
                continue
            extra: dict[str, str] = {"infohash": infohash, "source": "therarbg"}
            if row.get("i"):
                extra["imdb"] = str(row["i"])
            collected.append(
                Resource(
                    title=title,
                    link=build_magnet(infohash, title),
                    site=self.site_name,
                    kind=ResourceKind.MAGNET.value,
                    size=int(row.get("s") or 0),
                    seeders=int(row.get("se") or 0),
                    leechers=int(row.get("le") or 0),
                    publish_at=_ts_to_datetime(row.get("a")),
                    page_url=(
                        f"{self._base()}/post-detail/{row['pk']}/" if row.get("pk") else None
                    ),
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
        max_pages = int(self.option("max_pages", 2) or 2)
        collected: list[Resource] = []
        url: str | None = self._search_url(query)
        for _ in range(max(1, max_pages)):
            if not url:
                break
            payload = await _fetch_json(url, timeout=self.config.get("timeout"))
            if not isinstance(payload, dict):
                raise FetchError("therarbg 返回的不是结果对象（站点可能已改版）")
            collected.extend(self._parse_rows(payload.get("results")))
            links = payload.get("links") or {}
            url = str(links.get("next") or "") or None
        logger.info("therarbg 搜索「%s」：%d 条", query, len(collected))
        return collected

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        """therarbg 的关键词接口不支持空关键词拉最新，如实返回空。"""
        return []

    async def health_check(self) -> tuple[bool, str]:
        # 同 TPB：验结构不验条数（上游短时 0 条是合法响应）。
        try:
            payload = await _fetch_json(
                self._search_url("1080p"),
                timeout=self.config.get("timeout"),
            )
        except FetchError as exc:
            return False, str(exc)[:255]
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            return False, "接口返回结构异常（站点可能已改版）"
        count = len(self._parse_rows(payload.get("results")))
        if count:
            return True, f"连通正常，搜索「1080p」返回 {count} 条"
        return True, "接口连通正常（本次无结果，可能为上游短时波动）"


@register
class KnabenIndexer(SearchProvider):
    """knaben 聚合索引（聚合几十个站点，结果带 tracker 来源标注）。"""

    name = "knaben"
    kind = ProviderKind.INDEXER.value
    display_name = "knaben 聚合磁力搜索"

    def _base(self) -> str:
        return str(
            self.option("base_url", "") or self.config.get("url") or KNABEN_BASE
        ).rstrip("/")

    def _parse(self, payload: object) -> list[Resource]:
        if not isinstance(payload, dict):
            raise FetchError("knaben 返回的不是结果对象（站点可能已改版）")
        hits = payload.get("hits") or []
        collected: list[Resource] = []
        for hit in hits if isinstance(hits, list) else []:
            if not isinstance(hit, dict):
                continue
            title = str(hit.get("title") or "").strip()
            infohash = str(hit.get("hash") or "").lower()
            if not title or not infohash:
                continue
            # 站点给的 magnetUrl 自带 tracker 列表，比自己拼的裸磁力好连
            magnet = str(hit.get("magnetUrl") or "").strip() or build_magnet(infohash, title)
            extra: dict[str, str] = {"infohash": infohash, "source": "knaben"}
            if hit.get("tracker"):
                extra["tracker"] = str(hit["tracker"])
            publish_at = None
            raw_date = str(hit.get("date") or "")[:19]
            if raw_date:
                try:
                    publish_at = datetime.fromisoformat(raw_date)
                except ValueError:
                    publish_at = None
            collected.append(
                Resource(
                    title=title,
                    link=magnet,
                    site=self.site_name,
                    kind=ResourceKind.MAGNET.value,
                    size=int(hit.get("bytes") or 0),
                    seeders=int(hit.get("seeders") or 0),
                    leechers=int(hit.get("peers") or 0),
                    publish_at=publish_at,
                    page_url=str(hit.get("details") or "").strip() or None,
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
        size = int(self.option("limit", 100) or 100)
        payload = await _fetch_json(
            f"{self._base()}/v1",
            json_body={
                "search_type": "100%",
                "search_field": "title",
                "query": query,
                "order_by": "seeders",
                "order_direction": "desc",
                "size": min(300, max(20, size)),
                "hide_unsafe": True,
                "hide_xxx": True,
            },
            timeout=self.config.get("timeout"),
        )
        resources = self._parse(payload)
        logger.info("knaben 搜索「%s」：%d 条", query, len(resources))
        return resources

    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        """knaben 的搜索接口不支持空关键词拉最新，如实返回空。"""
        return []

    async def health_check(self) -> tuple[bool, str]:
        # 同 TPB：验结构不验条数（上游短时 0 条是合法响应）。
        try:
            payload = await _fetch_json(
                f"{self._base()}/v1",
                json_body={
                    "search_type": "100%",
                    "search_field": "title",
                    "query": "1080p",
                    "order_by": "seeders",
                    "order_direction": "desc",
                    "size": 20,
                    "hide_unsafe": True,
                    "hide_xxx": True,
                },
                timeout=self.config.get("timeout"),
            )
        except FetchError as exc:
            return False, str(exc)[:255]
        if not isinstance(payload, dict) or not isinstance(payload.get("hits"), list):
            return False, "接口返回结构异常（站点可能已改版）"
        count = len(self._parse(payload))
        if count:
            return True, f"连通正常，搜索「1080p」返回 {count} 条"
        return True, "接口连通正常（本次无结果，可能为上游短时波动）"
