"""awesome-zhuiju-free 资源清单接入（社区维护的追剧站点目录）。

**为什么接这个项目**：CineFlow 的站点接入一直是「用户自己找站 + 自己写选择器」，
v1.13.0 的 AI 分析只能减少写选择器的时间，站点从哪来仍是空白。
[awesome-zhuiju-free](https://github.com/laoma2053/awesome-zhuiju-free)
（CC-BY-4.0，7.1k star）用**机器可读的 `resources.json`**（有 JSON Schema）
维护 114 个追剧资源，并且每天用 GitHub Actions 跑一次可用性检测，
这正好补上「站点从哪来」——这也是竞品 Jackett 走的社区共享定义路线（见 docs/09 §11.3）。

**⚠️ 本模块最重要的一个判断：上游的 `reachable` 不等于「能搜到东西」。**

实测上游 `reports/availability.json` 的检测方式是 `GET 首页` 看 HTTP 状态码，
于是出现大量「首页 200 但搜索完全不可用」的情况——这正是 ADR-20 说的那类
最难发现的故障。逐站实测 11 个 magnet_search 站点的搜索接口后：

* `btbtla` / `btsj6` / `dyg22` / `pomo` / `ainunu` / `cld123`：首页 200，
  搜索页 200，但**磁力数为 0**（搜索结果不含链接，详情页也没有）
* `cilixiong`：搜索是 EmpireCMS `POST /e/search/index.php`，且**限流 5 秒**；
  详情页确有磁力（实测 1~4 条/页），但站内片库与主流片名重合度低
* `acg.rip`：只有 `.torrent`，且是动漫专站（我们已有 `nyaa` 覆盖同一场景）
* `kkso.net`：**唯一实测可直接产出可用链接的站**——搜索页自带夸克/百度分享链

所以本模块**不把上游清单直接变成启用中的站点**，而是：

1. 只把清单当**候选目录**（等价于「站点发现」的一个新来源），
2. 用**我们自己的搜索探测**（`probe_site`）判定「能不能真的搜到资源」，
3. 只有 `searchable` 档才给「一键添加」，其余如实标注原因。

宁可给可信的少数，也不要把 20 个「首页能开但搜不到」的站塞进用户的搜索链路——
那只会让每次搜索多等几秒然后返回 0 条（正是 v1.13.0 刚修完的那类问题）。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.core.config import settings
from app.core.logger import get_logger
from app.utils.http import async_client, fetch_text
from app.utils.strings import truncate

logger = get_logger(__name__)

#: 上游仓库（CC-BY-4.0，需署名）
UPSTREAM_REPO = "laoma2053/awesome-zhuiju-free"
UPSTREAM_URL = f"https://github.com/{UPSTREAM_REPO}"
UPSTREAM_LICENSE = "CC-BY-4.0"
UPSTREAM_SITE = "https://zhuiju.me"

#: 数据源（raw 直连，无需 token；jsDelivr 作为镜像兜底）
_BASES = [
    f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/main/",
    f"https://cdn.jsdelivr.net/gh/{UPSTREAM_REPO}@main/",
]
RESOURCES_PATH = "resources/resources.json"
AVAILABILITY_PATH = "reports/availability.json"

#: 我们只关心「能用来找片源」的两类，其余（在线播放/TVBox/IPTV/字幕…）不进搜索链路
SEARCHABLE_CATEGORIES = ("magnet_search", "cloud_search")

#: 本地缓存（离线可用 + 避免每次开页面都打上游）
CACHE_FILE = settings.DATA_DIR / "zhuiju_catalog.json"
#: 缓存有效期：上游每天 01:00 (UTC+8) 跑一次检测，12 小时足够
CACHE_TTL = 12 * 3600

#: 网盘分享链接特征（判定 cloud_search 站是否真的产出可转存链接）
PAN_LINK_RE = re.compile(
    r"https?://(?:pan\.quark\.cn/s/|pan\.baidu\.com/s/|www\.(?:alipan|aliyundrive)\.com/s/"
    r"|drive\.uc\.cn/s/|(?:115|115cdn)\.com/s/|pan\.xunlei\.com/s/|cloud\.189\.cn/t/"
    r"|caiyun\.139\.com/m/)[0-9a-zA-Z_\-]+"
)
MAGNET_RE = re.compile(r"magnet:\?xt=urn:btih:[0-9a-zA-Z]{32,40}")
TORRENT_RE = re.compile(r'href="([^"]+\.torrent[^"]*)"', re.I)


@dataclass
class CatalogEntry:
    """上游清单里的一个候选站点（已折叠成我们关心的字段）。"""

    id: str
    name: str
    url: str
    category: str
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    requires_login: bool = False
    #: 上游人工验证结论：recommended / caution
    upstream_status: str = "unknown"
    upstream_checked: str = ""
    #: 上游每日探测：reachable / restricted / unreachable
    reachability: str = "unknown"
    http_status: int | None = None
    #: 我们自己的判定（见模块 docstring）：searchable / reachable_only / blocked / unknown
    probe: str = "unknown"
    probe_note: str = ""
    #: 命中我们已有站点时给出 provider 名，避免重复添加
    known_provider: str | None = None

    @property
    def domain(self) -> str:
        try:
            return urlparse(self.url).netloc.lower().replace("www.", "")
        except ValueError:
            return ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["domain"] = self.domain
        data["category_label"] = CATEGORY_LABELS.get(self.category, self.category)
        return data


CATEGORY_LABELS = {
    "magnet_search": "磁力 / BT",
    "cloud_search": "网盘搜索",
    "online_video": "在线影视",
    "video_app": "影视 APP",
    "subtitles": "字幕",
    "player": "播放器 / 空壳",
    "subscription": "IPTV / 订阅源",
    "tvbox_config": "TVBox 配置",
    "membership": "会员拼团",
    "open_source": "开源项目",
    "other": "其他",
}

#: 域名 -> 我们已内置的 provider（避免让用户重复添加已有站点）
KNOWN_DOMAINS = {
    "pansou.de": "pansou",
    "kkso.net": "kkso",
    "acg.rip": "nyaa",
    "bdflixs.com": "wp_film",
    "mjf2020.com": "wp_film",
    "juhebd.com": "html_generic",
    "hdzu.org": "html_generic",
    "ldysg.win": "api_generic",
    "cilixiong.org": "cilixiong",
}


async def _fetch_json(path: str) -> Any:
    """从 raw / jsDelivr 依次尝试拉取 JSON。"""
    for base in _BASES:
        text = await fetch_text(base + path, timeout=30)
        if not text:
            continue
        try:
            return json.loads(text)
        except (ValueError, TypeError) as exc:
            logger.warning("zhuiju 清单解析失败 %s: %s", base + path, exc)
    return None


def _load_cache() -> dict[str, Any] | None:
    if not CACHE_FILE.exists():
        return None
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("zhuiju 缓存读取失败: %s", exc)
        return None


def _save_cache(payload: dict[str, Any]) -> None:
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("zhuiju 缓存写入失败: %s", exc)


def _merge(resources: Any, availability: Any) -> list[CatalogEntry]:
    """把 resources.json 与 availability.json 合并成候选清单。"""
    avail: dict[str, dict[str, Any]] = {}
    if isinstance(availability, dict):
        for row in availability.get("results") or []:
            if isinstance(row, dict) and row.get("resource_id"):
                avail[str(row["resource_id"])] = row

    items = (resources or {}).get("resources") if isinstance(resources, dict) else None
    entries: list[CatalogEntry] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or "")
        if category not in SEARCHABLE_CATEGORIES:
            continue
        rid = str(raw.get("id") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not rid or not url.startswith("http"):
            continue
        access = raw.get("access") if isinstance(raw.get("access"), dict) else {}
        verify = raw.get("verification") if isinstance(raw.get("verification"), dict) else {}
        probe = avail.get(rid) or {}
        entry = CatalogEntry(
            id=rid,
            name=str(raw.get("name") or rid),
            url=url,
            category=category,
            summary=truncate(str(raw.get("summary_short") or raw.get("summary") or ""), 120),
            tags=[str(t) for t in (raw.get("tags") or []) if t][:8],
            requires_login=bool(access.get("requires_login")),
            upstream_status=str(verify.get("status") or "unknown"),
            upstream_checked=str(verify.get("last_checked") or ""),
            reachability=str(probe.get("status") or "unknown"),
            http_status=probe.get("http_status") if isinstance(probe.get("http_status"), int) else None,
        )
        entry.known_provider = KNOWN_DOMAINS.get(entry.domain)
        entries.append(entry)
    entries.sort(key=lambda e: (e.category, e.id))
    return entries


async def refresh(force: bool = False) -> dict[str, Any]:
    """拉取（或复用缓存）上游清单。

    返回 ``{"updated_at", "upstream_updated_at", "count", "entries", "stale"}``。
    拉取失败时**回退到缓存**并标 ``stale=True``：清单是辅助功能，
    不该因为上游/网络抖动就让站点管理页开不出来。
    """
    cache = _load_cache()
    if not force and cache and time.time() - float(cache.get("fetched_at") or 0) < CACHE_TTL:
        return {**cache, "from_cache": True, "stale": False}

    resources = await _fetch_json(RESOURCES_PATH)
    availability = await _fetch_json(AVAILABILITY_PATH)
    if not resources:
        if cache:
            logger.warning("zhuiju 清单拉取失败，回退缓存")
            return {**cache, "from_cache": True, "stale": True}
        return {
            "fetched_at": 0, "updated_at": "", "upstream_updated_at": "",
            "count": 0, "entries": [], "from_cache": False, "stale": True,
            "error": "无法拉取上游清单（网络不可达或上游变更）",
        }

    entries = _merge(resources, availability)
    # 保留上一轮的探测结论：探测要打真实站点，不能每次刷新清单都跑一遍
    old = {e.get("id"): e for e in (cache or {}).get("entries") or []}
    for entry in entries:
        prev = old.get(entry.id)
        if prev and prev.get("probe") not in (None, "", "unknown"):
            entry.probe = str(prev.get("probe"))
            entry.probe_note = str(prev.get("probe_note") or "")

    payload = {
        "fetched_at": time.time(),
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "upstream_updated_at": str((resources or {}).get("updated_at") or ""),
        "upstream_generated_at": str((availability or {}).get("generated_at") or ""),
        "count": len(entries),
        "entries": [e.to_dict() for e in entries],
    }
    _save_cache(payload)
    return {**payload, "from_cache": False, "stale": False}


def load() -> dict[str, Any]:
    """只读缓存（不联网），供接口快速返回。"""
    cache = _load_cache()
    if not cache:
        return {"count": 0, "entries": [], "updated_at": "", "stale": True}
    return {**cache, "stale": time.time() - float(cache.get("fetched_at") or 0) >= CACHE_TTL}


def stats(entries: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """按我们自己的探测结论汇总（前端展示用）。"""
    rows = entries if entries is not None else load().get("entries") or []
    out: dict[str, int] = {"total": len(rows), "searchable": 0,
                           "reachable_only": 0, "blocked": 0, "unknown": 0}
    for row in rows:
        key = str(row.get("probe") or "unknown")
        out[key] = out.get(key, 0) + 1
    return out
async def _get_with_status(url: str, *, timeout: float) -> tuple[str | None, int | None]:
    """取正文**并保留状态码**。

    不能用 `utils.http.fetch_text`：它内部 `raise_for_status()` 后统一返回
    ``None``，于是「403 被反爬拦」与「域名根本连不上」在调用侧完全无法区分，
    而这个区别正是探测结论要给用户的信息（前者标 blocked 并说明不做对抗，
    后者是站点已死）。这里直接用共享客户端，自己拿 status。
    """
    try:
        async with async_client(timeout=timeout) as client:
            response = await client.get(url)
            return response.text, response.status_code
    except Exception as exc:
        logger.debug("zhuiju 探测请求失败 %s: %s", url, exc)
        return None, None


#: 站点搜索地址猜测顺序（覆盖实测到的几种主流形态）
_SEARCH_SHAPES = [
    "{base}/s/{kw}?p=1",          # kkso 系
    "{base}/?s={kw}",             # WordPress
    "{base}/search?q={kw}",
    "{base}/search/{kw}",
    "{base}/index.php/vod/search.html?wd={kw}",  # MacCMS
]

#: 探测用关键词：取三个「几乎所有中文资源站都该有」的片名，
#: 避免用生僻词把「站点没这部片」误判成「站点不可用」（ADR-20 同一教训）。
PROBE_KEYWORDS = ("庆余年", "凡人修仙传", "流浪地球")


async def probe_site(url: str, *, keywords: tuple[str, ...] = PROBE_KEYWORDS,
                     timeout: float = 15.0) -> dict[str, Any]:
    """真搜一次，判定站点能否产出**可下载的链接**。

    这是本模块存在的理由：上游 `availability.json` 只 `GET 首页` 看状态码，
    「首页 200 / 搜索 0 结果」会被标成 `reachable`。实测 11 个磁力站里有 6 个
    属于这种情况，若直接信上游就会把它们塞进搜索链路，让每次搜索白等几秒。

    判定四档：

    * ``searchable``      —— 搜到了磁力/种子/网盘链接，可以真用
    * ``reachable_only``  —— 页面能打开但搜不到任何可下载链接
    * ``blocked``         —— 403/468/WAF/超时，直连不可用
    * ``unknown``         —— 地址不合法等

    只在**用户显式点探测**或定时任务里调用，不在页面渲染路径上跑。
    """
    base = str(url or "").strip().rstrip("/")
    if not base.startswith("http"):
        return {"probe": "unknown", "probe_note": "地址不合法"}

    best: dict[str, Any] = {"probe": "unknown", "probe_note": "未拿到响应"}
    blocked_codes: list[int] = []
    reached = False

    for keyword in keywords:
        for shape in _SEARCH_SHAPES:
            target = shape.format(base=base, kw=keyword)
            text, status = await _get_with_status(target, timeout=timeout)
            if status and status >= 400:
                # 403/468 这类是反爬/WAF，记下来但继续试其它形态
                if status in (401, 403, 406, 429, 468, 503):
                    blocked_codes.append(status)
                continue
            if not text:
                continue
            reached = True
            magnets = len(set(MAGNET_RE.findall(text)))
            pans = len(set(PAN_LINK_RE.findall(text)))
            torrents = len(set(TORRENT_RE.findall(text)))
            if magnets or pans or torrents:
                parts = []
                if magnets:
                    parts.append(f"磁力 {magnets}")
                if pans:
                    parts.append(f"网盘 {pans}")
                if torrents:
                    parts.append(f"种子 {torrents}")
                return {
                    "probe": "searchable",
                    "probe_note": f"「{keyword}」搜到 {' / '.join(parts)}",
                    "search_url": shape.format(base=base, kw="{keyword}"),
                    "magnets": magnets, "pan_links": pans, "torrents": torrents,
                }
            # 关键词出现在页面里说明搜索确实生效了，只是不带链接（多半在详情页）
            if keyword in text:
                best = {
                    "probe": "reachable_only",
                    "probe_note": "搜索页有结果但页面内无可下载链接"
                                  "（可能在详情页，需自行配置正则）",
                    "search_url": shape.format(base=base, kw="{keyword}"),
                }

    if best.get("probe") == "reachable_only":
        return best
    if reached:
        return {"probe": "reachable_only", "probe_note": "页面可访问但未搜到任何可下载链接"}
    if blocked_codes:
        codes = "/".join(str(c) for c in sorted(set(blocked_codes)))
        return {"probe": "blocked", "probe_note": f"被拦截（HTTP {codes}），本项目不做反爬对抗"}
    return {"probe": "blocked", "probe_note": "连接超时或被重置"}


async def probe_all(limit: int = 0, only_unknown: bool = False) -> dict[str, Any]:
    """批量探测清单里的候选站点，结论写回缓存。

    串行执行并留间隔：这些是别人的站，且实测 `cilixiong` 有 5 秒搜索限流，
    并发打过去只会被判定成不可用（而且不礼貌）。
    """
    data = load()
    entries = list(data.get("entries") or [])
    if not entries:
        data = await refresh()
        entries = list(data.get("entries") or [])
    targets = [e for e in entries
               if not only_unknown or str(e.get("probe") or "unknown") == "unknown"]
    if limit > 0:
        targets = targets[:limit]

    for entry in targets:
        try:
            result = await probe_site(str(entry.get("url") or ""))
        except Exception as exc:  # 单站失败不能拖垮整批
            logger.warning("zhuiju 探测失败 %s: %s", entry.get("id"), exc)
            result = {"probe": "unknown", "probe_note": f"探测异常：{type(exc).__name__}"}
        entry.update({k: v for k, v in result.items() if k in ("probe", "probe_note")})
        if result.get("search_url"):
            entry["search_url"] = result["search_url"]

    payload = {**data, "entries": entries, "probed_at": datetime.now(UTC).isoformat(timespec="seconds")}
    payload.pop("stale", None)
    payload.pop("from_cache", None)
    _save_cache(payload)
    logger.info("zhuiju 站点探测完成：%d 个，%s", len(targets), stats(entries))
    return {"probed": len(targets), "stats": stats(entries),
            "probed_at": payload["probed_at"]}


def suggest_site(entry_id: str) -> dict[str, Any] | None:
    """把一个 `searchable` 候选转成可直接落库的站点配置建议。

    只给 `searchable` 的：`reachable_only` 的站配上去也搜不到东西，
    给出来等于给用户一个「加了但没用」的坑（v1.13.0 AI 接站同一立场）。
    """
    for row in load().get("entries") or []:
        if str(row.get("id")) != str(entry_id):
            continue
        if str(row.get("probe")) != "searchable":
            return {"ok": False, "reason": row.get("probe_note")
                    or "该站点未通过本地搜索探测，不建议添加"}
        provider = row.get("known_provider")
        domain = str(row.get("domain") or "")
        search_url = str(row.get("search_url") or "")
        if provider == "kkso" or "kkso" in domain:
            return {"ok": True, "provider": "kkso", "kind": "pan",
                    "name": row.get("name"), "url": row.get("url"), "options": {}}
        if provider and provider not in ("kkso",):
            return {"ok": True, "provider": provider, "kind": "indexer",
                    "name": row.get("name"), "url": row.get("url"),
                    "options": {"search_url": search_url} if search_url else {}}
        # 兜底：通用网页站 + 只抓磁力，让用户在此基础上微调
        return {"ok": True, "provider": "html_generic", "kind": "indexer",
                "name": row.get("name"), "url": row.get("url"),
                "options": {"search_url": search_url or f"{row.get('url')}/?s={{keyword}}",
                            "magnet_only": True}}
    return None
async def sync() -> dict[str, Any]:
    """定时任务入口：拉清单（+可选探测）。

    刻意**不自动改动用户的站点表**：清单只是候选目录，是否添加由用户决定。
    自动建站会带来两个问题——上游删站时我们不知道该不该删用户的配置，
    且「首页能开但搜不到」的站会悄悄进入搜索链路拖慢每次搜索（ADR-70）。
    """
    if not settings.ZHUIJU_SYNC_ENABLED:
        return {"skipped": True, "reason": "未启用社区清单同步"}
    data = await refresh(force=True)
    result = {
        "count": data.get("count") or 0,
        "upstream_updated_at": data.get("upstream_updated_at") or "",
        "stale": bool(data.get("stale")),
    }
    if data.get("error"):
        result["error"] = data["error"]
    if settings.ZHUIJU_PROBE_ON_SYNC and not data.get("stale"):
        probed = await probe_all(limit=max(0, int(settings.ZHUIJU_PROBE_LIMIT or 0)))
        result["probe"] = probed
    logger.info("zhuiju 清单同步完成：%s", result)
    return result
