"""校验 README 中的事实性声明与代码实际情况是否一致。"""
import json
import pathlib
import re
import sys
import urllib.request

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

README = pathlib.Path("README.md").read_text(encoding="utf-8")
SCRIPTS_README = pathlib.Path("scripts/README.md").read_text(encoding="utf-8")
checks = []


def check(name, ok, detail=""):
    checks.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


# ---- Provider 数量与名称 ----
from app.providers.registry import list_providers, load_builtin_providers  # noqa: E402

load_builtin_providers()
providers = list_providers()
names = {p["name"] for p in providers}
check("Provider 总数 18", len(providers) == 18, f"实际 {len(providers)}")
check("README 声明 18 个 Provider", "18 个注册 Provider" in README)
for expected in ("api_generic", "html_generic", "mukaku", "pan_generic",
                 "torznab", "rss", "nyaa", "pansou"):
    check(f"Provider {expected} 已注册", expected in names)
    check(f"README 提及 {expected}", expected in README)

# ---- 数据库表 ----
from app.db.models import Base  # noqa: E402

check("12 张表", len(Base.metadata.tables) == 12, f"实际 {len(Base.metadata.tables)}")
check("README 声明 12 张表", "12 张表" in README)

# ---- 默认站点 ----
from app.db.init_db import DEFAULT_SITES  # noqa: E402

check("默认示例站点 8 条", len(DEFAULT_SITES) == 8, f"实际 {len(DEFAULT_SITES)}")
check("README 声明 8 条示例站点", "写入 8 条" in README)
check("示例站点全部默认禁用", all(not s.get("enabled", False) for s in DEFAULT_SITES))
check("默认站点含 mukaku", any(s["provider"] == "mukaku" for s in DEFAULT_SITES))
check("默认站点含 api_generic", any(s["provider"] == "api_generic" for s in DEFAULT_SITES))
check("默认站点含 html_generic", any(s["provider"] == "html_generic" for s in DEFAULT_SITES))

# ---- 配置项 ----
from app.core.config import settings  # noqa: E402

for key, default in (("RADAR_ENABLED", True), ("RADAR_INTERVAL_MINUTES", 15),
                     ("RADAR_LIMIT_PER_SITE", 100)):
    check(f"配置项 {key} 存在", hasattr(settings, key))
    check(f"配置项 {key} 默认值 {default}", getattr(settings, key) == default,
          f"实际 {getattr(settings, key)}")
    check(f"README 提及 CF_{key}", f"CF_{key}" in README)

# ---- 调度任务 ----
scheduler_src = pathlib.Path("app/services/scheduler.py").read_text(encoding="utf-8")
job_ids = re.findall(r'^JOB_\w+ = "([^"]+)"', scheduler_src, re.M)
check("内置调度任务 4 个", len(job_ids) == 4, str(job_ids))
check("README 声明 4 内置任务", "4 内置任务" in README)
check("雷达任务已注册", "cineflow.radar" in job_ids)

# ---- API 端点 ----
try:
    with urllib.request.urlopen("http://127.0.0.1:8611/openapi.json", timeout=15) as r:
        spec = json.loads(r.read().decode())
    total = sum(
        1
        for _, methods in spec["paths"].items()
        for m in methods
        if m.lower() in ("get", "post", "patch", "delete", "put")
    )
    check("API 端点 53 个", total == 53, f"实际 {total}")
    check("README 声明 53 个端点", "53 个端点" in README and "共 53 个" in README)
    paths = set(spec["paths"])
    for path in ("/api/v1/radar/run", "/api/v1/radar/feed", "/api/v1/radar/jobs",
                 "/api/v1/sites/presets", "/api/v1/sites/discover"):
        check(f"端点存在 {path}", path in paths)
except Exception as exc:
    check("API 端点校验（需先起服务）", False, str(exc)[:80])

# ---- router 数量 ----
router_src = pathlib.Path("app/api/router.py").read_text(encoding="utf-8")
router_count = router_src.count("api_router.include_router(")
check("router 10 个", router_count == 10, f"实际 {router_count}")
check("README 声明 10 个 router", "10 个 router" in README)

# ---- 前端页面 ----
app_js = pathlib.Path("web/assets/app.js").read_text(encoding="utf-8")
pages_block = app_js[app_js.index("const PAGES"): app_js.index("];", app_js.index("const PAGES"))]
page_count = pages_block.count("{ key:")
check("前端页面 9 个", page_count == 9, f"实际 {page_count}")
check("README 声明点检 9 个页面", "9 个页面" in README)
check("scripts/README 声明 9 个页面", "9 个前端页面" in SCRIPTS_README)
check("前端含追新雷达页", '"radar"' in app_js and "pageRadar" in app_js)
check("前端含站点发现弹窗", "discoverDialog" in app_js)
check("前端含预设选择器", "presetPicker" in app_js)
check("前端含 options JSON 编辑", "parseOptions" in app_js)

# ---- 测试文件 ----
test_files = sorted(p.name for p in pathlib.Path("tests").glob("test_*.py"))
check("测试文件 9 个", len(test_files) == 9, str(test_files))
check("README 声明 9 个测试文件", "9 个测试文件" in README)
for name in ("test_custom_sites.py", "test_radar.py"):
    check(f"README 提及 {name}", name in README)

# ---- 脚本 ----
for script in ("smoke_test.py", "ui_check.py", "demo_pipeline.py", "live_check.py",
               "verify_docs.py"):
    check(f"脚本存在 {script}", pathlib.Path("scripts", script).exists())
    check(f"scripts/README 提及 {script}", script in SCRIPTS_README)
check("README 声明五个验证脚本", "五个开发期验证工具" in README)
check("README 声明 67 项接口用例", "67 项真实 HTTP 接口用例" in README)
check("scripts/README 声明 67 项", "67 项接口用例" in SCRIPTS_README)

# ---- 服务模块 ----
for module in ("radar", "discovery", "presets"):
    check(f"服务模块 app/services/{module}.py 存在",
          pathlib.Path(f"app/services/{module}.py").exists())
    check(f"README 提及 {module} 服务", module in README)

# ---- 文档中的 JSON 示例必须合法 ----
blocks = re.findall(r"```json\n(.*?)```", README, re.S)
bad = []
for index, block in enumerate(blocks):
    try:
        json.loads(block)
    except Exception as exc:
        bad.append(f"#{index}: {exc}")
check(f"README 中 {len(blocks)} 个 JSON 示例均合法", not bad, "; ".join(bad)[:200])

# ---- 导航站定位声明 ----
check("README 说明导航站不提供资源",
      "导航站本身不提供影视资源与磁力链接" in README)
check("README 说明 mukaku 需用中文名", "中文片名" in README)

print()
print("=" * 60)
failed = [c for c in checks if not c[0]]
print(f"README 事实校验：{len(checks) - len(failed)}/{len(checks)} 通过")
for _, name, detail in failed:
    print(f"  FAIL {name} {detail}")
sys.exit(1 if failed else 0)
