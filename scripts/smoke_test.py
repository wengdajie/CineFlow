"""对运行中的 CineFlow 实例做全接口冒烟测试。"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
#: 服务地址，可用 CF_BASE_URL / CF_PORT 覆盖（默认与 settings.PORT 一致）
BASE = os.environ.get("CF_BASE_URL") or f"http://127.0.0.1:{os.environ.get('CF_PORT', '6060')}"
API = BASE + "/api/v1"
results = []


def call(method, path, *, body=None, form=None, token=None, expect=(200, 201), extra_headers=None,
         timeout=45):
    url = path if path.startswith("http") else API + path
    data, headers = None, dict(extra_headers or {})
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, text = response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, text = exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:
        status, text = 0, str(exc)

    ok = status in expect
    results.append((ok, method, path, status))
    print(f"{'PASS' if ok else 'FAIL'} {status:3} {method:6} {path}")
    if not ok:
        print(f"       -> {text[:300]}")
    try:
        return json.loads(text)
    except Exception:
        return {}


print("=" * 70)
print("1) 健康检查与未授权拦截")
print("=" * 70)
call("GET", BASE + "/api/health")
call("GET", BASE + "/")
call("GET", "/system/dashboard", expect=(401,))
call("GET", "/subscribes", expect=(401,))

print("\n" + "=" * 70)
print("2) 认证")
print("=" * 70)
call("POST", "/auth/login", form={"username": "admin", "password": "wrong"}, expect=(400, 401))
auth = call("POST", "/auth/login", form={"username": "admin", "password": "cineflow"})
token = auth.get("access_token")
assert token, "登录失败，无法继续"
print(f"       token 长度 {len(token)}")
call("GET", "/auth/me", token=token)

print("\n" + "=" * 70)
print("3) 系统与仪表盘")
print("=" * 70)
call("GET", "/system/dashboard", token=token)
call("GET", "/system/info", token=token)
call("GET", "/system/jobs", token=token)
call("GET", "/system/logs?limit=20", token=token)
call("GET", "/system/notifications", token=token)

print("\n" + "=" * 70)
print("4) 站点与 Provider")
print("=" * 70)
providers = call("GET", "/sites/providers", token=token)


def items_of(payload):
    """兼容 list 与 {items: [...]} 两种响应结构。"""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return payload.get("items") or []
    return []


kinds = {}
for item in items_of(providers):
    kinds.setdefault(item.get("kind"), []).append(item.get("name"))
for kind, names in sorted(kinds.items()):
    print(f"       {kind:12} {sorted(names)}")

sites = call("GET", "/sites", token=token)
print(f"       已有站点 {len(items_of(sites))} 个")

created = call(
    "POST",
    "/sites",
    token=token,
    body={
        "name": "冒烟测试站点",
        "kind": "indexer",
        "provider": "torznab",
        "url": "http://127.0.0.1:9/api",
        "enabled": False,
        "priority": 5,
    },
)
site_id = created.get("id") or (created.get("item") or created.get("site") or {}).get("id")
print(f"       新建站点 id={site_id}")
if site_id:
    call("PATCH", f"/sites/{site_id}", token=token, body={"enabled": False, "priority": 9})
    call("POST", f"/sites/{site_id}/test", token=token, expect=(200, 400, 502))
    call("DELETE", f"/sites/{site_id}", token=token)

print("\n" + "=" * 70)
print("4b) 自定义站点：预设模板 / 字段映射 / 导航站发现")
print("=" * 70)
presets = call("GET", "/sites/presets", token=token)
preset_ids = [item.get("id") for item in items_of(presets)]
print(f"       可用预设 {preset_ids}")

# 套用预设一键建站
applied = call(
    "POST",
    "/sites/presets/mukaku/apply?name=" + urllib.parse.quote("冒烟-预设站"),
    token=token,
)
preset_site_id = applied.get("id")
print(f"       预设建站 id={preset_site_id} provider={applied.get('provider')}")
if preset_site_id:
    call("DELETE", f"/sites/{preset_site_id}", token=token)

# 字段映射式自定义 JSON API 站点
custom = call(
    "POST",
    "/sites",
    token=token,
    body={
        "name": "冒烟-自定义接口站",
        "kind": "indexer",
        "provider": "api_generic",
        "url": "https://example.invalid",
        "enabled": False,
        "options": {
            "api_base": "https://example.invalid/api/v1",
            "search_path": "search",
            "query_key": "kw",
            "list_path": "data.list",
            "item_map": {"title": "name", "link": "magnet"},
        },
    },
)
custom_id = custom.get("id")
print(f"       自定义站 id={custom_id} options 已保存={bool(custom.get('options'))}")
if custom_id:
    call("POST", f"/sites/{custom_id}/test", token=token, expect=(200, 400, 502))
    call("DELETE", f"/sites/{custom_id}", token=token)

# 正则式网页站点
html_site = call(
    "POST",
    "/sites",
    token=token,
    body={
        "name": "冒烟-网页正则站",
        "kind": "indexer",
        "provider": "html_generic",
        "url": "https://example.invalid",
        "enabled": False,
        "options": {
            "search_url": "https://example.invalid/search?q={keyword}",
            "magnet_only": True,
        },
    },
)
if html_site.get("id"):
    call("DELETE", f"/sites/{html_site['id']}", token=token)

print("\n" + "=" * 70)
print("4c) 追新雷达")
print("=" * 70)
call("GET", "/radar/jobs", token=token)
feed = call("GET", "/radar/feed?limit_per_site=5", token=token)
feed_total = (feed.get("data") or {}).get("total", 0)
print(f"       最新流 {feed_total} 条（未启用站点时为 0 属正常）")
radar_run = call("POST", "/radar/run?dry_run=true", token=token)
radar_data = radar_run.get("data") or {}
print(
    f"       预览：资源 {radar_data.get('resources', 0)} / "
    f"活跃订阅 {radar_data.get('subscribes', 0)} / 命中 {radar_data.get('matched', 0)}"
)

print("\n" + "=" * 70)
print("4d) 热度排行：资源榜 / 热词 / 站点榜 / 实时榜")
print("=" * 70)
overview = call("GET", "/trending?days=30&limit=5", token=token)
ov = overview.get("data") or {}
print(
    f"       总览：资源榜 {len(ov.get('resources') or [])} 条 / "
    f"热词 {len(ov.get('keywords') or [])} 个 / 站点 {len(ov.get('sites') or [])} 个"
)
ranking = call("GET", "/trending/resources?days=30&limit=10", token=token)
rows = (ranking.get("data") or {}).get("items") or []
print(f"       资源热度榜 {len(rows)} 条（无历史搜索缓存时为 0 属正常）")
for row in rows[:3]:
    print(
        f"         #{row.get('rank')} {str(row.get('title'))[:24]} "
        f"heat={row.get('heat')} {row.get('heat_percent')}%"
    )
call("GET", "/trending/keywords?days=60&limit=8", token=token)
call("GET", "/trending/sites?days=30&limit=10", token=token)
live = call("GET", "/trending/live?limit=5&limit_per_site=10", token=token)
live_rows = (live.get("data") or {}).get("items") or []
print(f"       实时热榜 {len(live_rows)} 条（未启用站点时为 0 属正常）")

# v1.7.0：豆瓣封面 + 图片代理
douban = call("GET", "/trending/douban?keyword=" + urllib.parse.quote("庆余年") + "&limit=3",
              token=token)
d_items = items_of(douban)
print(f"       豆瓣条目 {len(d_items)} 条 限流={douban.get('rate_limited')}")
for row in d_items[:3]:
    print(
        f"         {str(row.get('title'))[:20]:22} {row.get('year')} "
        f"{row.get('media_type')} 封面={'有' if row.get('poster') else '无'}"
    )
# 关掉补图时不该报错（画板之外的调用方用得到）
call("GET", "/trending/resources?days=30&limit=5&with_poster=false", token=token)
# 图片代理：白名单外必须 400（SSRF 防线），内网地址同样拒绝
call("GET", "/images/proxy?url=" + urllib.parse.quote("https://evil.example.com/x.jpg"),
     token=token, expect=(400,))
call("GET", "/images/proxy?url=" + urllib.parse.quote("http://127.0.0.1:6060/api/health"),
     token=token, expect=(400,))
# 代理端点必须匿名可用（img 标签带不了 token），所以不能是 401
call("GET", "/images/proxy?url=" + urllib.parse.quote("https://evil.example.com/x.jpg"),
     expect=(400,))

print("\n" + "=" * 70)
print("4e) 发现榜：豆瓣四分类 + Bilibili + YouTube（v1.10.0）")
print("=" * 70)
# 分类清单：前端据此渲染页签，所以它必须先对
cats = call("GET", "/trending/discover/categories", token=token)
cat_data = cats.get("data") or {}
cat_keys = [c.get("key") for c in (cat_data.get("categories") or [])]
print(f"       分类 {len(cat_keys)} 个：{cat_keys}")
assert len(cat_keys) == 7, f"发现榜应有 7 个分类，实际 {cat_keys}"
assert "bilibili" in cat_keys, "缺少 Bilibili 分类"
assert "youtube" in cat_keys, "缺少 YouTube 分类"
assert "bangumi" in cat_keys, "缺少「新番」分类（Bangumi 放送日历，v1.12.0）"
# kind 决定前端给「搜资源」还是「直接下载」，漏了会让 B 站/YouTube 卡片
# 错误显示成搜资源（v1.10.0 实测踩过这个坑），所以这里逐个钉死。
kinds = {c.get("key"): c.get("kind") for c in (cat_data.get("categories") or [])}
assert kinds == {
    "movie": "media", "tv": "media", "anime": "media", "show": "media",
    "bilibili": "video", "youtube": "video",
    # 新番是影视作品（要搜资源/订阅），不是单个视频，所以是 media
    "bangumi": "media",
}, f"分类 kind 不符预期：{kinds}"
parts = cat_data.get("bili_partitions") or []
print(f"       B 站二级分区 {len(parts)} 个：{[p.get('key') for p in parts]}")
regions = cat_data.get("yt_regions") or []
print(f"       YouTube 地区 {len(regions)} 个：{[r.get('key') for r in regions]}")
assert regions, "YouTube 分类必须下发可选地区，否则前端二级切换是空的"

# 总览：一次并发拉全部分类，首屏用
dv = call("GET", "/trending/discover?limit=4", token=token)
charts = (dv.get("data") or {}).get("charts") or []
print(f"       总览返回 {len(charts)} 个榜")
for chart in charts:
    print(
        f"         {chart.get('label')!s:10} {chart.get('count')} 条"
        f" 来源={chart.get('source')}"
    )

# 逐个分类单拉（切页签的路径）。外部榜单可能限流/被风控，
# 所以只断言 HTTP 200 + 结构正确，不断言一定有数据——否则门禁会随外网状态飘。
for category in ("movie", "tv", "anime", "show", "bilibili", "youtube", "bangumi"):
    one = call("GET", f"/trending/discover/{category}?limit=6", token=token)
    body = one.get("data") or {}
    rows = items_of(body)
    print(f"       {category:9} {len(rows):3} 条  {str(body.get('message') or '')[:40]}")
    for row in rows[:2]:
        print(
            f"         {str(row.get('title'))[:22]:24} 评分={row.get('rating')}"
            f" 封面={'有' if row.get('poster') else '无'}"
            f" 本地片源={row.get('local_count')}"
        )

# B 站二级分区：番剧/国创走的是 PGC 接口（与 UGC 分区不同），必须都能通
for partition in ("all", "bangumi", "guochuang", "douga"):
    call("GET", f"/trending/bilibili/{partition}?limit=5", token=token)

# YouTube 走公开 Piped 实例，实例可能整体不可用——只断言 HTTP 200 与结构，
# 不断言有数据，否则门禁会随第三方实例状态飘红（同豆瓣/B 站的处理口径）。
for region in ("US", "JP", "HK"):
    yt = call("GET", f"/trending/youtube/{region}?limit=5", token=token)
    yt_rows = items_of(yt.get("data") or {})
    print(f"       youtube/{region:3} {len(yt_rows):3} 条")
    for row in yt_rows[:2]:
        print(
            f"         {str(row.get('title'))[:26]:28}"
            f" 封面={'有' if row.get('poster') else '无'}"
            f" 地址={'有' if row.get('url') else '无'}"
        )
        assert row.get("url"), "YouTube 榜条目必须带播放地址，否则「下载」按钮是假的"
call("GET", "/trending/youtube/NOSUCH?limit=5", token=token)

# 未知分类应优雅降级：200 + 空列表 + 可读提示，而不是 404 让整页报错
unknown = call("GET", "/trending/discover/nosuchcategory?limit=5", token=token)
assert not items_of(unknown.get("data") or {}), "未知分类不该返回条目"
print(f"       未知分类降级提示：{str((unknown.get('data') or {}).get('message'))[:40]}")
call("GET", "/trending/bilibili/nosuchpartition?limit=5", token=token)
# 发现榜需要登录（榜单里带本地片源统计，属于用户数据）
call("GET", "/trending/discover", expect=(401,))

print("\n" + "=" * 70)
print("5) 搜索（无启用站点时应优雅返回空）")
print("=" * 70)
search = call("GET", "/search?keyword=" + urllib.parse.quote("庆余年") + "&media_type=tv", token=token)
print(f"       结果 {len(items_of(search))} 条（未配站点时为 0 属正常）")
# 每站诊断：v1.6.0 起搜索响应带 sites[]，用于回答"为什么这个站没结果"
site_outcomes = (search.get("data") or search).get("sites") or search.get("sites") or []
print(f"       站点诊断 {len(site_outcomes)} 条（未启用站点时为 0 属正常）")
for outcome in site_outcomes[:6]:
    print(
        f"         {str(outcome.get('site'))[:16]:16} {outcome.get('status'):8} "
        f"raw={outcome.get('raw')} kept={outcome.get('kept')} "
        f"{outcome.get('elapsed_ms')}ms {str(outcome.get('message') or '')[:40]}"
    )

print("\n" + "=" * 70)
print("5b) 网络视频下载（yt-dlp）：付费墙必须被拒")
print("=" * 70)
# 只验证合规边界与端点连通，不真去外网抓取（冒烟测试不该依赖公网）
for blocked_url in (
    "https://v.qq.com/x/cover/mzc00200abc/n0045xyz.html",
    "https://www.iqiyi.com/v_19rr7f0m0k.html",
    "https://www.netflix.com/watch/80100172",
):
    call(
        "POST",
        "/downloads/webvideo/probe?url=" + urllib.parse.quote(blocked_url, safe=""),
        token=token,
        expect=(400,),
    )
print("       长视频平台正片页在入口即被拒绝（不绕过付费墙）")

print("\n" + "=" * 70)
print("6) 元数据识别（未配 TMDB 时应降级不报错）")
print("=" * 70)
call("GET", "/media/recognize?name=" + urllib.parse.quote("凡人修仙传 第二季 第105集 4K HDR 国语中字"), token=token)
call("GET", "/media/search?keyword=" + urllib.parse.quote("三体"), token=token)
call("GET", "/media/trending", token=token)

print("\n" + "=" * 70)
print("7) 订阅全生命周期")
print("=" * 70)
sub = call(
    "POST",
    "/subscribes",
    token=token,
    body={"title": "冒烟测试剧", "media_type": "tv", "season": 1, "total_episodes": 6},
)
sub_id = sub.get("id") or (sub.get("item") or sub.get("subscribe") or {}).get("id")
print(f"       订阅 id={sub_id}")
call("GET", "/subscribes", token=token)
if sub_id:
    call("GET", f"/subscribes/{sub_id}", token=token)
    call("GET", f"/subscribes/{sub_id}/missing", token=token)
    call("PATCH", f"/subscribes/{sub_id}", token=token, body={"status": "paused"})
    call("PATCH", f"/subscribes/{sub_id}", token=token, body={"status": "active"})
    call("POST", f"/subscribes/{sub_id}/run", token=token, timeout=240)
call("POST", "/subscribes/run-all", token=token, timeout=240)

print("\n" + "=" * 70)
print("8) 下载与媒体库")
print("=" * 70)
call("GET", "/downloads", token=token)
call("POST", "/downloads/sync", token=token)
call("GET", "/library/stats", token=token)
call("GET", "/library/files", token=token)
call("GET", "/library/transfers", token=token)
call("POST", "/library/scan", token=token)
call("POST", "/library/transfer", token=token, body={"source": str(__import__("pathlib").Path("downloads").resolve()), "dry_run": True})
call("POST", "/library/refresh", token=token)

print("\n" + "=" * 70)
print("9) 插件：列表 / 启用 / 配置 / 动作 / 停用")
print("=" * 70)
plugins = call("GET", "/plugins", token=token)
found = {item["id"]: item for item in items_of(plugins)}
print(f"       发现插件 {sorted(found)}")
for plugin_id in ("auto_cleanup", "pan_transfer", "daily_digest"):
    if plugin_id not in found:
        results.append((False, "PLUGIN", plugin_id, 0))
        print(f"FAIL   0 PLUGIN {plugin_id} 未被发现")
        continue
    schema = found[plugin_id].get("config_schema") or []
    print(f"       {plugin_id}: {len(schema)} 个配置项")
    call("POST", f"/plugins/{plugin_id}/enable", token=token)
    call("PUT", f"/plugins/{plugin_id}/config", token=token, body={"config": {"enabled": True}})

detail = call("GET", "/plugins", token=token)
for item in items_of(detail):
    if item["id"] in ("auto_cleanup", "pan_transfer", "daily_digest"):
        print(f"       {item['id']}: enabled={item['enabled']} loaded={item['loaded']} actions={item['actions']}")

call("POST", "/plugins/auto_cleanup/run", token=token, body={"action": "cleanup", "params": {}})
call("POST", "/plugins/auto_cleanup/run", token=token, body={"action": "prune_missing_library_files", "params": {}})
call("POST", "/plugins/pan_transfer/run", token=token, body={"action": "list_pending", "params": {}})
digest = call("POST", "/plugins/daily_digest/run", token=token, body={"action": "preview", "params": {}})
body_text = (digest.get("result") or {}).get("body", "")
print("       —— 日报预览 ——")
for line in body_text.splitlines()[:14]:
    print("       " + line)
call("POST", "/plugins/auto_cleanup/run", token=token, body={"action": "no-such-action"}, expect=(400,))

jobs = call("GET", "/system/jobs", token=token)
print("       调度任务：")
for job in items_of(jobs):
    print(f"         {job.get('id')} -> {job.get('next_run_time')}")

for plugin_id in ("auto_cleanup", "pan_transfer", "daily_digest"):
    call("POST", f"/plugins/{plugin_id}/disable", token=token)

print("\n" + "=" * 70)
print("9b) 定时任务设置：查看 / 改期 / 重置 / 立即执行")
print("=" * 70)
schedules = call("GET", "/schedules", token=token)
sched_items = items_of(schedules)
print(f"       内置任务 {len(sched_items)} 个")
for item in sched_items:
    rule = (
        f"{item.get('minutes')} 分钟"
        if item.get("trigger") == "interval"
        else f"cron {item.get('cron')}"
    )
    print(
        f"         {item.get('key'):10} {item.get('name')[:18]:20} "
        f"{rule:16} enabled={item.get('enabled')} next={item.get('next_run_time')}"
    )
call("GET", "/schedules/subscribe", token=token)
call("GET", "/schedules/no-such-job", token=token, expect=(404,))

# 改期 → 校验生效 → 重置回默认
updated = call("PUT", "/schedules/subscribe", token=token,
               body={"trigger": "interval", "minutes": 45, "enabled": True})
udata = updated.get("data") or {}
print(f"       改期后：minutes={udata.get('minutes')} customized={udata.get('customized')} "
      f"applied={udata.get('applied')} next={udata.get('next_run_time')}")
call("PUT", "/schedules/library", token=token,
     body={"trigger": "cron", "cron": "30 5 * * *"})
# 非法规则必须被拒
call("PUT", "/schedules/subscribe", token=token,
     body={"trigger": "cron", "cron": "bad expr"}, expect=(400, 422))
call("PUT", "/schedules/subscribe", token=token,
     body={"trigger": "interval", "minutes": 0}, expect=(400, 422))

reset = call("POST", "/schedules/subscribe/reset", token=token)
rdata = reset.get("data") or {}
print(f"       重置后：minutes={rdata.get('minutes')} customized={rdata.get('customized')}")
call("POST", "/schedules/library/reset", token=token)
call("POST", "/schedules/radar/run", token=token, expect=(200, 400))

print("\n" + "=" * 70)
print("9c) 网盘管理：总览 / 浏览 / 待转存 / 记录 / 转存")
print("=" * 70)
pan = call("GET", "/pan", token=token)
pan_items = items_of(pan)
print(f"       已启用网盘 {len(pan_items)} 个")
for item in pan_items:
    quota = item.get("quota") or {}
    print(
        f"         {item.get('name')[:16]:18} {item.get('provider'):10} "
        f"已用 {quota.get('percent')}% 可转存={item.get('supports_save')}"
    )
    caps = item.get("capabilities") or {}
    assert caps, "总览必须下发能力位（前端据此渲染文件管理按钮）"
    print(
        f"           能力位：改名={caps.get('rename')} 移动={caps.get('move')} "
        f"搜索={caps.get('search')} 保活={caps.get('keepalive')}"
    )

pending = call("GET", "/pan/pending?limit=10", token=token)
print(f"       待转存队列 {len(items_of(pending))} 条")
records = call("GET", "/pan/records?limit=10", token=token)
print(f"       转存记录 {len(items_of(records))} 条")

# 有启用的网盘就顺带验证目录浏览、建目录、直链、删除
if pan_items:
    pan_site = pan_items[0]["site_id"]
    files = call("GET", f"/pan/files?site_id={pan_site}&path=/", token=token)
    print(
        f"       浏览根目录：{files.get('total')} 个条目 parent={files.get('parent')}"
    )
    # 查询串里的中文必须先 percent-encode，否则 urllib 会按 ASCII 编码报错
    probe_dir = urllib.parse.quote("/冒烟测试目录")
    probe_file = urllib.parse.quote("/不存在.mkv")
    call("POST", "/pan/mkdir", token=token,
         body={"site_id": pan_site, "path": "/冒烟测试目录"}, expect=(200, 400))
    call("DELETE", f"/pan/files?site_id={pan_site}&path={probe_dir}",
         token=token, expect=(200, 400))
    call("GET", f"/pan/download-url?site_id={pan_site}&path={probe_file}",
         token=token, expect=(200, 400))
    call("POST", f"/pan/{pan_site}/test", token=token)

    # v1.7.0 文件管理链路：建目录 → 改名 → 盘内搜索 → 移动 → 清理
    caps = pan_items[0].get("capabilities") or {}
    call("POST", "/pan/mkdir", token=token,
         body={"site_id": pan_site, "path": "/cf_smoke_src"}, expect=(200, 400))
    if caps.get("rename"):
        call("POST", "/pan/rename", token=token,
             body={"site_id": pan_site, "path": "/cf_smoke_src",
                   "new_name": "cf_smoke_renamed"}, expect=(200, 400))
    if caps.get("search"):
        found = call("GET", f"/pan/search?site_id={pan_site}&keyword=cf_smoke&limit=10",
                     token=token, expect=(200, 400))
        print(f"       盘内搜索命中 {found.get('total')} 条")
    if caps.get("move"):
        call("POST", "/pan/mkdir", token=token,
             body={"site_id": pan_site, "path": "/cf_smoke_dst"}, expect=(200, 400))
        call("POST", "/pan/move", token=token,
             body={"site_id": pan_site, "path": "/cf_smoke_renamed",
                   "target_dir": "/cf_smoke_dst", "copy": False}, expect=(200, 400))
        call("DELETE", f"/pan/files?site_id={pan_site}&path={urllib.parse.quote('/cf_smoke_dst')}",
             token=token, expect=(200, 400))
    call("DELETE", f"/pan/files?site_id={pan_site}&path={urllib.parse.quote('/cf_smoke_renamed')}",
         token=token, expect=(200, 400))
else:
    print("       （未启用网盘，跳过目录类端点；下面仍验证降级提示）")

# 不存在的网盘要 404 而不是 500
call("GET", "/pan/files?site_id=999999", token=token, expect=(404,))
# 没有可用网盘或链接非法时要 400 并给出明确原因
call("POST", "/pan/save", token=token,
     body={"share_url": "https://pan.quark.cn/s/smoketest"}, expect=(200, 400))
transfer = call("POST", "/pan/transfer?limit=5", token=token)
print(f"       批量转存：待处理 {transfer.get('pending')} 成功 {transfer.get('saved')}")
# 凭据保活巡检：无论有没有网盘都必须返回结构化结果
keep = call("POST", "/pan/keep-alive", token=token)
print(f"       保活巡检：共 {keep.get('total')} 个，异常 {keep.get('failed')} 个")
# 不存在的网盘做文件管理要 400 而不是 500
call("POST", "/pan/rename", token=token,
     body={"site_id": 999999, "path": "/a", "new_name": "b"}, expect=(400,))
call("POST", "/pan/move", token=token,
     body={"site_id": 999999, "path": "/a", "target_dir": "/b"}, expect=(400,))
call("GET", "/pan/search?site_id=999999&keyword=x", token=token, expect=(400,))
# 未授权拦截
call("GET", "/pan", expect=(401,))
call("POST", "/pan/save", body={"share_url": "x"}, expect=(401,))
call("POST", "/pan/keep-alive", expect=(401,))
call("GET", "/pan/search?site_id=1&keyword=x", expect=(401,))

print("\n" + "=" * 70)
print("9d) ChatOps 机器人：平台 / 配置 / 指令 / 审计 / Webhook 验签")
print("=" * 70)
platforms = call("GET", "/chatops/platforms", token=token)
for item in items_of(platforms):
    print(
        f"         {item.get('display_name'):10} {item.get('webhook_path'):40} "
        f"字段 {len(item.get('fields') or [])} 已配置={item.get('configured')}"
    )
config = call("GET", "/chatops/config", token=token)
cdata = config.get("data") or {}
print(
    f"       全局：enabled={cdata.get('enabled')} auto_download={cdata.get('auto_download')} "
    f"result_limit={cdata.get('result_limit')}"
)
commands = call("GET", "/chatops/commands", token=token)
print(f"       支持指令 {[item['name'] for item in commands.get('commands') or []]}")

# 指令解析（不执行）
for text in ("搜索 庆余年 第二季", "下载 2", "订阅 凡人修仙传 第2季", "状态", "热榜"):
    parsed = call("POST", "/chatops/parse", token=token, body={"text": text})
    data = parsed.get("data") or {}
    print(
        f"         「{text}」-> {data.get('name')} arg={data.get('argument')!r} "
        f"index={data.get('index')} season={data.get('season')}"
    )
call("POST", "/chatops/parse", token=token, body={"text": "@bot"}, expect=(400, 422))

# 指令真实执行
for text in ("状态", "订阅列表", "帮助", "热榜", "转存"):
    tested = call("POST", "/chatops/test", token=token, body={"text": text})
    reply = str(tested.get("reply") or "").splitlines()
    print(f"         执行「{text}」-> {reply[0][:56] if reply else '(空)'}")

audit = call("GET", "/chatops/audit?limit=10", token=token)
print(f"       审计日志 {len(items_of(audit))} 条（刚才的指令应已留痕）")

# 配置读写与脱敏
call("PUT", "/chatops/config", token=token,
     body={"platforms": {"telegram": {"secret_token": "smoke-secret", "token": "smoke-bot"}}})
masked = (call("GET", "/chatops/config", token=token).get("data") or {}).get("platforms") or {}
tg = masked.get("telegram") or {}
print(f"       密钥脱敏：secret_token={tg.get('secret_token')} （应为 ******）")
if tg.get("secret_token") != "******":
    results.append((False, "MASK", "/chatops/config", 200))
    print("FAIL   0 MASK   密钥未脱敏")

# Webhook：验签失败必须 401；未知平台 404
call("POST", "/chatops/webhook/telegram", body={"message": {"text": "状态"}}, expect=(401,))
call("POST", "/chatops/webhook/wechat", body={}, expect=(404,))

# 带正确 secret token 的 Webhook 必须放行并真正执行指令（不需要登录）
webhook_ok = call(
    "POST",
    "/chatops/webhook/telegram",
    body={
        "message": {
            "text": "状态",
            "chat": {"id": 1},
            "from": {"id": 2},
            "message_id": int(__import__("time").time()),
        }
    },
    extra_headers={"X-Telegram-Bot-Api-Secret-Token": "smoke-secret"},
)
print(f"       带验签的 Webhook：handled={webhook_ok.get('handled')}")
if not webhook_ok.get("handled"):
    results.append((False, "WEBHOOK", "/chatops/webhook/telegram", 200))
    print("FAIL   0 WEBHOOK 验签通过但指令未执行")

# 复原配置，避免污染本地环境
call("PUT", "/chatops/config", token=token, body={"platforms": {}})
call("GET", "/chatops/config", expect=(401,))

print("\n" + "=" * 70)
print("9e) STRM 同步：概览 / 记录 / 手动同步 / 匿名 302 播放端点")
print("=" * 70)
strm = call("GET", "/strm", token=token)
sdata = strm.get("data") or {}
print(
    f"       STRM {sdata.get('total')} 个（有效 {sdata.get('alive')} / 失效 {sdata.get('invalid')}）"
    f" 模式={sdata.get('link_mode')} 目录={sdata.get('strm_dir')}"
)
call("GET", "/strm/records?limit=10", token=token)
call("GET", "/strm/records?limit=10&alive_only=true", token=token)
synced = call("POST", "/strm/sync", token=token, body={})
print(f"       全盘同步：{synced.get('message')}")
call("POST", "/strm/sync", token=token, body={"site_id": 999999})
# 播放端点必须**免认证**（播放器带不了 JWT），未知记录回 404 而不是 401
call("GET", "/strm/play/999999", expect=(404,))
call("GET", "/strm", expect=(401,))

print("\n" + "=" * 70)
print("9f) 网盘分享追更：CRUD / 巡检 / 批量巡检")
print("=" * 70)
ps_created = call(
    "POST",
    "/pan-subscribes",
    token=token,
    body={
        "name": "冒烟测试追更",
        "share_url": "https://pan.quark.cn/s/smoke-test",
        "exclude_regex": "预告|花絮",
        "rename_search": r".*第(\d+)集.*",
        "rename_replace": r"S01E\1.mkv",
        "weekdays": [0, 3],
    },
)
ps_id = (ps_created.get("data") or {}).get("id")
print(f"       追更任务 id={ps_id}")
ps_list = call("GET", "/pan-subscribes", token=token)
print(f"       追更任务共 {ps_list.get('total')} 个（失效 {ps_list.get('invalid')}）")
if ps_id:
    call("PATCH", f"/pan-subscribes/{ps_id}", token=token, body={"name": "冒烟测试追更（改）"})
    checked = call("POST", f"/pan-subscribes/{ps_id}/check", token=token)
    print(f"       单条巡检：{checked.get('message')}")
call("POST", "/pan-subscribes/check-all?limit=10", token=token)
call("PATCH", "/pan-subscribes/999999", token=token, body={"name": "x"}, expect=(404,))
call("POST", "/pan-subscribes/999999/check", token=token, expect=(404,))
call("GET", "/pan-subscribes", expect=(401,))

print("\n" + "=" * 70)
print("9g) 刮削与洗版：媒体库补刮 / 订阅洗版试算")
print("=" * 70)
scraped = call("POST", "/library/scrape", token=token, body={"limit": 20, "overwrite": False})
print(
    f"       补刮：扫描 {scraped.get('scanned')} 刮削 {scraped.get('scraped')} "
    f"跳过 {scraped.get('skipped')} 降级 {scraped.get('degraded')}"
)
if sub_id:
    up = call("POST", f"/subscribes/{sub_id}/upgrade", token=token, body={"dry_run": True})
    print(f"       洗版试算：{up.get('message')}")
batch_up = call("POST", "/subscribes/upgrade-all", token=token, body={"dry_run": False})
print(f"       批量洗版：{batch_up.get('message')}")
call("POST", "/subscribes/999999/upgrade", token=token, body={"dry_run": True}, expect=(404,))

print("\n" + "=" * 70)
print("9h) 设置页新配置组（刮削 / STRM / 分享追更与洗版）")
print("=" * 70)
settings_body = call("GET", "/system/settings", token=token)
titles = [group["title"] for group in settings_body.get("groups") or []]
print(f"       配置分组 {len(titles)} 个：{titles}")
for expected in ("刮削与分类", "STRM 同步", "分享追更与洗版"):
    ok = expected in titles
    results.append((ok, "CHECK", f"设置分组「{expected}」", 200 if ok else 0))
    print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  设置分组「{expected}」")

print("\n" + "=" * 70)
print("9i) 可编辑设置：读取元信息 / 在线修改 / 非法值拒绝 / 恢复默认")
print("=" * 70)
settings_body = call("GET", "/system/settings", token=token)
editable = [
    item
    for group in settings_body.get("groups") or []
    for item in group.get("items") or []
    if item.get("editable")
]
print(f"       可编辑配置 {settings_body.get('editable_total')} 项（分组内 {len(editable)} 项）")
ok = bool(editable) and all("type" in item for item in editable)
results.append((ok, "CHECK", "可编辑项均带 type 元信息", 200 if ok else 0))
print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  可编辑项均带 type 元信息")

# 改一个「运行期读取即生效」的键，然后确认读回来是新值
updated = call("PUT", "/system/settings", token=token, body={"values": {"RANKING_MAX_PER_RUN": 7}})
print(f"       改配置：{updated.get('message')}")
after = call("GET", "/system/settings", token=token)
current = next(
    (
        item
        for group in after.get("groups") or []
        for item in group.get("items") or []
        if item.get("key") == "RANKING_MAX_PER_RUN"
    ),
    {},
)
ok = str(current.get("value")) == "7" and current.get("overridden") is True
results.append((ok, "CHECK", "改动已生效且标记为 overridden", 200 if ok else 0))
print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  改动已生效且标记为 overridden（当前 {current.get('value')}）")

# 白名单外的键、非法枚举值、非法 cron 都必须整体拒绝
call("PUT", "/system/settings", token=token, body={"values": {"SECRET_KEY": "x"}}, expect=(400,))
call("PUT", "/system/settings", token=token, body={"values": {"DATA_DIR": "/tmp"}}, expect=(400,))
call("PUT", "/system/settings", token=token,
     body={"values": {"DOWNLOADER_STRATEGY": "不存在"}}, expect=(400,))
call("POST", "/system/settings/reset", token=token, body={"keys": ["RANKING_MAX_PER_RUN"]})
call("POST", "/system/settings/reset", token=token, body={"keys": None})
call("GET", "/system/settings", expect=(401,))

print("\n" + "=" * 70)
print("9j) 用户与权限：CRUD / 三档角色 / 403 边界 / 自我保护")
print("=" * 70)
users = call("GET", "/users", token=token)
print(f"       现有用户 {users.get('total')} 个，角色档位 {[r['value'] for r in users.get('roles') or []]}")

viewer = call("POST", "/users", token=token,
              body={"username": "cf_smoke_viewer", "password": "smoke-pass", "role": "viewer",
                    "note": "冒烟测试账号"})
viewer_id = (viewer.get("data") or {}).get("id")
operator = call("POST", "/users", token=token,
                body={"username": "cf_smoke_operator", "password": "smoke-pass", "role": "operator"})
operator_id = (operator.get("data") or {}).get("id")
print(f"       新建 viewer id={viewer_id} / operator id={operator_id}")

# is_superuser 必须由 role 推导，不能由前端说了算
ok = (viewer.get("data") or {}).get("is_superuser") is False
results.append((ok, "CHECK", "viewer 的 is_superuser 为 false", 200 if ok else 0))
print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  viewer 的 is_superuser 为 false")

call("POST", "/users", token=token,
     body={"username": "cf_smoke_viewer", "password": "smoke-pass", "role": "viewer"}, expect=(400,))

viewer_auth = call("POST", "/auth/login", form={"username": "cf_smoke_viewer", "password": "smoke-pass"})
viewer_token = viewer_auth.get("access_token")
op_auth = call("POST", "/auth/login", form={"username": "cf_smoke_operator", "password": "smoke-pass"})
op_token = op_auth.get("access_token")
ok = viewer_auth.get("role") == "viewer" and op_auth.get("role") == "operator"
results.append((ok, "CHECK", "登录响应带正确角色", 200 if ok else 0))
print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  登录响应带正确角色")

if viewer_token:
    # viewer 只读：能看，不能写
    call("GET", "/subscribes", token=viewer_token)
    call("GET", "/rule-groups", token=viewer_token)
    call("POST", "/subscribes", token=viewer_token, body={"title": "viewer 不该建成功"}, expect=(403,))
    call("GET", "/users", token=viewer_token, expect=(403,))
if op_token:
    # operator 能干活，但改不了系统配置与用户
    call("GET", "/site-health", token=op_token)
    call("PUT", "/system/settings", token=op_token,
         body={"values": {"RANKING_MAX_PER_RUN": 9}}, expect=(403,))
    call("GET", "/users", token=op_token, expect=(403,))
    call("POST", "/rule-groups", token=op_token,
         body={"name": "operator 不该能建", "levels": [{"resolution": "1080p"}]}, expect=(403,))

# 自我保护：不能删自己；最后一个管理员不能降级
admin_id = next((item["id"] for item in users.get("items") or [] if item["username"] == "admin"), None)
if admin_id:
    call("DELETE", f"/users/{admin_id}", token=token, expect=(400,))
    call("PATCH", f"/users/{admin_id}", token=token, body={"role": "operator"}, expect=(400,))
if viewer_id:
    call("PATCH", f"/users/{viewer_id}", token=token, body={"note": "改过备注", "role": "operator"})
call("PATCH", "/users/999999", token=token, body={"note": "x"}, expect=(404,))
call("GET", "/users", expect=(401,))

print("\n" + "=" * 70)
print("9k) 站点健康巡检：概览 / 历史 / 单站探测 / 批量探测")
print("=" * 70)
health = call("GET", "/site-health", token=token)
hdata = health.get("data") or health
print(
    f"       健康概览：共 {health.get('total')} 站"
    f"（正常 {hdata.get('ok')} / 降级 {hdata.get('degraded')} / 故障 {hdata.get('down')}）"
)
call("GET", "/site-health/records?limit=10", token=token)
sites_list = call("GET", "/sites", token=token)
first_site = next(iter(items_of(sites_list)), {})
if first_site.get("id"):
    checked = call("POST", f"/site-health/check/{first_site['id']}", token=token)
    print(f"       单站探测 #{first_site['id']}：{checked.get('message') or checked.get('status')}")
call("POST", "/site-health/check/999999", token=token, expect=(404,))
call("GET", "/site-health", expect=(401,))

print("\n" + "=" * 70)
print("9l) 榜单自动订阅：规则 CRUD / 试算 / 执行（dry-run 不留垃圾订阅）")
print("=" * 70)
rank_created = call(
    "POST",
    "/ranking-rules",
    token=token,
    body={
        "name": "冒烟测试榜单",
        "source": "tmdb_trending",
        "media_type": "tv",
        "limit": 3,
        "min_vote": 7.0,
        "enabled": False,
    },
)
rank_id = (rank_created.get("data") or {}).get("id")
print(f"       榜单规则 id={rank_id}")
rank_list = call("GET", "/ranking-rules", token=token)
print(f"       榜单规则共 {rank_list.get('total')} 条，来源 {len(rank_list.get('sources') or [])} 个")
call("POST", "/ranking-rules", token=token, body={"name": "x", "source": "不存在的榜"}, expect=(400,))
if rank_id:
    call("PATCH", f"/ranking-rules/{rank_id}", token=token, body={"limit": 5})
    preview = call("POST", f"/ranking-rules/{rank_id}/preview", token=token)
    print(f"       榜单试算：候选 {preview.get('total')} 条（无 TMDB Key 时为 0 属正常）")
    call("POST", f"/ranking-rules/{rank_id}/run", token=token, body={"dry_run": True})
call("PATCH", "/ranking-rules/999999", token=token, body={"limit": 5}, expect=(404,))
call("GET", "/ranking-rules", expect=(401,))

print("\n" + "=" * 70)
print("9m) 过滤规则组：内置模板 / CRUD / 试算分层排序")
print("=" * 70)
groups = call("GET", "/rule-groups", token=token)
print(
    f"       规则组共 {groups.get('total')} 个，当前默认：{groups.get('default') or '（未设默认组）'}"
)
# 内置模板必须至少有一个（init_db 建的 4 个），且默认组最多一个
builtin = groups.get("items") or []
ok = len(builtin) >= 4 and sum(1 for item in builtin if item.get("is_default")) <= 1
results.append((ok, "CHECK", "内置规则组模板存在且默认组唯一", 200 if ok else 0))
print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  内置规则组模板存在且默认组唯一")
group_created = call(
    "POST",
    "/rule-groups",
    token=token,
    body={
        "name": "冒烟测试规则组",
        "description": "1080p 中字优先",
        "levels": [
            {"name": "1080p 中字", "resolution": "1080p", "include": "中字"},
            {"name": "1080p", "resolution": "1080p"},
            {"name": "4K", "resolution": "2160p"},
        ],
        "accept_unmatched": True,
    },
)
group_id = (group_created.get("data") or {}).get("id")
print(f"       规则组 id={group_id}，层数 {(group_created.get('data') or {}).get('level_count')}")
call("POST", "/rule-groups", token=token, body={"name": "没有层级", "levels": []}, expect=(400,))
call("POST", "/rule-groups", token=token,
     body={"name": "冒烟测试规则组", "levels": [{"resolution": "1080p"}]}, expect=(400,))
if group_id:
    call("GET", f"/rule-groups/{group_id}", token=token)
    call("PATCH", f"/rule-groups/{group_id}", token=token, body={"description": "改过说明"})
    previewed = call(
        "POST",
        f"/rule-groups/{group_id}/preview",
        token=token,
        body={
            "resources": [
                {"title": "某剧 S01E01 2160p WEB-DL", "size": "8GB", "seeders": 50},
                {"title": "某剧 S01E01 1080p WEB-DL 中字", "size": "2GB", "seeders": 30},
                {"title": "某剧 S01E01 480p TVRip", "size": "300MB", "seeders": 1},
            ]
        },
    )
    top = next(iter(previewed.get("items") or []), {})
    print(f"       试算命中：首位「{top.get('title')}」层级 {top.get('rule_level')}")
    # 分层的意义：1080p 中字要压过 4K 无字幕
    ok = top.get("rule_level") == 0 and "中字" in str(top.get("title"))
    results.append((ok, "CHECK", "1080p 中字排在 4K 之前", 200 if ok else 0))
    print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  1080p 中字排在 4K 之前")
call("PATCH", "/rule-groups/999999", token=token, body={"description": "x"}, expect=(404,))
call("GET", "/rule-groups", expect=(401,))

print("\n" + "=" * 70)
print("9n) 调度：v1.5.0 两个新任务已注册且可手动执行")
print("=" * 70)
schedules = call("GET", "/schedules", token=token)
keys = [item.get("key") for item in schedules.get("items") or []]
print(f"       内置任务 {len(keys)} 个：{keys}")
for expected in ("site_health", "ranking"):
    ok = expected in keys
    results.append((ok, "CHECK", f"任务「{expected}」已注册", 200 if ok else 0))
    print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  任务「{expected}」已注册")
# cron 非法必须被挡住，而不是等调度器起不来才报错
call("PUT", "/schedules/site_health", token=token,
     body={"cron": "这不是 cron"}, expect=(400, 422))
call("POST", "/schedules/site_health/reset", token=token)

print("\n" + "=" * 70)
print("9c) 网盘账号登录：能力声明 / 扫码会话 / Cookie 校验（v1.8.0）")
print("=" * 70)
# 能力清单：夸克不支持扫码这件事由后端声明，前端不写死
provs = call("GET", "/pan/login/providers", token=token)
prov_rows = provs.get("data") or []
by_name = {p.get("provider"): p for p in prov_rows}
print(f"       登录能力 {len(prov_rows)} 项")
for p in prov_rows:
    print(
        f"         {p.get('label')!s:10} 扫码={'✓' if p.get('qrcode') else '✗'}"
        f" Cookie={'✓' if p.get('cookie') else '✗'}  {str(p.get('note'))[:40]}"
    )
assert by_name.get("pan115", {}).get("qrcode") is True, "115 应支持扫码"
assert by_name.get("baidu", {}).get("qrcode") is True, "百度应支持扫码"
# 夸克登录需签名公参，逆向属对抗风控，明确不做（ADR-38）
assert by_name.get("quark", {}).get("qrcode") is False, "夸克不应声明支持扫码"
assert all(p.get("cookie") for p in prov_rows), "所有网盘都应支持 Cookie 导入"

# 夸克要扫码必须被拒（而不是给一个用不了的二维码）
call("POST", "/pan/login/qrcode", token=token, body={"provider": "quark"}, expect=(400,))
call("POST", "/pan/login/qrcode", token=token, body={"provider": "nosuchpan"}, expect=(400,))
# 假 token 轮询 → 404（会话只存内存，不存在就是不存在）
call("GET", "/pan/login/qrcode/definitely-not-a-real-token", token=token, expect=(404,))
# 没扫码就想保存 → 必须拒绝，不能写一份空凭据进库
call("POST", "/pan/login/complete", token=token,
     body={"token": "definitely-not-a-real-token"}, expect=(400,))
# 空 Cookie 不写库（min_length=1 由 schema 挡掉 → 422）
call("POST", "/pan/login/cookie", token=token,
     body={"provider": "quark", "cookie": "   "}, expect=(400, 422))
# 明显无效的 Cookie → 拒绝写库（校验不过不落库，ADR-40）
call("POST", "/pan/login/cookie", token=token,
     body={"provider": "quark", "cookie": "totally=invalid"}, expect=(400,))
# 对外接口**不接受** verify=false：否则带上它就能把任意字符串写成 Cookie，
# 把 ADR-40 直接绕过去。多余字段被忽略，校验照样执行 → 仍是 400。
call("POST", "/pan/login/cookie", token=token,
     body={"provider": "quark", "cookie": "totally=invalid", "verify": False},
     expect=(400,))
# 只校验不保存：无论有效与否都是 200，success 字段给结论
vr = call("POST", "/pan/login/verify", token=token,
          body={"provider": "quark", "cookie": "totally=invalid"})
print(f"       Cookie 自查结论 success={vr.get('success')} {str(vr.get('message'))[:40]}")
call("POST", "/pan/login/verify", token=token,
     body={"provider": "nosuchpan", "cookie": "x=1"})
# 登录类接口必须要鉴权（凭据写入是高危操作，至少 operator）
call("GET", "/pan/login/providers", expect=(401,))
call("POST", "/pan/login/qrcode", body={"provider": "pan115"}, expect=(401,))
call("POST", "/pan/login/cookie",
     body={"provider": "quark", "cookie": "x=1"}, expect=(401,))
# 注意：这里**不能**用 viewer_token 测 403 边界——§9j 的角色测试已经把
# cf_smoke_viewer 提权成 operator 了，此时它不再是只读账号。
# 网盘登录接口的 operator 门槛由 §9j 的通用 403 边界用例覆盖。

print("\n" + "=" * 70)
print("9o) 下载器管理：字段清单 / CRUD / 参数校验（v1.10.0 从站点管理搬到设置页）")
print("=" * 70)
dl_schema = call("GET", "/downloaders/schema", token=token)
dl_items = dl_schema.get("items") or []
spec_by_provider = {item.get("provider"): item for item in dl_items}
print(f"       下载器种类 {len(dl_items)} 个")
for item in dl_items:
    print(
        f"         {item.get('display_name')!s:16}"
        f" {len(item.get('fields') or [])} 个可配字段"
        f"  {str(item.get('note'))[:30]}"
    )
    # display_name/note 是前端表单标题与说明的唯一来源，漏下发界面就是空白
    assert item.get("display_name"), f"{item.get('provider')} 缺少 display_name"
    assert item.get("note"), f"{item.get('provider')} 缺少用途说明 note"
    for field in item.get("fields") or []:
        assert field.get("label"), f"{item.get('provider')}.{field.get('key')} 缺少 label"
        assert field.get("type"), f"{item.get('provider')}.{field.get('key')} 缺少 type"
        assert field.get("target") in ("column", "option"), \
            f"{item.get('provider')}.{field.get('key')} 的 target 非法"
        if field.get("type") == "choice":
            assert field.get("choices"), \
                f"{item.get('provider')}.{field.get('key')} 是枚举却没给 choices"
for expected in ("qbittorrent", "transmission", "aria2", "ytdlp"):
    assert expected in spec_by_provider, f"schema 缺少 {expected}"
# 反向确认「假配置项」不会回归：aria2 不读 username，ytdlp 不读 url/password
aria2_keys = {f.get("key") for f in spec_by_provider["aria2"]["fields"]}
ytdlp_keys = {f.get("key") for f in spec_by_provider["ytdlp"]["fields"]}
assert "username" not in aria2_keys, "aria2 不读 username，不该出现在表单里"
assert not ({"url", "username", "password"} & ytdlp_keys), \
    "yt-dlp 是本地进程，不读 url/username/password（靠 cookie_file 登录）"
assert "cookie_file" in ytdlp_keys, "yt-dlp 必须能配 cookie_file"

# 列表：站点管理页已不再管下载器，这里是唯一入口
dl_list = call("GET", "/downloaders", token=token)
print(f"       现有下载器 {len(dl_list.get('items') or [])} 个")

# 新建（默认不启用：连不上的下载器一旦启用会污染真实下载流程）
dl_created = call(
    "POST", "/downloaders", token=token,
    body={
        "name": "冒烟-下载器",
        "provider": "qbittorrent",
        "enabled": False,
        "values": {"url": "http://127.0.0.1:9/", "username": "smoke",
                   "password": "smoke-pass", "priority": 7, "timeout": 20},
    },
)
dl_id = (dl_created.get("data") or {}).get("id")
dl_values = (dl_created.get("data") or {}).get("values") or {}
print(f"       新建下载器 id={dl_id} 密码已脱敏={dl_values.get('password_set')}")
assert dl_values.get("password_set") is True, "回显应只给 password_set 布尔"
assert "password" not in dl_values, "回显不能带明文密码"

if dl_id:
    # 参数校验：越界/非法枚举/非数字都必须 400，而不是静默写进库
    call("PATCH", f"/downloaders/{dl_id}", token=token,
         body={"name": "冒烟-下载器", "provider": "qbittorrent",
               "enabled": False, "values": {"timeout": "abc"}}, expect=(400,))
    call("PATCH", f"/downloaders/{dl_id}", token=token,
         body={"name": "冒烟-下载器", "provider": "qbittorrent",
               "enabled": False, "values": {"priority": 9999}}, expect=(400,))
    # 未登记的键要被丢弃，不能往 options 里堆垃圾
    patched = call("PATCH", f"/downloaders/{dl_id}", token=token,
                   body={"name": "冒烟-下载器改名", "provider": "qbittorrent",
                         "enabled": False,
                         "values": {"category": "cineflow", "不存在的键": "x"}})
    pv = (patched.get("data") or {}).get("values") or {}
    print(f"       改名后 category={pv.get('category')} 垃圾键被丢弃={'不存在的键' not in pv}")
    assert "不存在的键" not in pv, "未登记的键不该写进 options"
    # 测试连通性：连不上是预期结果，接口本身必须 200 并给出结论
    dl_test = call("POST", f"/downloaders/{dl_id}/test", token=token, expect=(200, 400))
    print(f"       连通性结论 success={dl_test.get('success')} {str(dl_test.get('message'))[:40]}")

# 边界：非下载器 provider 不能从这里建；不存在的 id 一律 404
call("POST", "/downloaders", token=token,
     body={"name": "冒烟-非法", "provider": "torznab", "enabled": False, "values": {}},
     expect=(400,))
call("PATCH", "/downloaders/99999999", token=token,
     body={"name": "x", "provider": "qbittorrent", "enabled": False, "values": {}},
     expect=(404,))
call("DELETE", "/downloaders/99999999", token=token, expect=(404,))
# 鉴权：schema/列表要登录，写操作要管理员
call("GET", "/downloaders/schema", expect=(401,))
call("GET", "/downloaders", expect=(401,))
call("POST", "/downloaders",
     body={"name": "x", "provider": "qbittorrent", "enabled": False, "values": {}},
     expect=(401,))

print("\n" + "=" * 70)
print("9p) 资源类型→下载方式路由（v1.13.0）")
print("=" * 70)
routing = call("GET", "/downloads/routing", token=token)
routes = items_of(routing) or []
print(f"       资源类型路由 {len(routes)} 项")
kinds = {entry.get("kind") for entry in routes}
# 五种资源类型都必须有明确结论，缺一种就意味着那类资源会走进"静默 pending"
assert {"magnet", "torrent", "pan", "direct", "webvideo"} <= kinds, kinds
for entry in routes:
    print(f"       - {entry.get('kind'):9} ready={entry.get('ready')!s:5} "
          f"可用={entry.get('downloaders')} {str(entry.get('hint'))[:44]}")
    # 每一类都要能回答「缺了该怎么办」，且提示必须可行动（说清去哪儿加什么）
    assert entry.get("hint"), f"{entry.get('kind')} 缺少提示"
    assert "设置" in entry["hint"], f"{entry.get('kind')} 的提示没说去哪儿配"
    assert entry.get("providers"), f"{entry.get('kind')} 没有候选下载器"
    if not entry.get("ready"):
        assert entry.get("reason"), f"{entry.get('kind')} 不可用却没给原因"
# yt-dlp 是唯一默认启用的下载器，所以网页视频必须 ready、网盘/直链必须不 ready
by_kind = {entry["kind"]: entry for entry in routes}
assert by_kind["webvideo"]["providers"] == ["ytdlp"], "网页视频只能交给 yt-dlp"
assert "aria2" in by_kind["pan"]["providers"], "网盘落地要靠 aria2"
assert "qbittorrent" in by_kind["magnet"]["providers"], "磁力要交给 BT 下载器"
assert "ytdlp" not in by_kind["magnet"]["providers"], "yt-dlp 下不了磁力，不该出现在候选里"
call("GET", "/downloads/routing", expect=(401,))

print("\n" + "=" * 70)
print("9q) 内置 AI 站点分析（v1.13.0，默认关闭）")
print("=" * 70)
ai_cfg = call("GET", "/ai/config", token=token)
ai_data = ai_cfg.get("data") or {}
print(f"       ready={ai_data.get('ready')} enabled={ai_data.get('enabled')} "
      f"model={ai_data.get('model')} key={ai_data.get('api_key_hint')}")
# 默认必须是关的：开启才会把站点页面正文发给第三方模型
assert ai_data.get("enabled") is False, "内置 AI 默认必须关闭（外发数据要显式同意）"
assert ai_data.get("ready") is False, "未配置时不该报告 ready"
assert "设置" in str(ai_data.get("reason")), "不可用的理由必须能指导操作"
# 密钥绝不回显原文，只给长度
assert "api_key_set" in ai_data and "api_key_hint" in ai_data
print(f"       可选接入方案 {len(ai_data.get('providers') or [])} 种")
# 未启用时 analyze 必须 400 且说清去哪配（而不是 500 或静默失败）
denied = call("POST", "/ai/analyze", token=token,
              body={"url": "https://example.com", "keyword": "流浪地球"}, expect=(400,))
print(f"       未启用时 analyze 被拒：{str(denied.get('detail'))[:60]}")
# 入口参数校验
call("POST", "/ai/analyze", token=token, body={"url": "x"}, expect=(422,))
# verify 不需要模型（本地真跑一次搜索），但野 provider 必须被拒
bad_verify = call("POST", "/ai/verify", token=token,
                  body={"suggestion": {"url": "https://example.com", "provider": "magic_parser"},
                        "keyword": "测试"})
print(f"       野 provider 试跑结论 success={(bad_verify.get('data') or {}).get('success')}")
# apply：已注册但不在 AI 清单里的 provider 也必须被拦（否则能建出「kind=indexer 的下载器」）
call("POST", "/ai/apply", token=token,
     body={"suggestion": {"url": "https://example.com", "provider": "qbittorrent"},
           "name": "冒烟-AI非法站点"}, expect=(400,))
# 鉴权：全部端点都要登录
call("GET", "/ai/config", expect=(401,))
call("POST", "/ai/analyze", body={"url": "https://example.com"}, expect=(401,))
call("POST", "/ai/verify",
     body={"suggestion": {"url": "https://example.com", "provider": "rss"}}, expect=(401,))
call("POST", "/ai/apply",
     body={"suggestion": {"url": "https://example.com", "provider": "rss"}, "name": "x"},
     expect=(401,))

print("\n" + "=" * 70)
print("9r) 在线影视站（MacCMS）预设：默认禁用且如实标注（v1.13.0）")
print("=" * 70)
# 「从模板添加」的选单必须包含 maccms，否则用户接这类站还得手填 provider
presets = call("GET", "/sites/presets", token=token)
maccms_presets = [p for p in (items_of(presets) or []) if p.get("provider") == "maccms"]
print(f"       模板库 maccms 预设 {len(maccms_presets)} 个")
assert maccms_presets, "「从模板添加」里必须能选到 MacCMS 在线影视站"
for item in maccms_presets:
    desc = str(item.get("description") or "")
    print(f"       - {item.get('name')} · {desc[:52]}")
    # 这类站约 92% 是会员正片。模板说明必须把边界写清楚，
    # 否则用户会以为「加了站却下不了」是 bug，然后反复折腾配置
    assert "92" in desc or "会员" in desc, "模板说明必须写明会员正片占比这个边界"

# 已写进库的两个 maccms 站点必须默认禁用，且 note 里写明原因
sites_all = call("GET", "/sites", token=token)
site_list = sites_all if isinstance(sites_all, list) else (items_of(sites_all) or [])
maccms_sites = [s for s in site_list if s.get("provider") == "maccms"]
print(f"       内置 maccms 站点 {len(maccms_sites)} 个")
for item in maccms_sites:
    print(f"       - {item.get('name')} enabled={item.get('enabled')}")
    assert not item.get("enabled"), f"{item.get('name')} 必须默认禁用"

print("\n" + "=" * 70)
print("9s) RSS 追新：方言 / 预览 / CRUD / 增量与试运行（v1.18.0）")
print("=" * 70)
# 方言清单必须逐站说明字段差异 —— 界面靠它告诉用户"这个站为什么没有做种数"，
# 否则用户只会以为是程序坏了
dialects = call("GET", "/rss-feeds/dialects", token=token)
dialect_keys = [item.get("key") for item in dialects.get("items") or []]
print(f"       支持方言 {len(dialect_keys)} 种：{dialect_keys}")
for expected in ("mikan", "nyaa", "dmhy", "generic"):
    ok = expected in dialect_keys
    results.append((ok, "CHECK", f"RSS 方言「{expected}」已适配", 200 if ok else 0))
    print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  RSS 方言「{expected}」已适配")
for item in dialects.get("items") or []:
    assert item.get("note"), f"方言 {item.get('key')} 缺字段差异说明"

# 预览拉不通时必须如实 success=false 并给可操作提示，
# 不能"空列表 + success=true"让用户以为源是好的
bad_preview = call("POST", "/rss-feeds/preview", token=token,
                   body={"url": "https://example.invalid/none.xml"})
print(f"       坏地址预览：success={bad_preview.get('success')} msg={str(bad_preview.get('message'))[:40]}")
assert bad_preview.get("success") is False, "拉不通的 RSS 不能报成功"
assert bad_preview.get("message"), "预览失败必须给出原因"

rss_id = None
created_rss = call("POST", "/rss-feeds", token=token,
                   body={"name": "冒烟 RSS 源", "url": "https://example.invalid/smoke-feed.xml",
                         "aggregate": True, "max_per_run": 3})
rss_id = (created_rss.get("data") or {}).get("id")
print(f"       新建 RSS 源 id={rss_id} dialect={(created_rss.get('data') or {}).get('dialect')}")

# 同一地址重复添加要返回既有记录，而不是 500（唯一约束撞库）
dup = call("POST", "/rss-feeds", token=token,
           body={"name": "重复", "url": "https://example.invalid/smoke-feed.xml"})
same = (dup.get("data") or {}).get("id") == rss_id
results.append((same, "CHECK", "重复 RSS 地址返回既有记录", 200 if same else 0))
print(f"{'PASS' if same else 'FAIL'} 200 CHECK  重复 RSS 地址返回既有记录")

rss_list = call("GET", "/rss-feeds", token=token)
print(f"       RSS 源共 {rss_list.get('total')} 个，统计 {rss_list.get('stats')}")

# 拉不通的源要计失败次数，而不是静默"成功但 0 条"
checked = call("POST", f"/rss-feeds/{rss_id}/check", token=token)
print(f"       巡检结果：success={checked.get('success')} msg={str(checked.get('message'))[:50]}")
assert checked.get("success") is False, "拉不通的源不能报成功"

# reset_failures 必须一并恢复启用，否则用户点了重置发现还是不跑
call("PATCH", f"/rss-feeds/{rss_id}", token=token, body={"enabled": False})
restored = call("PATCH", f"/rss-feeds/{rss_id}", token=token,
                body={"reset_failures": True, "aggregate": False, "max_per_run": 7})
rdata = restored.get("data") or {}
ok = rdata.get("enabled") is True and rdata.get("failure_count") == 0 and rdata.get("max_per_run") == 7
results.append((ok, "CHECK", "reset_failures 清零并恢复启用", 200 if ok else 0))
print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  reset_failures 清零并恢复启用")

# 试运行不能写回 guid（否则试跑一次真巡检就什么都不下了）
dry = call("POST", "/rss-feeds/check-all?dry_run=true", token=token)
print(f"       试运行全部：检查 {dry.get('checked')} 个源，dry_run={dry.get('dry_run')}")
assert dry.get("dry_run") is True, "check-all?dry_run=true 必须标记为试运行"

# 404 边界
call("POST", "/rss-feeds/999999/check", token=token, expect=(404,))
call("PATCH", "/rss-feeds/999999", token=token, body={"name": "x"}, expect=(404,))
call("DELETE", "/rss-feeds/999999", token=token, expect=(404,))

print("\n" + "=" * 70)
print("9t) 更新检测：结论必须可核对（v1.18.0）")
print("=" * 70)
# 仓库没有任何 Release 时必须退回读主干，否则会永远回答"已是最新版本"
upd = call("GET", "/system/update/check", token=token)
print(f"       当前 {upd.get('current')} / 上游 {upd.get('latest') or '未知'}"
      f" / 依据 {upd.get('source')} / 形态 {upd.get('mode')}")
print(f"       结论：{upd.get('message')}")
for field in ("current", "mode", "source", "has_update", "can_apply"):
    ok = field in upd
    results.append((ok, "CHECK", f"更新结果含 {field}", 200 if ok else 0))
    print(f"{'PASS' if ok else 'FAIL'} 200 CHECK  更新结果含 {field}")
assert upd.get("mode") in ("source", "docker"), upd.get("mode")
# 容器部署不能声称能自更新（我们刻意不挂 docker.sock）
if upd.get("mode") == "docker":
    assert upd.get("can_apply") is False, "容器部署不能声称可一键更新"

applied = call("POST", "/system/update/apply", token=token)
print(f"       执行更新：success={applied.get('success')} msg={str(applied.get('message'))[:60]}")
# 无论成败都必须给出下一步：源码部署说清为什么没执行，容器部署给出 compose 命令
assert applied.get("message"), "更新结果必须带说明"
if applied.get("success") is False and upd.get("mode") == "docker":
    assert applied.get("commands"), "容器部署必须给出可复制的更新命令"

print("\n" + "=" * 70)
print("10) 清理测试数据")
print("=" * 70)
if dl_id:
    call("DELETE", f"/downloaders/{dl_id}", token=token)
if sub_id:
    call("DELETE", f"/subscribes/{sub_id}", token=token)
if ps_id:
    call("DELETE", f"/pan-subscribes/{ps_id}", token=token)
if rank_id:
    call("DELETE", f"/ranking-rules/{rank_id}", token=token)
if rss_id:
    call("DELETE", f"/rss-feeds/{rss_id}", token=token)
if group_id:
    call("DELETE", f"/rule-groups/{group_id}", token=token)
# 测试账号必须删掉，否则下次跑冒烟会撞用户名
if viewer_id:
    call("DELETE", f"/users/{viewer_id}", token=token)
if operator_id:
    call("DELETE", f"/users/{operator_id}", token=token)

failed = [item for item in results if not item[0]]
print("\n" + "=" * 70)
print(f"冒烟测试结果：{len(results) - len(failed)}/{len(results)} 通过")
if failed:
    print("失败项：")
    for _, method, path, status in failed:
        print(f"  {status} {method} {path}")
print("=" * 70)
sys.exit(1 if failed else 0)
