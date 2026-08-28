"""校验 README 中的事实性声明与代码实际情况是否一致。"""
import json
import os
import pathlib
import re
import sys
import urllib.request

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

#: 服务地址，可用 CF_BASE_URL / CF_PORT 覆盖
BASE = os.environ.get("CF_BASE_URL") or f"http://127.0.0.1:{os.environ.get('CF_PORT', '6060')}"

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
check("Provider 总数 21", len(providers) == 21, f"实际 {len(providers)}")
check("README 声明 21 个 Provider", "21 个注册 Provider" in README)
for expected in ("api_generic", "html_generic", "mukaku", "pan_generic",
                 "torznab", "rss", "nyaa", "pansou"):
    check(f"Provider {expected} 已注册", expected in names)
    check(f"README 提及 {expected}", expected in README)

# ---- 数据库表 ----
from app.db.models import Base  # noqa: E402

check("14 张表", len(Base.metadata.tables) == 14, f"实际 {len(Base.metadata.tables)}")
check("README 声明 14 张表", "14 张表" in README)
for table in ("audit_logs", "pan_saves"):
    check(f"新增表 {table} 存在", table in Base.metadata.tables)

# ---- 默认站点 ----
from app.db.init_db import DEFAULT_SITES  # noqa: E402

check("默认示例站点 11 条", len(DEFAULT_SITES) == 11, f"实际 {len(DEFAULT_SITES)}")
check("README 声明 11 条示例站点", "写入 11 条" in README)
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
check("内置调度任务 5 个", len(job_ids) == 5, str(job_ids))
check("README 声明 5 内置任务", "5 内置任务" in README)
check("雷达任务已注册", "cineflow.radar" in job_ids)
check("网盘转存任务已注册", "cineflow.pan_transfer" in job_ids)

# ---- API 端点 ----
try:
    with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=15) as r:
        spec = json.loads(r.read().decode())
    total = sum(
        1
        for _, methods in spec["paths"].items()
        for m in methods
        if m.lower() in ("get", "post", "patch", "delete", "put")
    )
    check("API 端点 82 个", total == 82, f"实际 {total}")
    check("README 声明 82 个端点", "82 个端点" in README and "共 82 个" in README)
    paths = set(spec["paths"])
    for path in ("/api/v1/radar/run", "/api/v1/radar/feed", "/api/v1/radar/jobs",
                 "/api/v1/sites/presets", "/api/v1/sites/discover",
                 "/api/v1/trending", "/api/v1/trending/resources",
                 "/api/v1/trending/live", "/api/v1/trending/keywords",
                 "/api/v1/trending/sites",
                 "/api/v1/schedules", "/api/v1/schedules/{key}",
                 "/api/v1/schedules/{key}/reset", "/api/v1/schedules/{key}/run"):
        check(f"端点存在 {path}", path in paths)
except Exception as exc:
    check("API 端点校验（需先起服务）", False, str(exc)[:80])

# ---- router 数量 ----
router_src = pathlib.Path("app/api/router.py").read_text(encoding="utf-8")
router_count = router_src.count("api_router.include_router(")
check("router 14 个", router_count == 14, f"实际 {router_count}")
check("README 声明 14 个 router", "14 个 router" in README)
for name in ("trending", "schedules", "pan", "chatops"):
    check(f"router {name} 已挂载", f"{name}.router" in router_src)

# ---- 前端页面 ----
app_js = pathlib.Path("web/assets/app.js").read_text(encoding="utf-8")
pages_block = app_js[app_js.index("const PAGES"): app_js.index("];", app_js.index("const PAGES"))]
page_count = pages_block.count("{ key:")
check("前端页面 14 个", page_count == 14, f"实际 {page_count}")
check("README 声明点检 14 个页面", "14 个页面" in README)
check("scripts/README 声明 14 个页面", "14 个前端页面" in SCRIPTS_README)
check("前端含热度排行页", '"trending"' in app_js and "pageTrending" in app_js)
check("前端含定时任务页", '"schedules"' in app_js and "pageSchedules" in app_js)
check("README 声明 14 个功能页", "14 个功能页" in README)
check("前端含追新雷达页", '"radar"' in app_js and "pageRadar" in app_js)
check("前端含站点发现弹窗", "discoverDialog" in app_js)
check("前端含预设选择器", "presetPicker" in app_js)
check("前端含 options JSON 编辑", "parseOptions" in app_js)

