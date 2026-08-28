"""真实站点端到端验证：启用 mukaku → 搜索 → 订阅 → 追新雷达匹配。

只做「dry_run」不真正下载，不需要下载器。
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
#: 服务地址，可用 CF_BASE_URL / CF_PORT 覆盖
BASE = os.environ.get("CF_BASE_URL") or f"http://127.0.0.1:{os.environ.get('CF_PORT', '6060')}"
API = BASE + "/api/v1"


def call(method, path, *, body=None, form=None, token=None):
    url = API + path
    data, headers = None, {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8", "replace") or "{}")


token = call("POST", "/auth/login", form={"username": "admin", "password": "cineflow"})[1][
    "access_token"
]

print("=" * 70)
print("1) 启用 Mukaku 站点")
print("=" * 70)
sites = call("GET", "/sites", token=token)[1]
mukaku = next((s for s in sites if s["provider"] == "mukaku"), None)
if not mukaku:
    status, mukaku = call("POST", "/sites/presets/mukaku/apply", token=token)
    print("   预设建站:", status)
site_id = mukaku["id"]
print("   站点:", mukaku["name"], "id=", site_id)
call("PATCH", f"/sites/{site_id}", token=token, body={"enabled": True})

status, test = call("POST", f"/sites/{site_id}/test", token=token)
print("   连通性测试:", status, test.get("message"))

print()
print("=" * 70)
print("2) 真实搜索（磁力 + 网盘）")
print("=" * 70)
KEYWORD = "师兄太稳健"
status, results = call(
    "GET", "/search?keyword=" + urllib.parse.quote(KEYWORD) + "&media_type=tv", token=token
)
items = results if isinstance(results, list) else results.get("items", [])
print(f"   搜索「{KEYWORD}」返回 {len(items)} 条")
kinds = {}
for item in items:
    kinds[item.get("kind")] = kinds.get(item.get("kind"), 0) + 1
print("   资源类型分布:", kinds)
for item in items[:5]:
    meta = item.get("meta") or {}
    print(
        f"     [{item.get('kind'):7}] S{meta.get('season')}E{meta.get('episodes')} "
        f"{meta.get('resolution')} {meta.get('quality')} "
        f"score={item.get('score')} | {(item.get('title') or '')[:52]}"
    )
pan = [i for i in items if i.get("kind") == "pan"]
if pan:
    print(f"   网盘示例: {pan[0]['link'][:60]}")

print()
print("=" * 70)
print("3) 建立订阅并跑追新雷达（dry_run）")
print("=" * 70)
status, sub = call(
    "POST",
    "/subscribes",
    token=token,
    body={"title": KEYWORD, "media_type": "tv", "season": 1, "total_episodes": 20},
)
print("   建立订阅:", status, sub.get("id"), sub.get("title"), "| detail:", sub.get("detail", ""))
sub_id = sub.get("id")

status, radar = call("POST", "/radar/run?dry_run=true", token=token)
data = radar.get("data") or {}
print("   雷达:", status)
print(
    f"   最新流资源 {data.get('resources')} 条 / 活跃订阅 {data.get('subscribes')} / "
    f"命中订阅 {data.get('matched')} / 待下载 {len(data.get('downloads') or [])}"
)
for entry in (data.get("downloads") or [])[:6]:
    print(
        f"     -> [{entry.get('subscribe')}] 集数={entry.get('episodes')} "
        f"score={entry.get('score')} | {(entry.get('title') or '')[:56]}"
    )
for skip in (data.get("skipped") or [])[:4]:
    print(f"     x [{skip.get('title')}] {skip.get('reason')}（候选 {skip.get('candidates')}）")

print()
print("=" * 70)
print("4) 清理")
print("=" * 70)
if sub_id:
    print("   删除订阅:", call("DELETE", f"/subscribes/{sub_id}", token=token)[0])
print("   停用站点:", call("PATCH", f"/sites/{site_id}", token=token, body={"enabled": False})[0])
