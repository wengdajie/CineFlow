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


def call(method, path, *, body=None, form=None, token=None, expect=(200, 201), extra_headers=None):
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
        with urllib.request.urlopen(request, timeout=40) as response:
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

print("\n" + "=" * 70)
print("5) 搜索（无启用站点时应优雅返回空）")
print("=" * 70)
search = call("GET", "/search?keyword=" + urllib.parse.quote("庆余年") + "&media_type=tv", token=token)
print(f"       结果 {len(items_of(search))} 条（未配站点时为 0 属正常）")

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
    call("POST", f"/subscribes/{sub_id}/run", token=token)
call("POST", "/subscribes/run-all", token=token)

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
else:
    print("       （未启用网盘，跳过目录类端点；下面仍验证降级提示）")

# 不存在的网盘要 404 而不是 500
call("GET", "/pan/files?site_id=999999", token=token, expect=(404,))
# 没有可用网盘或链接非法时要 400 并给出明确原因
call("POST", "/pan/save", token=token,
     body={"share_url": "https://pan.quark.cn/s/smoketest"}, expect=(200, 400))
transfer = call("POST", "/pan/transfer?limit=5", token=token)
print(f"       批量转存：待处理 {transfer.get('pending')} 成功 {transfer.get('saved')}")
# 未授权拦截
call("GET", "/pan", expect=(401,))
call("POST", "/pan/save", body={"share_url": "x"}, expect=(401,))

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
print("10) 清理测试数据")
print("=" * 70)
if sub_id:
    call("DELETE", f"/subscribes/{sub_id}", token=token)

failed = [item for item in results if not item[0]]
print("\n" + "=" * 70)
print(f"冒烟测试结果：{len(results) - len(failed)}/{len(results)} 通过")
if failed:
    print("失败项：")
    for _, method, path, status in failed:
        print(f"  {status} {method} {path}")
print("=" * 70)
sys.exit(1 if failed else 0)
