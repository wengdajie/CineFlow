"""通用 JSON API 索引器：用字段映射适配任意 JSON 资源站，无需写代码。

设计目标：把「一个陌生的 JSON 资源站」抽象为若干可配置的描述项，
用户只需在站点 ``options`` 里描述接口路径与字段映射即可接入。

支持三种取资源的形态：

1. **一阶段**：搜索接口直接返回带下载链接的列表（最常见的 BT 站 API）
2. **两阶段**：搜索接口只返回影视条目（无链接），需按条目 ID 再请求详情
   接口，从详情里取出该片的全部种子/网盘链接
3. **最新流**：独立的「最新发布」接口，用于定时追新雷达

配置示例（两阶段站点）::

    {
      "api_base": "https://example.com/api/v1",
      "fixed_params": {"app_id": "xxx", "identity": "yyy"},
      "search_path": "getVideoList",
      "query_key": "sb",
      "list_path": "data.data",
      "item_map": {"detail_id": "idcode", "title": "title", "year": "years"},
      "detail_path": "getVideoDetail",
      "detail_query_key": "id",
      "detail_extract": [
        {"list_path": "data.all_seeds", "kind": "magnet",
         "map": {"title": "zname", "link": "zlink", "size": "zsize"}}
      ]
    }
"""

from __future__ import annotations

import asyncio
import html
import re
from typing import Any
from urllib.parse import urljoin

from app.core.logger import get_logger
from app.providers.base import Resource, SearchProvider
from app.providers.registry import register
from app.schemas.enums import ProviderKind, ResourceKind
from app.utils.http import fetch_json
from app.utils.strings import normalize, parse_datetime, parse_size

logger = get_logger(__name__)

#: 列表项默认字段映射
DEFAULT_ITEM_MAP = {
    "title": "title",
    "link": "link",
    "size": "size",
    "seeders": "seeders",
    "leechers": "leechers",
    "page_url": "page_url",
    "password": "password",
    "description": "description",
    "publish_at": "publish_at",
    "detail_id": "id",
}

#: 「作品级」元数据字段映射：封面、评分、年份这些描述**作品**而非单个种子的信息。
#: 很多影视站的搜索接口本来就返回这些字段，采下来榜单就能画封面墙，
#: 不必依赖 TMDB（未配 API Key 时仍可有图有评分）。
DEFAULT_MEDIA_MAP = {
    "poster": "image",
    "rating": "doub_score",
    "rating_people": "doub_score_peo_num",
    "year": "years",
    "genres": "class",
    "area": "production_area",
    "total_episodes": "episodes",
    "overview": "abstract",
    "actors": "performer",
    "director": "director",
    "alias": "alias",
    "status_text": "ejs",
    "definition": "definition",
}

_RELATIVE_TIME = re.compile(r"^\s*\d+\s*(分钟|小时|天|秒|周|月)前\s*$")


def dig(data: Any, path: str) -> Any:
    """按 ``a.b.c`` 路径取值，支持列表下标。"""
    current = data
    for segment in str(path or "").split("."):
        if not segment:
            continue
        if isinstance(current, dict):
            current = current.get(segment)
        elif isinstance(current, list) and segment.isdigit():
            index = int(segment)
            current = current[index] if index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def flatten_items(value: Any) -> list[dict[str, Any]]:
    """把「列表」或「字典套列表」统一压平成字典列表。"""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        collected: list[dict[str, Any]] = []
        for sub in value.values():
            if isinstance(sub, list):
                collected.extend(item for item in sub if isinstance(item, dict))
            elif isinstance(sub, dict):
                collected.append(sub)
        return collected
    return []


def clean_text(value: Any) -> str:
    """清理站点返回的标题：反转义 HTML 实体、去掉零宽字符。"""
    text = html.unescape(str(value or ""))
    for char in ("\u200b", "\u200e", "\u200f", "\ufeff"):
        text = text.replace(char, "")
    return text.strip()


