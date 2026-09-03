"""站点发现：从导航站抓取被收录的影视资源站清单。

典型场景是「硬核指南」这类 OneNav 主题的导航站——它们本身不提供
影视资源与磁力链接，只收录其他站点的入口。本模块把这些入口抓出来，
让用户在 CineFlow 里挑选并一键添加为自定义资源站。

OneNav 的站点卡片形如::

    <a href="javascript:" class="card is-views site-3208" data-id="3208"
       data-url="https://uz998.com" title="影视、直播、漫画、小说一站式娱乐APP">
      ... <div class="text-sm overflowClip_1"> 蓝光追剧神器 </div> ...

因此按 ``data-url`` + ``title`` + 卡片内文本即可提取「地址/简介/名称」。
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.core.logger import get_logger
from app.utils.http import fetch_text
from app.utils.strings import truncate

logger = get_logger(__name__)

#: 内置导航站（用户也可传入任意导航站地址）
DEFAULT_DIRECTORIES: list[dict[str, str]] = [
    {
        "name": "硬核指南",
        "url": "https://yinghezhinan.com/?referer_com",
        "note": "收录免费优质影音/二次元/音乐/游戏站点的导航站",
    },
]

# 导航站卡片锚点（属性顺序不固定，先粗筛出整个 <a> 标签再逐属性提取）
_ANCHOR = re.compile(r"<a\b[^>]*\bdata-url=[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z_:-]+)\s*=\s*"([^"]*)"')
# 卡片展示名（OneNav 把站点名放在 overflowClip_1 容器里）
_CARD_NAME = re.compile(
    r"""data-id="(?P<id>\d+)".{0,1500}?overflowClip_1"?\s*>\s*(?P<name>[^<]{1,60})<""",
    re.S | re.I,
)
# 兜底：页面上任意外链
_PLAIN_LINK = re.compile(
    r"""<a[^>]+href="(?P<url>https?://[^"]+)"[^>]*>(?P<name>[^<]{2,60})</a>""", re.I
)

#: 影视/追剧相关关键词（用于给发现结果打「相关性」标记）
MEDIA_HINTS = [
    "影视", "追剧", "电影", "电视剧", "剧集", "动漫", "番剧", "二次元",
    "蓝光", "4K", "高清", "磁力", "种子", "BT", "网盘", "夸克", "资源",
    "片源", "字幕", "纪录片", "综艺", "直播",
]


@dataclass
class DiscoveredSite:
    """导航站收录的一个候选站点。"""

    name: str
    url: str
    description: str = ""
    source: str = ""
    tags: list[str] = field(default_factory=list)
    media_related: bool = False

    @property
    def domain(self) -> str:
        try:
            return urlparse(self.url).netloc.lower()
        except ValueError:
            return ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["domain"] = self.domain
        return data


def _is_media_related(text: str) -> tuple[bool, list[str]]:
    """判断文本是否与影视资源相关，并返回命中的标签。"""
    lowered = text.lower()
    hits = [
        hint
        for hint in MEDIA_HINTS
        if hint.lower() in lowered
    ]
    return bool(hits), hits[:6]


def parse_directory(html_text: str, source: str = "") -> list[DiscoveredSite]:
    """解析导航站页面，提取收录的站点清单。"""
    if not html_text:
        return []

    text = html.unescape(html_text)

    # 先建立 data-id → 展示名 的索引（卡片名在锚点之后的容器里）
    names: dict[str, str] = {}
    for match in _CARD_NAME.finditer(text):
        card_id = match.group("id")
        name = re.sub(r"\s+", " ", match.group("name")).strip()
        if name and card_id not in names:
            names[card_id] = name

    found: dict[str, DiscoveredSite] = {}
    source_host = urlparse(source).netloc.lower() if source.startswith("http") else ""

    for anchor in _ANCHOR.finditer(text):
        attrs = {key.lower(): value for key, value in _ATTR.findall(anchor.group(0))}
        url = (attrs.get("data-url") or "").strip()
        # 有些卡片把真实地址放在 href 上，data-url 为空
        if not url.startswith("http"):
            href = (attrs.get("href") or "").strip()
            url = href if href.startswith("http") else ""
        if not url:
            continue

        host = urlparse(url).netloc.lower()
        if not host or (source_host and host == source_host):
            continue

        desc = re.sub(r"\s+", " ", attrs.get("title") or attrs.get("data-original-title") or "")
        card_id = attrs.get("data-id") or ""
        name = names.get(card_id) or _name_from_desc(desc) or host
        related, tags = _is_media_related(f"{name} {desc}")

        site = DiscoveredSite(
            name=truncate(name, 60),
            url=url,
            description=truncate(desc.strip(), 200),
            source=source,
            tags=tags,
            media_related=related,
        )
        exists = found.get(host)
        # 同域名多次出现时，保留信息更完整的那条
        if exists is None or (not exists.description and site.description):
            found[host] = site

    if not found:
        # 非卡片结构：退化为抓取页面外链
        for match in _PLAIN_LINK.finditer(text):
            url = match.group("url").strip()
            host = urlparse(url).netloc.lower()
            name = re.sub(r"\s+", " ", match.group("name")).strip()
            if not name or not host or (source_host and host == source_host):
                continue
            related, tags = _is_media_related(name)
            found.setdefault(
                host,
                DiscoveredSite(
                    name=truncate(name, 60), url=url, source=source,
                    tags=tags, media_related=related,
                ),
            )

    return list(found.values())