# ---- 暗色 / 浅色主题 ----
index_html = pathlib.Path("web/index.html").read_text(encoding="utf-8")
style_css = pathlib.Path("web/assets/style.css").read_text(encoding="utf-8")
check("index.html 预置 data-theme", "data-theme" in index_html)
check("index.html 含防闪白脚本", "cf_theme" in index_html)
check("CSS 定义暗色主题变量", '[data-theme="dark"]' in style_css)
check("CSS 定义浅色主题变量", '[data-theme="light"]' in style_css)
check("前端含主题切换控件", "themeToggle" in app_js and "theme-toggle" in app_js)
check("前端含主题持久化", "cf_theme" in app_js)
check("前端含 setTheme/applyTheme", "setTheme" in app_js and "applyTheme" in app_js)
check("前端支持跟随系统主题", "prefers-color-scheme" in app_js)
check("README 说明主题切换", "暗色" in README and "浅色" in README)

# ---- 热度排行 ----
check("热度服务文件存在", pathlib.Path("app/services/trending.py").exists())
trending_src = pathlib.Path("app/services/trending.py").read_text(encoding="utf-8")
for func in ("resource_ranking", "live_ranking", "hot_keywords",
             "site_activity", "overview"):
    check(f"热度服务函数 {func}", f"def {func}" in trending_src)
check("热度打分函数 heat", "def heat" in trending_src)
check("热度榜标题归并去碎片化",
      "_canonical_title" in trending_src and "_VARIANT_TAGS" in trending_src)
check("热度榜剥离集号/季号标记",
      "_EPISODE_MARK" in trending_src and "_SEASON_MARK" in trending_src)
check("热度榜折叠同名未知类型",
      "_collapse_unknown" in trending_src and "def absorb" in trending_src)
check("前端含热度条渲染", "heatCell" in app_js and "heat-bar" in style_css)
check("前端含排名徽标", "rankCell" in app_js and ".rank" in style_css)
check("README 说明热度排行", "热度排行" in README)
check("README 说明热度公式", "heat" in README)

# ---- 定时任务设置 ----
check("设置仓库文件存在", pathlib.Path("app/services/settings_store.py").exists())
store_src = pathlib.Path("app/services/settings_store.py").read_text(encoding="utf-8")
for func in ("get_setting", "set_setting", "delete_setting"):
    check(f"设置仓库函数 {func}", f"def {func}" in store_src)
for func in ("effective_schedule", "normalize_schedule", "builtin_specs"):
    check(f"调度模块函数 {func}", f"def {func}" in scheduler_src)
for method in ("update_schedule", "reset_schedule", "describe_schedule"):
    check(f"调度服务方法 {method}", f"def {method}" in scheduler_src)
check("调度规则可持久化", "settings_store" in scheduler_src)
check("前端含定时任务表单", "scheduleForm" in app_js)
check("README 说明定时任务可改期", "定时任务" in README and "cron" in README)

# ---- 测试文件 ----
test_files = sorted(p.name for p in pathlib.Path("tests").glob("test_*.py"))
check("测试文件 12 个", len(test_files) == 12, str(test_files))
check("README 声明 12 个测试文件", "12 个测试文件" in README)
for name in ("test_custom_sites.py", "test_radar.py", "test_trending.py",
             "test_panstorage.py", "test_chatops.py"):
    check(f"README 提及 {name}", name in README)

# ---- 脚本 ----
for script in ("smoke_test.py", "ui_check.py", "demo_pipeline.py", "live_check.py",
               "verify_docs.py"):
    check(f"脚本存在 {script}", pathlib.Path("scripts", script).exists())
    check(f"scripts/README 提及 {script}", script in SCRIPTS_README)
check("README 声明五个验证脚本", "五个开发期验证工具" in README)
check("README 测试徽章 270", "tests-270%20passed" in README)
check("README 版本号 1.3.0", "1.3.0" in README)
version_src = pathlib.Path("app/core/version.py").read_text(encoding="utf-8")
check("代码版本号为 1.3.0", 'APP_VERSION = "1.3.0"' in version_src)
check("README 声明 117 项接口用例", "117 项真实 HTTP 接口用例" in README)
check("scripts/README 声明 117 项", "117 项接口用例" in SCRIPTS_README)