RESOURCE_KINDS = {"torrent", "magnet", "pan", "direct", "webvideo"}


def guess_kind(link: str, declared: str | None = None) -> str:
    """根据链接推断资源类型。

    ``declared`` 是站点配置里的 ``kind`` 字段（ProviderKind），
    不是 ResourceKind。两者名字相同但值域不同，ProviderKind 的值
    （"indexer"、"pan"）在 ResourceKind 里没有对应项。

    如果直接 ``if declared: return declared``，站点配置了
    ``kind="indexer"`` 的 BT 站会**把所有磁力标成 kind=indexer**，
    下载路由查不到这个 ResourceKind 而回退到 torrent 兜底（ADR-82）。
    因此只接受合法的 ResourceKind 值。
    """
    if declared and declared in RESOURCE_KINDS:
        return declared
    lowered = link.lower()
    if lowered.startswith("magnet:"):
        return ResourceKind.MAGNET.value
    if ".torrent" in lowered or "/download" in lowered or "/down?" in lowered:
        return ResourceKind.TORRENT.value
    if lowered.startswith("http") and any(
        token in lowered
        for token in ("pan.", "cloud.", "aliyundrive", "alipan", "quark",
                      "123pan", "123684", "189.cn", "lanzou", "115.com", "yunpan")
    ):
        return ResourceKind.PAN.value
    return ResourceKind.TORRENT.value


