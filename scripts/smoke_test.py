"""对运行中的 CineFlow 实例做全接口冒烟测试。"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
BASE = "http://127.0.0.1:8611"
API = BASE + "/api/v1"
results = []


def call(method, path, *, body=None, form=None, token=None, expect=(200, 201)):
    url = path if path.startswith("http") else API + path
    data, headers = None, {}
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