# ---- 服务模块 ----
for module in ("radar", "discovery", "presets", "trending", "settings_store",
               "pan_storage"):
    check(f"服务模块 app/services/{module}.py 存在",
          pathlib.Path(f"app/services/{module}.py").exists())
    check(f"README 提及 {module} 服务", module in README)

# ---- docs/ 文档体系 ----
DOC_FILES = [
    "README.md",
    "01-项目现状.md",
    "02-架构设计.md",
    "03-升级路线图.md",
    "04-决策记录.md",
    "05-ChatOps-机器人.md",
    "06-网盘管理.md",
    "07-运维手册.md",
    "08-变更日志.md",
]
docs_dir = pathlib.Path("docs")
check("docs/ 目录存在", docs_dir.is_dir())
docs_index = (docs_dir / "README.md").read_text(encoding="utf-8") if (docs_dir / "README.md").exists() else ""
for name in DOC_FILES:
    path = docs_dir / name
    check(f"文档存在 docs/{name}", path.exists())
    if path.exists():
        check(f"文档非空 docs/{name}", len(path.read_text(encoding="utf-8")) > 500,
              f"{len(path.read_text(encoding='utf-8'))} 字符")
    if name != "README.md":
        check(f"docs/README 索引了 {name}", name in docs_index)
check("README 指向 docs/ 文档体系", "docs/" in README and "文档" in README)

# 路线图必须已回填真实结论，不能留占位
roadmap = (docs_dir / "03-升级路线图.md")
if roadmap.exists():
    roadmap_text = roadmap.read_text(encoding="utf-8")
    check("路线图无「待实测」占位", "待实测" not in roadmap_text)
    check("路线图无「未开始」残留（v1.3.0 已交付）", "未开始" not in roadmap_text)
    for milestone in ("M1", "M2", "M3", "M4"):
        check(f"路线图含里程碑 {milestone}", milestone in roadmap_text)

# 变更日志必须记录到当前版本
changelog = (docs_dir / "08-变更日志.md")
if changelog.exists():
    changelog_text = changelog.read_text(encoding="utf-8")
    for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0"):
        check(f"变更日志含 v{version}", version in changelog_text)

# ---- 网盘管理（M2） ----
check("网盘存储 Provider 目录存在", pathlib.Path("app/providers/panstorage").is_dir())
pan_base = pathlib.Path("app/providers/panstorage/base.py").read_text(encoding="utf-8")
check("BasePanStorage 抽象类存在", "class BasePanStorage" in pan_base)
for cls, mod in (("AListStorage", "alist"), ("QuarkStorage", "quark"),
                 ("LocalDirStorage", "local_dir")):
    src = pathlib.Path(f"app/providers/panstorage/{mod}.py")
    check(f"网盘 Provider {mod} 文件存在", src.exists())
    check(f"网盘 Provider {cls} 已定义", cls in src.read_text(encoding="utf-8"))
    check(f"网盘 Provider {mod} 已注册", mod in names)
for dc in ("PanFile", "PanQuota", "SaveResult"):
    check(f"网盘数据结构 {dc}", f"class {dc}" in pan_base)
check("网盘能力可优雅降级（默认实现不抛异常）",
      "return PanQuota()" in pan_base and "return False" in pan_base)
pan_service_src = pathlib.Path("app/services/pan_storage.py").read_text(encoding="utf-8")
for func in ("storages", "get_storage", "_pick_for_share", "overview", "list_files",
             "save_share", "transfer_pending", "delete_file", "make_dir",
             "resolve_download_url", "list_save_records", "test_storage"):
    check(f"网盘服务函数 {func}", f"def {func}" in pan_service_src)