@register
class GenericApiIndexer(SearchProvider):
    """字段映射式 JSON API 索引器（自定义资源站通用适配器）。"""

    name = "api_generic"
    kind = ProviderKind.INDEXER.value
    display_name = "自定义 JSON API 站点"

    # ---------------- 基础设施 ----------------
    def _base(self) -> str:
        base = str(self.option("api_base", "") or self.config.get("url") or "").strip()
        return base.rstrip("/")

    def _endpoint(self, path: str) -> str:
        """拼接接口地址；``path`` 已是绝对地址时直接用。"""
        path = str(path or "").strip()
        if path.startswith("http://") or path.startswith("https://"):
            return path
        base = self._base()
        if not base:
            return ""
        if not path:
            return base
        return f"{base}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        headers = dict(self.option("headers", {}) or {})
        if self.config.get("api_key") and self.option("auth_header", True):
            headers.setdefault("Authorization", f"Bearer {self.config['api_key']}")
        if self.config.get("cookie"):
            headers.setdefault("Cookie", str(self.config["cookie"]))
        return headers

    def _fixed_params(self) -> dict[str, Any]:
        params = dict(self.option("fixed_params", {}) or {})
        # 允许把 api_key 作为固定 query 参数注入（部分站点用 ?apikey=）
        key_param = self.option("api_key_param")
        if key_param and self.config.get("api_key"):
            params[str(key_param)] = self.config["api_key"]
        return params

    async def _request(self, path: str, params: dict[str, Any]) -> Any:
        """发起一次 JSON 请求并校验站点自定义的成功标记。"""
        url = self._endpoint(path)
        if not url:
            return None
        method = str(self.option("method", "GET")).upper()
        merged = {**self._fixed_params(), **params}
        payload = await fetch_json(
            url,
            method=method,
            params=merged if method == "GET" else None,
            json_body=merged if method != "GET" else None,
            headers=self._headers(),
            timeout=self.config.get("timeout"),
        )
        if payload is None:
            return None
        return payload if self._is_success(payload) else None

    def _is_success(self, payload: Any) -> bool:
        """按配置判定响应是否成功（默认宽松放行）。"""
        if not isinstance(payload, dict):
            return True
        flag_key = self.option("success_key")
        if flag_key:
            expected = self.option("success_value", True)
            actual = dig(payload, str(flag_key))
            if str(actual).lower() != str(expected).lower():
                logger.warning(
                    "站点 %s 返回失败标记 %s=%s（message=%s）",
                    self.site_name, flag_key, actual,
                    dig(payload, str(self.option("message_key", "message"))),
                )
                return False
        return True

    # ---------------- 资源构建 ----------------
    def _build_resource(
        self,
        item: dict[str, Any],
        field_map: dict[str, str],
        *,
        kind: str | None = None,
        fallback_title: str = "",
        site_suffix: str = "",
        extra: dict[str, Any] | None = None,
    ) -> Resource | None:
        """按字段映射把一个 JSON 项转成 Resource。"""
        link = clean_text(dig(item, field_map.get("link", "link")))
        if not link:
            return None
        if link.startswith("/"):
            # 站点常返回相对下载路径
            link = urljoin(self._base() + "/", link.lstrip("/"))

        title = clean_text(dig(item, field_map.get("title", "title"))) or fallback_title
        if not title:
            return None

        published = dig(item, field_map.get("publish_at", "publish_at"))
        published_text = clean_text(published)
        # "38分钟前" 这类相对时间无法解析，交给站点排序即可
        publish_at = (
            None if _RELATIVE_TIME.match(published_text) else parse_datetime(published_text)
        )

        resource_kind = guess_kind(link, kind)
        site_name = f"{self.site_name}·{site_suffix}" if site_suffix else self.site_name
        page_url = clean_text(dig(item, field_map.get("page_url", "page_url")))
        if page_url.startswith("/"):
            page_url = urljoin(self._site_root(), page_url.lstrip("/"))

        return Resource(
            title=title,
            link=link,
            site=site_name,
            kind=resource_kind,
            page_url=page_url or None,
            description=clean_text(dig(item, field_map.get("description", "description")))[:500]
            or None,
            size=parse_size(dig(item, field_map.get("size", "size"))),
            seeders=_as_int(dig(item, field_map.get("seeders", "seeders"))),
            leechers=_as_int(dig(item, field_map.get("leechers", "leechers"))),
            publish_at=publish_at,
            priority=self.priority,
            password=clean_text(dig(item, field_map.get("password", "password"))) or None,
            extra={"provider": self.name, **(extra or {})},
        )

    def _media_meta(self, item: dict[str, Any]) -> dict[str, Any]:
        """从搜索列表项里抽取「作品级」元数据（封面/评分/年份等）。

        这些字段描述的是**作品**，而不是某个具体种子，因此挂在 Resource.extra 上
        由榜单聚合时提取。站点没有对应字段就自然缺省，不影响资源本身可用。
        """
        field_map = {**DEFAULT_MEDIA_MAP, **(self.option("media_map", {}) or {})}
        meta: dict[str, Any] = {}

        poster = clean_text(dig(item, field_map.get("poster", "")))
        if poster:
            if poster.startswith("/"):
                poster = urljoin(self._site_root(), poster.lstrip("/"))
            # 只收 http(s) 图，避免把 data: 或垃圾值塞进前端 img src
            if poster.startswith(("http://", "https://")):
                meta["poster"] = poster

        rating = clean_text(dig(item, field_map.get("rating", "")))
        try:
            # 站点常用 "0" / "" / "暂无" 表示没有评分，这些都不该显示成 0.0 分
            value = float(rating)
            if value > 0:
                meta["rating"] = round(value, 1)
        except (TypeError, ValueError):
            pass

        people = _as_int(dig(item, field_map.get("rating_people", "")))
        if people:
            meta["rating_people"] = people

        for key in ("year", "area", "status_text", "definition", "director"):
            text = clean_text(dig(item, field_map.get(key, "")))
            if text:
                meta[key] = text[:80]

        overview = clean_text(dig(item, field_map.get("overview", "")))
        if overview:
            meta["overview"] = overview[:400]

        for key in ("genres", "actors", "alias"):
            text = clean_text(dig(item, field_map.get(key, "")))
            if text:
                # 站点用逗号/斜杠/顿号分隔，统一切成列表供前端画标签
                parts = [p.strip() for p in re.split(r"[,，/、|]+", text) if p.strip()]
                if parts:
                    meta[key] = parts[:12]

        episodes = _as_int(dig(item, field_map.get("total_episodes", "")))
        if episodes:
            meta["total_episodes"] = episodes

        return meta

    def _site_root(self) -> str:
        """站点站点根地址（用于补全相对页面链接）。"""
        root = str(self.option("site_url", "") or self.config.get("url") or "").strip()
        if not root:
            base = self._base()
            match = re.match(r"^(https?://[^/]+)", base)
            root = match.group(1) if match else base
        return root.rstrip("/") + "/"

    # ---------------- 搜索 ----------------
    def _page_params(self, page: int) -> dict[str, Any]:
        params: dict[str, Any] = {}
        page_key = self.option("page_key")
        if page_key:
            params[str(page_key)] = int(page) + int(self.option("page_base", 1))
        limit_key = self.option("limit_key")
        if limit_key:
            params[str(limit_key)] = int(self.option("limit", 20))
        return params

    async def search(
        self,
        keyword: str,
        *,
        media_type: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        page: int = 0,
    ) -> list[Resource]:
        search_path = str(self.option("search_path", "") or "")
        query_key = str(self.option("query_key", "keyword"))
        if not keyword or not self._base():
            return []

        params = {query_key: keyword, **self._page_params(page)}
        media_param = self.option("media_type_param")
        if media_param and media_type:
            mapping = dict(self.option("media_type_map", {}) or {})
            if media_type in mapping:
                params[str(media_param)] = mapping[media_type]

        payload = await self._request(search_path, params)
        if payload is None:
            return []

        items = flatten_items(dig(payload, str(self.option("list_path", "data"))))
        if not items:
            return []

        item_map = {**DEFAULT_ITEM_MAP, **(self.option("item_map", {}) or {})}
        max_items = int(self.option("max_detail_items", 5))

        # 形态一：列表项自带下载链接
        direct = [
            resource
            for item in items
            if (resource := self._build_resource(item, item_map)) is not None
        ]

        # 形态二：需要二次请求详情才能拿到链接
        detail_path = str(self.option("detail_path", "") or "")
        if not detail_path:
            return direct

        candidates = self._rank_candidates(items, item_map, keyword)[:max_items]
        detail_results = await asyncio.gather(
            *(self._fetch_detail(item, item_map) for item in candidates),
            return_exceptions=True,
        )
        collected = list(direct)
        for group in detail_results:
            if isinstance(group, BaseException):
                logger.warning("站点 %s 详情抓取异常: %s", self.site_name, group)
                continue
            collected.extend(group)
        return collected

    def _rank_candidates(
        self, items: list[dict[str, Any]], item_map: dict[str, str], keyword: str
    ) -> list[dict[str, Any]]:
        """按标题与关键词的相关性给候选条目排序，避免浪费详情请求。

        站点搜索常返回大量弱相关条目（如搜「沙丘」返回「沙丘战将」），
        详情请求成本高，所以优先请求标题最贴近的条目。
        """
        target = normalize(keyword).lower()
        if not target:
            return items

        def score(item: dict[str, Any]) -> tuple[int, int]:
            title = normalize(clean_text(dig(item, item_map.get("title", "title")))).lower()
            alias = normalize(clean_text(dig(item, item_map.get("alias", "alias")))).lower()
            if title == target:
                rank = 0
            elif target and target in title:
                rank = 1
            elif target and target in alias:
                rank = 2
            elif title and title in target:
                rank = 3
            else:
                rank = 4
            return rank, len(title)

        return sorted(items, key=score)

    async def _fetch_detail(
        self, item: dict[str, Any], item_map: dict[str, str]
    ) -> list[Resource]:
        """请求条目详情，抽取其中全部资源链接。"""
        detail_id = clean_text(dig(item, item_map.get("detail_id", "id")))
        if not detail_id:
            return []
        detail_path = str(self.option("detail_path", "") or "")
        query_key = str(self.option("detail_query_key", "id"))
        payload = await self._request(detail_path, {query_key: detail_id})
        if payload is None:
            return []

        fallback_title = clean_text(dig(item, item_map.get("title", "title")))
        page_url = clean_text(dig(item, item_map.get("page_url", "page_url")))
        collected: list[Resource] = []
        # 封面/评分等作品级信息只在搜索列表项里，详情返回的是种子列表，
        # 因此在这里一次性算好，挂到该作品的每条资源上
        media_meta = self._media_meta(item)

        rules = self.option("detail_extract", []) or []
        if not rules:
            return []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            entries = flatten_items(dig(payload, str(rule.get("list_path", ""))))
            field_map = {**DEFAULT_ITEM_MAP, **(rule.get("map", {}) or {})}
            limit = int(rule.get("limit", self.option("detail_item_limit", 60)))
            for entry in entries[:limit]:
                resource = self._build_resource(
                    entry,
                    field_map,
                    kind=rule.get("kind"),
                    fallback_title=fallback_title,
                    site_suffix=str(rule.get("label", "") or ""),
                    extra={
                        "detail_id": detail_id,
                        "page_url_hint": page_url,
                        **media_meta,
                    },
                )
                if resource is not None:
                    collected.append(resource)
        return collected

    # ---------------- 最新流（定时追新雷达） ----------------
    async def fetch_latest(self, limit: int = 100) -> list[Resource]:
        """拉取站点「最新发布」列表，用于不依赖关键词的追新巡检。"""
        latest_path = str(self.option("latest_path", "") or "")
        if not latest_path or not self._base():
            return []

        item_map = {**DEFAULT_ITEM_MAP, **(self.option("item_map", {}) or {})}
        latest_map = {**item_map, **(self.option("latest_map", {}) or {})}
        variants = self.option("latest_params", [{}]) or [{}]
        pages = max(int(self.option("latest_pages", 1)), 1)

        requests: list[dict[str, Any]] = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            for page in range(pages):
                requests.append({**variant, **self._page_params(page)})

        payloads = await asyncio.gather(
            *(self._request(latest_path, params) for params in requests),
            return_exceptions=True,
        )

        collected: list[Resource] = []
        list_path = str(self.option("latest_list_path", self.option("list_path", "data")))
        for payload in payloads:
            if isinstance(payload, BaseException) or payload is None:
                continue
            for entry in flatten_items(dig(payload, list_path)):
                resource = self._build_resource(entry, latest_map)
                if resource is not None:
                    collected.append(resource)
                if len(collected) >= limit:
                    return collected
        return collected

    # ---------------- 健康检查 ----------------
    async def health_check(self) -> tuple[bool, str]:
        if not self._base():
            return False, "未配置 api_base 或 url"
        if not self.option("search_path") and not self.option("latest_path"):
            return False, "未配置 search_path / latest_path"

        probe = str(self.option("health_keyword", "") or "")
        if probe and self.option("search_path"):
            results = await self.search(probe)
            if results:
                return True, f"搜索连通，返回 {len(results)} 条资源"
            return False, f"搜索「{probe}」未返回资源，请检查字段映射"

        if self.option("latest_path"):
            latest = await self.fetch_latest(limit=5)
            if latest:
                return True, f"最新流连通，返回 {len(latest)} 条资源"
            return False, "最新流未返回资源，请检查字段映射"
        return True, "配置已就绪（未设置 health_keyword，跳过实测）"


def _as_int(value: Any) -> int:
    """宽松地把任意值转为整数。"""
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return int(value)
    digits = re.sub(r"[^\d]", "", str(value))
    return int(digits) if digits else 0
