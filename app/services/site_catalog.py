"""BT / 磁力站点与 RSS 追新源的**实测预设**清单。

**为什么单独一个模块**：`presets.py` 放的是「字段映射模板」（教用户怎么填），
本模块放的是「已经实测跑通、可以直接落库的具体站点」。两者的验收标准不同：
模板只要字段说明正确，具体站点必须**当场搜出真实资源**才能进来。

## 收录标准（v1.19.0 实测，关键词「凡人修仙传」「沙丘」「流浪地球」）

只有**真的返回了可用下载链接**的站点才进这个清单。实测被**淘汰**的候选：

| 站点 | 实测结果 | 淘汰原因 |
|---|---|---|
| BD影视聚合 juhebd | 搜索页 45 条 `/mv/xxxx`，详情页磁力 **0** | 在线播放站，无下载源 |
| 美剧粉 mjf2020 | 搜索页 115 条 `/jianjie/N.html`，详情页磁力 **0** | 同上 |
| 高清族 hdzu / 高清MP4 mp4ba | HTTP **403** | 反爬拒绝 |
| SeedHub sidhub | HTTP **403** | 同上 |
| 1337x / clmclm | HTTP **403** | 同上 |
| TGx / cilibao | ConnectError | 域名不可达 |
| BTSOW | 302 跳转到 tellme.pw 广告页 | 已失效 |
| BTNull | TLS DECRYPTION_FAILED | 证书/连接异常 |

**把淘汰名单写进代码注释是刻意的**：下次有人想「再加几个站」时，
不必把这些坑重新踩一遍；也说明这份清单短不是因为没找，而是筛过。
"""

from __future__ import annotations

from typing import Any

from app.schemas.enums import ProviderKind

#: 已实测可用的磁力/BT 搜索站点。
#:
#: ``measured`` 字段记录实测产出，是这份清单的**验收凭据**；
#: ``caveat`` 如实写明缺陷，避免用户以为是自己配错了。
BT_SITE_PRESETS: list[dict[str, Any]] = [
    {
        "id": "dmhy_search",
        "name": "动漫花园 dmhy（搜索）",
        "kind": ProviderKind.INDEXER.value,
        "provider": "html_generic",
        "url": "https://share.dmhy.org",
        "priority": 18,
        "description": "华语圈最大的动漫资源站，搜索页直出磁力链与体积，"
                       "无需二段抓取，速度快、元数据完整。动漫/日剧首选。",
        "measured": "凡人修仙传 60 条、沙丘 30 条，体积解析正常",
        "caveat": "以动漫为主，欧美电影命中率低；非动漫题材建议配合其他站",
        "verified": True,
        "options": {
            "search_url": "https://share.dmhy.org/topics/list?keyword={keyword}",
            "row_pattern": r"<tr[^>]*>(.*?)</tr>",
            "field_patterns": {
                "title": r'<a href="/topics/view/[^"]+"[^>]*>(?:\s*<span[^>]*>[^<]*</span>\s*)?(.*?)</a>',
                "link": r'href="(magnet:\?xt=urn:btih:[^"]+)"',
                "page_url": r'href="(/topics/view/[^"]+)"',
                "size": r"<td[^>]*>\s*([\d.]+\s*[KMGT]B)\s*</td>",
            },
            # 站点已按关键词过滤过，再本地过滤会因为它给标题插高亮空格而误杀
            "local_filter": False,
            "max_rows": 60,
            "note": "搜索页直出磁力。标题里关键词两侧的空格来自站点的高亮 "
                    "<span>，strip_tags 后是正常现象，不影响订阅匹配（已实测）",
        },
    },
    {
        "id": "bdflixs",
        "name": "BD电影首发站",
        "kind": ProviderKind.INDEXER.value,
        "provider": "html_generic",
        "url": "https://www.bdflixs.com",
        "priority": 35,
        "description": "电影为主，详情页内含大量磁力（单页可达 200+ 条），"
                       "适合找同一部片的多种画质版本。",
        "measured": "沙丘 582 条、流浪地球 303 条磁力",
        "caveat": "⚠️ 磁力只在详情页且不带体积/做种数，因此 size 恒为 0、"
                  "同一部片的多条磁力共用详情页标题——能下，但列表里"
                  "分不出画质，需要点进详情页确认",
        "verified": True,
        "options": {
            "search_url": "https://www.bdflixs.com/?s={keyword}",
            "detail_link_field": r'href="(https?://www\.bdflixs\.com/\d+\.html)"',
            "max_detail_items": 3,
            "magnet_only": True,
            "local_filter": False,
            "note": "两段抓取：搜索页取 /N.html 详情页，再从详情页正文抠磁力",
        },
    },
    {
        "id": "cilixiong",
        "name": "磁力熊 Cilixiong",
        "kind": ProviderKind.INDEXER.value,
        "provider": "html_generic",
        "url": "https://www.cilixiong.org",
        "priority": 40,
        "description": "电影/剧集，走 EmpireCMS 的 POST 搜索，详情页取磁力。",
        "measured": "流浪地球 3 部命中，逐部 4~8 条真实磁力",
        "caveat": "⚠️ 只收电影与剧集（classid=1,2），动漫番剧搜不到；"
                  "搜索必须走 POST——GET ?s= 会静默返回首页，"
                  "导致任何关键词都返回同一批结果",
        "verified": True,
        "options": {
            "search_url": "https://www.cilixiong.org/e/search/index.php",
            "search_method": "POST",
            "search_data": {
                "keyboard": "{keyword}",
                "classid": "1,2",
                "show": "title",
                "tempid": "1",
            },
            "detail_link_field": r'href="(/(?:movie|tv)/\d+\.html)"',
            "max_detail_items": 6,
            "local_filter": False,
        },
    },
]

