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
        "id": "bt4g",
        "name": "BT4G 磁力搜索",
        "kind": ProviderKind.INDEXER.value,
        "provider": "bt4g",
        "url": "https://bt4gprx.com",
        "priority": 15,
        "description": "公开 BT 聚合搜索。**2026-09-04 复测已失效**："
                       "全部 5 个镜像域名（bt4gprx.com / bt4g.org / bt4g.com / "
                       "www.bt4gprx.com / bt4gprx.org）的 RSS 端点都改成了 "
                       "Cloudflare 人机验证页，返回 HTTP 200 + 一段 JS。"
                       "按 ADR-34 不做逆向，因此保留 Provider 但**默认不启用**。",
        "measured": "2026-09-04 实测：5/5 镜像返回 Cloudflare 人机验证页"
                    "（2441~2444 字节，含 cf_chl_out），可用条目 0 条。"
                    "此前 v1.26.0 实测「喜剧之王单口季」15 条 —— 站点侧变更，非本项目回归",
        "caveat": "⚠️ 当前不可用：RSS 端点已被 Cloudflare 人机验证拦截。"
                  "保留它是为了在站点撤下验证后能一键恢复；启用后搜索会如实报"
                  "「返回人机验证页」而不是「没有结果」",
        # 如实写 False：把已失效的源标成 verified 会让用户以为是自己配错了，
        # 反复折腾代理/域名 —— 而实际做什么都没用（ADR-82 的口径要求）。
        "verified": False,
        "enabled": False,
        "options": {
            "orderby": "size",
            "note": "搜索词走 quote 而不是 quote_plus，+ 号不会被解成空格",
        },
    },
    {
        "id": "bitsearch",
        "name": "Bitsearch 磁力搜索",
        "kind": ProviderKind.INDEXER.value,
        "provider": "bitsearch",
        "url": "https://bitsearch.eu",
        "priority": 16,
        "description": "公开 BT 聚合搜索。BT4G 失效后，这是本项目**唯一**"
                       "能覆盖华语综艺/剧集且提供真实做种数的免鉴权源 —— "
                       "dmhy/nyaa/animetosho 只收动漫，torrents-csv 偏英文。"
                       "内置 2 页翻页与镜像回退，因为它一旦挂掉 BT 搜索就整体归零。",
        "measured": "2026-09-04 实测（翻 2 页）：「喜剧之王单口季」35 条、"
                    "「流浪地球」40 条，相关性 100%，最高做种 99；"
                    "主域名换成不可达地址时经 solidtorrents.to 回退仍拿到 40 条",
        "caveat": "收录含大量网盘引流假种（体积 <5MB 却标 4K），依赖体积过滤拦掉；"
                  "第 3 页起会读超时、并发下会返回 500（限流，非无结果），"
                  "故默认只翻 2 页且翻页失败不影响已取回的结果",
        "verified": True,
        "options": {
            "sort": "seeders",
            "note": "搜索词走 quote_plus（query 参数，+ 即空格），与 BT4G 的路径段相反",
        },
    },
    {
        "id": "tpb",
        "name": "TPB 磁力搜索（apibay）",
        "kind": ProviderKind.INDEXER.value,
        "provider": "tpb",
        "url": "https://apibay.org",
        "priority": 17,
        "description": "The Pirate Bay 官方 API，免鉴权、不挂 Cloudflare、"
                       "一次返回全部命中且带真实做种数。参考 magnet-finder 选型："
                       "公开 JSON API 比 HTML 站稳定得多，是 Bitsearch 的全球内容备份。",
        "measured": "2026-09-11 实测：「dune 2024」100 条（最高 947 做种，"
                    "体积/IMDb/发布时间字段完整）；「流浪地球」100 条但相关性差",
        "caveat": "以全球/英文内容为主，华语关键词相关性弱——与 Bitsearch 互补而非替代；"
                  "接口不分页、一次全量返回",
        "verified": True,
        "options": {
            "note": "数字字段全是字符串，Provider 内已转 int；id=0 是没结果时的占位记录",
        },
    },
    {
        "id": "therarbg",
        "name": "therarbg 磁力搜索",
        "kind": ProviderKind.INDEXER.value,
        "provider": "therarbg",
        "url": "https://therarbg.com",
        "priority": 17,
        "description": "RARBG 数据库的延续站点，?format=json 就是现成 REST 接口，"
                       "免鉴权、带真实做种数、支持分页。",
        "measured": "2026-09-11 实测：「dune 2024」50 条/页且 links.next 分页有效；"
                    "「流浪地球」50 条但相关性差",
        "caveat": "以全球/英文内容为主，华语覆盖弱；多词查询的空格必须 %20 而非 +"
                  "（用 + 返回 0 条，Provider 已处理）",
        "verified": True,
        "options": {
            "max_pages": 2,
        },
    },
    {
        "id": "knaben",
        "name": "knaben 聚合磁力搜索",
        "kind": ProviderKind.INDEXER.value,
        "provider": "knaben",
        "url": "https://api.knaben.org",
        "priority": 17,
        "description": "聚合几十个站点的索引（TPB/1337x/Nyaa 等），POST JSON 接口，"
                       "免鉴权、按做种数排序、结果标注来源站（tracker 字段），"
                       "且自带含 tracker 列表的完整磁力——比裸磁力更好连 peer。",
        "measured": "2026-09-11 实测：「dune 2024」20 条（最高 945 做种）；"
                    "「流浪地球」7 条且首条即目标（DBD-Raws 1080P BDRip，9 做种）——"
                    "三个 API 源中华语表现最好",
        "caveat": "华语结果数量仍少于 Bitsearch；默认 hide_xxx/hide_unsafe，"
                  "结果偏保守",
        "verified": True,
        "options": {
            "limit": 100,
        },
    },
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
        "id": "torrents_csv",
        "name": "Torrents-CSV 公开 BT 搜索",
        "kind": ProviderKind.INDEXER.value,
        "provider": "torrents_csv",
        "url": "https://torrents-csv.com/service",
        "priority": 18,
        "description": "**零配置、免 API Key、支持中文关键词**的公开 BT 搜索。"
                       "聚合自 DHT 与主流公开站（海盗湾/1337x/Zooqle/YTS 等），"
                       "带做种数/体积/更新时间。实测中文 65 条、英文 75 条/3 页。",
        "measured": "凡人修仙传 65 条、庆余年 4 条、沙丘 3 条、dune 75 条/3 页",
        "caveat": "单页上限 25 条，翻页最多 3 页（75 条）；中文短词搜到的结果较少",
        "verified": True,
        "options": {
            "max_pages": 3,
            "local_filter": True,
            "note": "公开 BT 搜索。零配置，填好地址即可启用。接口本身免费，"
                    "部署量大的场景建议自建反代。",
        },
    },
    {
        "id": "animetosho",
        "name": "AnimeTosho 动漫索引",
        "kind": ProviderKind.INDEXER.value,
        "provider": "animetosho",
        "url": "https://feed.animetosho.org",
        "priority": 22,
        "description": "动漫全量镜像（Nyaa/AniDex/TokyoTosho），中英文可搜。"
                       "产出 .torrent 直链，不依赖 DHT。实测中文关键词 75 条。",
        "measured": "凡人修仙传 75 条、葬送的芙莉莲 75 条、Frieren 70 条",
        "caveat": "只收动漫，非动漫题材搜索命中率为零",
        "verified": True,
        "options": {
            "local_filter": False,
            "note": "备注：local_filter=False 刻意为之——中文关键词只能匹配到罗马音标题，"
                    "按字面过滤会把正确结果全误杀。",
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
        "id": "miobt",
        "name": "MioBT 动漫最新",
        "url": "http://www.miobt.com/rss.xml",
        "dialect": "generic",
        "aggregate": True,
        "measured": "200 状态码，RSS 正常解析（acgnx 系模板）",
        "description": "acgnx 系动漫 RSS，与爱恋动漫同模板。",
    },
    {
        "id": "comicat",
        "name": "漫猫动漫最新",
        "url": "http://www.comicat.org/rss.xml",
        "dialect": "generic",
        "aggregate": True,
        "measured": "200 状态码，RSS 正常解析（acgnx 系模板）",
        "description": "acgnx 系动漫 RSS，与爱恋动漫同模板。",
    },
    {
        "id": "animetosho_rss",
        "name": "AnimeTosho 全站最新",
        "url": "https://feed.animetosho.org/rss2",
        "dialect": "generic",
        "aggregate": True,
        "measured": "200 状态码，大量 item（常规 200+ 条）",
        "description": "AnimeTosho 全站 RSS，覆盖 Nyaa/AniDex/TokyoTosho 的动漫更新。",
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
    # 预设自己声明 ``enabled: False`` 的（已实测失效但保留待恢复的源）
    # 即使批量导入时要求全部启用，也不能启用 —— 否则每次搜索都会多等一个
    # 必然失败的站点，并在诊断里刷一条错误。调用方的 ``enabled`` 是
    # 「默认怎么办」，预设里的显式 False 是「这个站现在真的不行」，后者更具体。
    if preset.get("enabled") is False:
        enabled = False
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


# 默认初始化与用户安装引用同一具体站点定义；名称和优先级保留历史兼容。
DEFAULT_SITES = [
{'name': 'Jackett 聚合',
 'kind': 'indexer',
 'provider': 'torznab',
 'url': 'http://127.0.0.1:9117/api/v2.0/indexers/all/results/torznab',
 'enabled': False,
 'priority': 10,
 'options': {'note': '填入 Jackett/Prowlarr 的 Torznab 地址与 API Key'}},
{**site_payload(get_bt_preset('torrents_csv'), enabled=True), "name": 'Torrents-CSV 公开搜索', "priority": 18},
{**site_payload(get_bt_preset('animetosho'), enabled=True), "name": 'AnimeTosho 动漫', "priority": 22},
{'name': 'Nyaa 动漫',
 'kind': 'indexer',
 'provider': 'nyaa',
 'url': 'https://nyaa.si',
 'enabled': False,
 'priority': 30,
 'options': {'category': '1_2'}},
{'name': 'Mukaku 影视站',
 'kind': 'indexer',
 'provider': 'mukaku',
 'url': 'https://web5.mukaku.com',
 'enabled': False,
 'priority': 20,
 'options': {'note': '内置字段映射，启用后即可搜索（磁力+网盘），支持最新流追新；搜索请使用中文片名'}},
{'name': '西瓜影院（在线）',
 'kind': 'indexer',
 'provider': 'maccms',
 'url': 'https://www.bzzdyy.com',
 'enabled': False,
 'priority': 60,
 'options': {'note': 'MacCMS 在线站：产出播放源指向的【平台原始地址】，交给 yt-dlp。实测约 92% '
                     '是腾讯/爱奇艺/优酷/苒果的会员正片，这部分会被如实拒绕（不接入任何 VIP 解析网关）；能下的主要是 B 站与官方免费内容',
             'max_items': 6}},
{'name': 'Bilibili 视频搜索',
 'kind': 'indexer',
 'provider': 'bilibili',
 'url': 'https://www.bilibili.com',
 'enabled': False,
 'priority': 70,
 'options': {'note': '搜索 B 站公开视频，下载交由 yt-dlp；仅公开内容，不解析大会员正片。填入自己的 Cookie 可提升配额稳定性（非必需）',
             'limit': 20}},
{'name': 'YouTube 视频搜索',
 'kind': 'indexer',
 'provider': 'youtube',
 'url': 'https://www.youtube.com',
 'enabled': False,
 'priority': 75,
 'options': {'note': '复用 yt-dlp 的 ytsearch；国内网络请在 proxy 里填代理地址，如 http://127.0.0.1:7890',
             'limit': 20,
             'proxy': ''}},
{'name': 'PanSou 盘搜',
 'kind': 'pan',
 'provider': 'pansou',
 'url': 'http://127.0.0.1:8888',
 'enabled': False,
 'priority': 20,
 'options': {'note': '部署 pansou 服务后填写其地址'}},
{'name': 'KK 网盘搜',
 'kind': 'pan',
 'provider': 'kkso',
 'url': 'https://kkso.net',
 'enabled': True,
 'priority': 25,
 'options': {'note': '开箱可用的网盘搜索（夸克/百度/迅雷）。来自 awesome-zhuiju-free 清单，经本地搜索探测实测可用'}},
{'name': '追剧 zhuiju.us',
 'kind': 'pan',
 'provider': 'kkso',
 'url': 'https://www.zhuiju.us',
 'enabled': True,
 'priority': 26,
 'options': {'note': '与 KK 网盘搜同模板，资源以百度/迅雷为主且更新更勤（实测提取码拼在链接 ?pwd= 上，已自动提取）'}},
{'name': 'BT 世界网',
 'kind': 'indexer',
 'provider': 'html_generic',
 'url': 'https://www.btsj6.com',
 'enabled': False,
 'priority': 48,
 'options': {'note': '来自 awesome-zhuiju-free 清单，实测「凡人修仙传」直出 20 条磁力；注意搜索路径是 ?s=（/s/ 会 '
                     '404）。热门剧命中好，冷门片可能 0 条',
             'search_url': 'https://www.btsj6.com/?s={keyword}',
             'magnet_only': True,
             'max_rows': 60}},
{**site_payload(get_bt_preset('cilixiong'), enabled=True), "name": '磁力熊 Cilixiong', "priority": 46},
{'name': 'qBittorrent',
 'kind': 'downloader',
 'provider': 'qbittorrent',
 'url': 'http://127.0.0.1:8080',
 'username': 'admin',
 'enabled': False,
 'priority': 10,
 'options': {'category': 'CineFlow', 'tags': 'CineFlow'}},
{'name': 'yt-dlp 视频下载',
 'kind': 'downloader',
 'provider': 'ytdlp',
 'url': '',
 'enabled': True,
 'priority': 60,
 'options': {'note': '抓取公开视频页面（B 站/YouTube/抖音/TikTok 等 1700+ 站点）；仅支持公开内容，不解析会员/付费正片',
             'max_height': 1080,
             'write_subtitles': True,
             'write_thumbnail': True,
             'rate_limit': 0}},
{'name': 'AList 网盘（推荐）',
 'kind': 'panstorage',
 'provider': 'alist',
 'url': 'http://127.0.0.1:5244',
 'username': 'admin',
 'enabled': False,
 'priority': 10,
 'options': {'note': 'AList 一套接入 20+ 网盘，填地址与账号（或 api_key）即可；转存走离线下载接口',
             'root_path': '/',
             'offline_tool': 'SimpleHttp'}},
{'name': '夸克网盘',
 'kind': 'panstorage',
 'provider': 'quark',
 'url': 'https://drive-pc.quark.cn',
 'enabled': False,
 'priority': 20,
 'options': {'note': '在 cookie 字段填浏览器里的完整 Cookie；支持分享链接直接转存', 'cookie': '', 'root_path': '/'}},
{'name': '115 网盘',
 'kind': 'panstorage',
 'provider': 'pan115',
 'url': 'https://115.com',
 'enabled': False,
 'priority': 21,
 'options': {'note': '在「网盘管理 → 登录网盘」扫码即可自动填 Cookie（115 官方扫码接口，本项目支持得最好的网盘）；也可手工粘贴 Cookie',
             'cookie': '',
             'root_path': '/'}},
{'name': '百度网盘',
 'kind': 'panstorage',
 'provider': 'baidu',
 'url': 'https://pan.baidu.com',
 'enabled': False,
 'priority': 22,
 'options': {'note': '在「网盘管理 → 登录网盘」扫码登录（passport 通道）；百度风控较严，扫码换不到 Cookie 时改用 Cookie 导入',
             'cookie': '',
             'root_path': '/'}},
{'name': 'WebDAV 网盘',
 'kind': 'panstorage',
 'provider': 'webdav',
 'url': 'http://127.0.0.1:5005/dav',
 'username': '',
 'enabled': False,
 'priority': 30,
 'options': {'note': '一份实现覆盖 Nextcloud/坚果云/群晖/TeraCLOUD/AList 的 DAV 端点；填 URL + 账号密码即可浏览并生成 STRM',
             'root_path': '/'}},
{'name': '本地/挂载目录',
 'kind': 'panstorage',
 'provider': 'local_dir',
 'url': 'file:///',
 'enabled': False,
 'priority': 90,
 'options': {'note': '把 rclone/CloudDrive 挂载出来的目录当网盘浏览，无需联网即可试用', 'root_path': '/'}},
{'name': 'Emby',
 'kind': 'mediaserver',
 'provider': 'emby',
 'url': 'http://127.0.0.1:8096',
 'enabled': False,
 'priority': 50}
]