def _name_from_desc(desc: str) -> str:
    """从「站点名：简介」形式的 title 里取出站点名。"""
    for sep in ("：", ":", "-", "—", "|"):
        if sep in desc:
            head = desc.split(sep, 1)[0].strip()
            if 1 < len(head) <= 30:
                return head
    return ""


async def discover(
    url: str | None = None, *, media_only: bool = True, limit: int = 200
) -> dict[str, Any]:
    """抓取导航站并返回候选资源站清单。"""
    targets = (
        [{"name": urlparse(url).netloc or url, "url": url}]
        if url
        else list(DEFAULT_DIRECTORIES)
    )

    collected: list[DiscoveredSite] = []
    errors: list[str] = []
    for target in targets:
        text = await fetch_text(target["url"], timeout=30)
        if not text:
            errors.append(f"{target['name']}：页面抓取失败")
            continue
        items = parse_directory(text, source=target["name"])
        logger.info("导航站 %s 解析出 %d 个站点", target["name"], len(items))
        collected.extend(items)

    if media_only:
        collected = [item for item in collected if item.media_related]
    collected.sort(key=lambda item: (not item.media_related, item.name))

    return {
        "total": len(collected),
        "sites": [item.to_dict() for item in collected[:limit]],
        "errors": errors,
        "directories": [item["url"] for item in targets],
    }

# ---------------- 探测与落库建议 ----------------
def _provider_guess(domain: str, search_url: str) -> tuple[str, str, dict[str, Any]]:
    """按域名/探测出的搜索形态猜 provider。

    与 ``zhuiju.suggest_site`` 保持同一套判据，避免两处对同一个站
    给出不同的 provider（那会让用户以为是随机的）。
    """
    if "kkso" in domain:
        return "kkso", "pan", {}
    return (
        "html_generic",
        "indexer",
        {"search_url": search_url, "magnet_only": True} if search_url else {},
    )


#: 交互式探测用的关键词。**刻意只用 1 个**（zhuiju 的定时任务仍用 3 个）：
#: probe_site 是 keywords × 5 种搜索形态的双层循环，每个形态都可能吃满超时。
#: 3 词 × 5 形态 × 15s = **225s 最坏情况** —— 实测真的会跑到 urllib 超时，
#: 而这是挂在界面按钮后面的同步请求，用户只会以为点了没反应。
INTERACTIVE_KEYWORDS = ("凡人修仙传",)

#: 单次交互式探测的墙钟上限。超了就如实说"没探完"，不假装结论。
PROBE_BUDGET_SECONDS = 40.0


async def probe_discovered(
    url: str,
    *,
    timeout: float = 8.0,
    budget: float = PROBE_BUDGET_SECONDS,
) -> dict[str, Any]:
    """对导航站发现的候选站**真搜一次**，判定能不能用。

    直接复用 :func:`app.services.zhuiju.probe_site` —— 导航站候选和社区清单
    候选面临的是同一个问题（「首页 200 ≠ 搜得到」，见 ADR-70），
    没有理由为它们写第二套判据。

    **但不能照搬它的参数**：那个函数是给定时任务用的（3 个关键词、15s 超时、
    串行留间隔），双层循环最坏要 225s。挂在界面按钮后面必须收窄成
    1 个关键词 + 8s 超时，并加一道墙钟上限兜底 —— 超时**如实报 unknown**，
    不猜成 blocked（那会把"我们没探完"说成"站点不可用"，是在替用户下错结论）。

    在这里晚导入是为了避免 ``discovery`` ↔ ``zhuiju`` 循环依赖。
    """
    from app.services import zhuiju as zhuiju_service

    try:
        probe = await asyncio.wait_for(
            zhuiju_service.probe_site(
                url, keywords=INTERACTIVE_KEYWORDS, timeout=timeout
            ),
            timeout=budget,
        )
    except (TimeoutError, asyncio.TimeoutError):
        logger.info("候选站探测超预算（%ss）：%s", budget, url)
        probe = {
            "probe": "unknown",
            "probe_note": f"探测超过 {int(budget)} 秒未完成，站点响应过慢，未能判定",
        }
    domain = urlparse(url).netloc.lower()
    # probe_site 回的 search_url 已含 {keyword} 占位，正是 html_generic 要的格式
    search_url = str(probe.get("search_url") or "")
    provider, kind, options = _provider_guess(domain, search_url)
    return {
        **probe,
        "url": url,
        "domain": domain,
        "provider": provider,
        "kind": kind,
        "options": options,
    }


def suggest_from_probe(
    name: str, url: str, probe: dict[str, Any], *, source: str = ""
) -> dict[str, Any]:
    """把一次探测结果转成可直接落库的站点配置。

    只放行 ``searchable``：``reachable_only`` 的站加进来也搜不到东西，
    等于给用户一个「加了但没用」的坑 —— 与 ADR-70 / zhuiju 同一立场。
    """
    if str(probe.get("probe")) != "searchable":
        return {
            "ok": False,
            "reason": str(probe.get("probe_note") or "该站点未通过本地搜索探测，不建议添加"),
        }
    return {
        "ok": True,
        "name": truncate(name or probe.get("domain") or url, 60),
        "url": url,
        "provider": str(probe.get("provider") or "html_generic"),
        "kind": str(probe.get("kind") or "indexer"),
        "options": {
            **(probe.get("options") or {}),
            "note": f"由导航站发现并通过本地搜索探测{('（' + source + '）') if source else ''}",
        },
        "probe_note": probe.get("probe_note"),
    }