check("网盘选盘按分享域名匹配同家网盘", "pan.quark.cn" in pan_service_src)
download_src = pathlib.Path("app/services/download.py").read_text(encoding="utf-8")
check("盘搜命中后自动转存", "_try_auto_save_pan" in download_src)
check("自动转存受 PAN_AUTO_SAVE 控制", "PAN_AUTO_SAVE" in download_src)
for key in ("PAN_AUTO_SAVE", "PAN_TRANSFER_INTERVAL_MINUTES", "PAN_TRANSFER_BATCH"):
    check(f"配置项 {key} 存在", hasattr(settings, key))
    check(f"README 提及 CF_{key}", f"CF_{key}" in README)
check("前端含网盘管理页", "pageStorage" in app_js)
check("前端含容量进度与面包屑", "quotaCard" in app_js and "breadcrumb" in app_js)
check("CSS 含网盘样式", ".pan-card" in style_css and ".crumbs" in style_css)
check("README 说明网盘管理", "网盘管理" in README and "转存" in README)

# ---- ChatOps（M3） ----
check("ChatOps 目录存在", pathlib.Path("app/services/chatops").is_dir())
adapters_src = pathlib.Path("app/services/chatops/adapters.py").read_text(encoding="utf-8")
commands_src = pathlib.Path("app/services/chatops/commands.py").read_text(encoding="utf-8")
chat_service_src = pathlib.Path("app/services/chatops/service.py").read_text(encoding="utf-8")
for cls in ("ChatAdapter", "FeishuAdapter", "DingTalkAdapter", "TelegramAdapter"):
    check(f"ChatOps 适配器 {cls}", f"class {cls}" in adapters_src)
check("飞书支持加密事件解密", "def decrypt" in adapters_src)
check("钉钉签名校验含防重放", "max_drift_ms" in adapters_src)
check("Telegram 校验 secret token", "x-telegram-bot-api-secret-token" in adapters_src)
check("未配密钥默认拒绝入站", "_missing_secret" in adapters_src
      and "allow_unverified" in adapters_src)
check("适配器声明配置字段供前端渲染", "config_fields" in adapters_src)
for func in ("parse", "_parse_season", "_parse_episode"):
    check(f"指令解析函数 {func}", f"def {func}" in commands_src)
check("指令别名表存在", "ALIASES" in commands_src)
check("指令帮助文本存在", "HELP_TEXT" in commands_src)
for func in ("handle_message", "process_webhook", "get_config", "save_config",
             "list_audit", "clear_sessions"):
    check(f"ChatOps 服务函数 {func}", f"def {func}" in chat_service_src)
for func in ("_do_search", "_do_download", "_do_subscribe", "_do_status",
             "_do_subscribes", "_do_transfer", "_do_trending"):
    check(f"ChatOps 指令实现 {func}", f"def {func}" in chat_service_src)
check("ChatOps 幂等去重", "_seen" in chat_service_src and "_PROCESSED" in chat_service_src)
check("ChatOps 会话上下文", "_remember" in chat_service_src and "_recall" in chat_service_src)
check("ChatOps 指令留痕审计", "_audit" in chat_service_src and "AuditLog" in chat_service_src)
check("ChatOps auto_download 真的生效", "auto_download=bool(config" in chat_service_src)
for key in ("CHATOPS_ENABLED", "CHATOPS_AUTO_DOWNLOAD", "CHATOPS_RESULT_LIMIT",
            "CHATOPS_ALLOW_USERS", "CHATOPS_SESSION_TTL"):
    check(f"配置项 {key} 存在", hasattr(settings, key))
    check(f"README 提及 CF_{key}", f"CF_{key}" in README)
check("前端含机器人页", "pageChatops" in app_js)
check("前端展示可复制回调地址", "webhook-box" in app_js and "copyText" in app_js)
check("CSS 含回调地址样式", ".webhook-box" in style_css)
check("README 说明 ChatOps", "飞书" in README and "钉钉" in README and "Telegram" in README)
check("README 给出 Webhook 回调路径", "/api/v1/chatops/webhook/" in README)

# ---- 设置页（M4） ----
system_src = pathlib.Path("app/api/routers/system.py").read_text(encoding="utf-8")
check("生效配置端点存在", "def effective_settings" in system_src)
check("敏感配置脱敏", "_mask" in system_src and "已设置" in system_src)
check("前端含设置页", "pageSettings" in app_js)
check("README 说明设置页", "设置页" in README)

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