#: 已实测可用的 RSS 追新源。
#:
#: RSS 是「更新快」的关键：搜索是用户主动发起的，RSS 由调度器定时拉，
#: 新资源发布后最快一个巡检周期（默认 20 分钟）就能进库并触发下载。
RSS_FEED_PRESETS: list[dict[str, Any]] = [
    {
        "id": "mikan_classic",
        "name": "蜜柑计划 · 全站最新",
        "url": "https://mikanani.me/RSS/Classic",
        "dialect": "mikan",
        "aggregate": True,
        "measured": "200 状态码，100 个 item",
        "description": "番剧追新首选。聚合流，务必配合订阅过滤或标题包含规则，"
                       "否则会把全站新番都拉进来。",
    },
    {
        "id": "dmhy_rss",
        "name": "动漫花园 · 全站最新",
        "url": "https://share.dmhy.org/topics/rss/rss.xml",
        "dialect": "dmhy",
        "aggregate": True,
        "measured": "200 状态码，500 个 item",
        "description": "更新量最大的中文动漫源（单次 500 条），磁力直出。",
    },
    {
        "id": "nyaa_anime",
        "name": "Nyaa · 动画分类",
        "url": "https://nyaa.si/?page=rss&c=1_2&f=0",
        "dialect": "nyaa",
        "aggregate": True,
        "measured": "200 状态码，75 个 item",
        "description": "英文圈动漫源，**带做种数**，适合按健康度筛选。",
    },
    {
        "id": "acgrip",
        "name": "ACG.RIP · 全站最新",
        "url": "https://acg.rip/.xml",
        "dialect": "acgnx",
        "aggregate": True,
        "measured": "200 状态码，30 个 item",
        "description": "体量小但质量稳定，可作为蜜柑/花园的补充源。",
    },
    {
        "id": "kisssub",
        "name": "爱恋动漫 Kisssub",
        "url": "https://www.kisssub.org/rss.xml",
        # 实测方言判定为 generic 而非 acgnx：该站 feed 自述标题是「爱恋动漫」，
        # 方言层按 feed 自述优先（镜像域名太多），认不出就用通用解析兜底。
        # 这里如实写 generic —— 写成 acgnx 会让预览页显示的方言与实际不符。
        "dialect": "generic",
        "aggregate": True,
        "measured": "200 状态码，50 个 item，方言=generic",
        "description": "爱恋动漫（acgnx 系）。通用解析可正常出条目，"
                       "但不提供做种数。",
    },
    {
        "id": "sukebei",
        "name": "Sukebei（成人向，默认关闭）",
        "url": "https://sukebei.nyaa.si/?page=rss",
        "dialect": "nyaa",
        "aggregate": True,
        "measured": "200 状态码，75 个 item",
        "description": "⚠️ 成人内容源。仅在明确需要时启用，"
                       "默认不建议开启（会污染追新雷达与热度排行）。",
        "adult": True,
    },
]


def list_bt_presets() -> list[dict[str, Any]]:
    """已实测可用的 BT 站点预设。"""
    return BT_SITE_PRESETS


def get_bt_preset(preset_id: str) -> dict[str, Any] | None:
    for item in BT_SITE_PRESETS:
        if item["id"] == str(preset_id):
            return item
    return None


def list_rss_presets(*, include_adult: bool = False) -> list[dict[str, Any]]:
    """已实测可用的 RSS 源预设。

    ``include_adult=False``（默认）会过滤掉成人向源：它不该在用户
    「一键添加推荐源」时被顺手带进去。
    """
    if include_adult:
        return RSS_FEED_PRESETS
    return [item for item in RSS_FEED_PRESETS if not item.get("adult")]


def get_rss_preset(preset_id: str) -> dict[str, Any] | None:
    for item in RSS_FEED_PRESETS:
        if item["id"] == str(preset_id):
            return item
    return None


def site_payload(preset: dict[str, Any], *, enabled: bool = True) -> dict[str, Any]:
    """把 BT 站点预设转成可直接落库的站点配置。"""
    options = dict(preset.get("options") or {})
    caveat = str(preset.get("caveat") or "").strip()
    measured = str(preset.get("measured") or "").strip()
    # 把实测数据与缺陷写进 options.note，用户在站点详情里能看到
    parts = [str(options.get("note") or "").strip(), f"实测：{measured}" if measured else "", caveat]
    note = "。".join([p for p in parts if p])
    if note:
        options["note"] = note
    options["preset_id"] = preset["id"]
    return {
        "name": preset["name"],
        "kind": preset.get("kind") or ProviderKind.INDEXER.value,
        "provider": preset["provider"],
        "url": preset["url"],
        "enabled": bool(enabled),
        "priority": int(preset.get("priority", 40)),
        "options": options,
    }


def feed_payload(preset: dict[str, Any], *, enabled: bool = True) -> dict[str, Any]:
    """把 RSS 预设转成可直接落库的 RSS 源配置。"""
    return {
        "name": preset["name"],
        "url": preset["url"],
        "dialect": preset.get("dialect") or "generic",
        "aggregate": bool(preset.get("aggregate", True)),
        "enabled": bool(enabled),
        "note": preset.get("description") or "",
    }
