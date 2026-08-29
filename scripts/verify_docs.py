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
check("Provider 总数 28", len(providers) == 28, f"实际 {len(providers)}")
check("README 声明 28 个 Provider", "28 个注册 Provider" in README)
for expected in ("api_generic", "html_generic", "mukaku", "pan_generic",
                 "torznab", "rss", "nyaa", "pansou",
                 # v1.7.0 新增：两个视频站搜索 + 两个资源站
                 "bilibili", "youtube", "yyets", "wp_film",
                 # v1.8.0 新增：115 网盘存储
                 "pan115"):
    check(f"Provider {expected} 已注册", expected in names)
    check(f"README 提及 {expected}", expected in README)

# ---- 数据库表 ----
from app.db.models import Base  # noqa: E402

check("19 张表", len(Base.metadata.tables) == 19, f"实际 {len(Base.metadata.tables)}")
check("README 声明 19 张表", "19 张表" in README)
for table in ("audit_logs", "pan_saves", "pan_subscribes", "strm_records",
              "site_health", "ranking_rules", "filter_rule_groups"):
    check(f"新增表 {table} 存在", table in Base.metadata.tables)

# ---- 默认站点 ----
from app.db.init_db import DEFAULT_SITES  # noqa: E402

check("默认示例站点 22 条", len(DEFAULT_SITES) == 22, f"实际 {len(DEFAULT_SITES)}")
check("README 声明 22 条示例站点", "写入 22 条" in README)
check("默认站点含 webdav", any(s["provider"] == "webdav" for s in DEFAULT_SITES))
# 需要填地址/账号的站点必须默认禁用（示例值直接跑必然报错）；
# yt-dlp 是本地库调用、装了依赖就能用，是唯一例外
check(
    "需配置的示例站点默认禁用",
    all(
        not s.get("enabled", False)
        for s in DEFAULT_SITES
        if s["provider"] != "ytdlp"
    ),
)
check(
    "yt-dlp 默认启用（本地库无需配置）",
    any(s["provider"] == "ytdlp" and s.get("enabled") for s in DEFAULT_SITES),
)
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
check("内置调度任务 12 个", len(job_ids) == 12, str(job_ids))
check("README 声明 12 内置任务", "12 内置任务" in README)
for job in ("cineflow.radar", "cineflow.pan_transfer", "cineflow.pan_subscribe",
            "cineflow.pan_keepalive",
            "cineflow.strm_sync", "cineflow.scrape", "cineflow.upgrade",
            "cineflow.site_health", "cineflow.ranking"):
    check(f"任务已注册 {job}", job in job_ids)

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
    check("API 端点 136 个", total == 136, f"实际 {total}")
    check("README 声明 136 个端点", "136 个端点" in README and "共 136 个" in README)
    paths = set(spec["paths"])
    for path in ("/api/v1/users", "/api/v1/users/{user_id}",
                 "/api/v1/system/settings", "/api/v1/system/settings/reset",
                 "/api/v1/site-health", "/api/v1/site-health/records",
                 "/api/v1/site-health/check", "/api/v1/site-health/check/{site_id}",
                 "/api/v1/ranking-rules", "/api/v1/ranking-rules/{rule_id}",
                 "/api/v1/ranking-rules/{rule_id}/preview",
                 "/api/v1/ranking-rules/{rule_id}/run", "/api/v1/ranking-rules/run-all",
                 "/api/v1/rule-groups", "/api/v1/rule-groups/{group_id}",
                 "/api/v1/rule-groups/{group_id}/preview",
                 "/api/v1/radar/run", "/api/v1/radar/feed", "/api/v1/radar/jobs",
                 "/api/v1/sites/presets", "/api/v1/sites/discover",
                 "/api/v1/trending", "/api/v1/trending/resources",
                 "/api/v1/trending/live", "/api/v1/trending/keywords",
                 "/api/v1/trending/sites",
                 "/api/v1/schedules", "/api/v1/schedules/{key}",
                 "/api/v1/schedules/{key}/reset", "/api/v1/schedules/{key}/run",
                 # v1.7.0：网盘文件管理 + 保活 + 豆瓣封面 + 图片代理
                 "/api/v1/pan/rename", "/api/v1/pan/move", "/api/v1/pan/search",
                 "/api/v1/pan/keep-alive", "/api/v1/trending/douban",
                 "/api/v1/images/proxy"):
        check(f"端点存在 {path}", path in paths)
except Exception as exc:
    check("API 端点校验（需先起服务）", False, str(exc)[:80])

# ---- router 数量 ----
router_src = pathlib.Path("app/api/router.py").read_text(encoding="utf-8")
router_count = router_src.count("api_router.include_router(")
check("router 21 个", router_count == 21, f"实际 {router_count}")
check("README 声明 21 个 router", "21 个 router" in README)
for name in ("trending", "schedules", "pan", "chatops", "strm", "pan_subscribes",
             "users", "site_health", "ranking", "rule_groups"):
    check(f"router {name} 已挂载", f"{name}.router" in router_src)

# ---- 前端页面 ----
app_js = pathlib.Path("web/assets/app.js").read_text(encoding="utf-8")
pages_block = app_js[app_js.index("const PAGES"): app_js.index("];", app_js.index("const PAGES"))]
page_count = pages_block.count("{ key:")
check("前端页面 20 个", page_count == 20, f"实际 {page_count}")
check("README 声明点检 20 个页面", "20 个页面" in README)
check("scripts/README 声明 20 个页面", "20 个前端页面" in SCRIPTS_README)
check("前端含热度排行页", '"trending"' in app_js and "pageTrending" in app_js)
check("前端含定时任务页", '"schedules"' in app_js and "pageSchedules" in app_js)
check("README 声明 20 个功能页", "20 个功能页" in README)
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
check("测试文件 35 个", len(test_files) == 35, str(test_files))
check("README 声明 35 个测试文件", "35 个测试文件" in README)
for name in ("test_custom_sites.py", "test_radar.py", "test_trending.py",
             "test_panstorage.py", "test_chatops.py", "test_nfo.py",
             "test_scraper.py", "test_webdav.py", "test_strm_sync.py",
             "test_pan_subscribe.py", "test_upgrade.py", "test_categories.py",
             "test_config_store.py", "test_rules.py", "test_site_health.py",
             "test_ranking.py", "test_rule_groups.py", "test_users.py"):
    check(f"README 提及 {name}", name in README)

# ---- 脚本 ----
for script in ("smoke_test.py", "ui_check.py", "demo_pipeline.py", "live_check.py",
               "verify_docs.py", "research_refs.py"):
    check(f"脚本存在 {script}", pathlib.Path("scripts", script).exists())
    check(f"scripts/README 提及 {script}", script in SCRIPTS_README)
check("README 声明六个验证脚本", "六个开发期验证工具" in README)
check("README 测试徽章 773", "tests-773%20passed" in README)
check("README 版本号 1.8.0", "1.8.0" in README)
version_src = pathlib.Path("app/core/version.py").read_text(encoding="utf-8")
check("代码版本号为 1.8.0", 'APP_VERSION = "1.8.0"' in version_src)
check("README 声明 255 项接口用例", "255 项真实 HTTP 接口用例" in README)
check("scripts/README 声明 255 项", "255 项接口用例" in SCRIPTS_README)

# ---- 服务模块 ----
for module in ("radar", "discovery", "presets", "trending", "settings_store",
               "pan_storage", "scraper", "strm_sync", "pan_subscribe", "upgrade",
               "config_store", "site_health", "ranking", "rule_groups"):
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
    "09-竞品对标与差距分析.md",
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
    check("路线图无「未开始」残留（v1.5.0 已交付）", "未开始" not in roadmap_text)
    for milestone in ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10",
                      "M11", "M12", "M13", "M14", "M15", "M16",
                      "M17", "M18", "M19", "M20"):
        check(f"路线图含里程碑 {milestone}", milestone in roadmap_text)
    # 路线图必须往前看：候选章节要指向下一个版本，不能还停在已发布的版本上
    check("路线图候选指向 v1.8.0", "v1.8.0 候选" in roadmap_text)

# 变更日志必须记录到当前版本
changelog = (docs_dir / "08-变更日志.md")
if changelog.exists():
    changelog_text = changelog.read_text(encoding="utf-8")
    for version in ("1.0.0", "1.1.0", "1.2.0", "1.3.0", "1.4.0", "1.5.0", "1.6.0", "1.7.0"):
        check(f"变更日志含 v{version}", version in changelog_text)
    # 变更日志要如实记录「没做什么」，否则下次接手的人会重复踩同一个坑
    check("变更日志说明拒绝 VIP 解析", "付费墙" in changelog_text)

# ADR 必须覆盖本轮的关键决策
adr_text = (docs_dir / "04-决策记录.md").read_text(encoding="utf-8")
for adr in ("ADR-24", "ADR-25", "ADR-26", "ADR-27",
            "ADR-36", "ADR-37", "ADR-38", "ADR-39", "ADR-40"):
    check(f"决策记录含 {adr}", adr in adr_text)
check("ADR-24 记录 yt-dlp 合规边界", "只下**公开内容**" in adr_text)
check("ADR-25 记录公平性靠交错", "轮转交错" in adr_text)
check("ADR-26 记录不强依赖 TMDB", "不强依赖 TMDB" in adr_text)
check("ADR-27 记录渲染归属校验", "这一屏属于谁" in adr_text)
# 竞品分析里被推翻的旧判断必须写清原因，而不是悄悄删掉
gap_text = (docs_dir / "09-竞品对标与差距分析.md").read_text(encoding="utf-8")
check("竞品分析标注 v1.7.0 推翻了旧判断", "v1.7.0 部分推翻" in gap_text)
check("竞品分析记录 VIP 解析不做", "明确不做" in gap_text)
# 运维手册要能回答本轮用户问过的问题
ops_text = (docs_dir / "07-运维手册.md").read_text(encoding="utf-8")
check("运维手册有站点诊断排障", "站点诊断" in ops_text)
check("运维手册解释封面色块属预期", "预期行为" in ops_text)
check("运维手册有 yt-dlp 排障", "yt-dlp" in ops_text)

# ---- 网盘管理（M2） ----
check("网盘存储 Provider 目录存在", pathlib.Path("app/providers/panstorage").is_dir())
pan_base = pathlib.Path("app/providers/panstorage/base.py").read_text(encoding="utf-8")
check("BasePanStorage 抽象类存在", "class BasePanStorage" in pan_base)
for cls, mod in (("AListStorage", "alist"), ("QuarkStorage", "quark"),
                 ("LocalDirStorage", "local_dir"), ("WebDavStorage", "webdav")):
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

# ---- NFO 刮削（M5） ----
nfo_src = pathlib.Path("app/core/nfo.py").read_text(encoding="utf-8")
check("NFO 渲染模块在 core（无 IO，可离线单测）", "app/core/nfo.py")
for func in ("build_movie_nfo", "build_tvshow_nfo", "build_season_nfo",
             "build_episode_nfo", "build_for", "parse_nfo_tmdb_id"):
    check(f"NFO 函数 {func}", f"def {func}" in nfo_src)
check("NFO 覆盖四种根节点",
      all(f'ET.Element("{tag}")' in nfo_src
          for tag in ("movie", "tvshow", "season", "episodedetails")))
check("NFO 图片命名符合媒体服务器惯例", "IMAGE_FILENAMES" in nfo_src and "fanart" in nfo_src)
check("core/nfo 不做网络请求", "httpx" not in nfo_src and "async def" not in nfo_src)
scraper_src = pathlib.Path("app/services/scraper.py").read_text(encoding="utf-8")
for func in ("scrape_file", "scrape_library"):
    check(f"刮削服务函数 {func}", f"def {func}" in scraper_src)
check("刮削支持 TMDB 不可用降级", "degraded" in scraper_src)
check("刮削默认不覆盖已有 NFO", "overwrite" in scraper_src)
for key in ("SCRAPE_ENABLED", "SCRAPE_IMAGES", "SCRAPE_OVERWRITE", "SCRAPE_CRON",
            "SCRAPE_BATCH"):
    check(f"配置项 {key} 存在", hasattr(settings, key))
    check(f"README 提及 CF_{key}", f"CF_{key}" in README)
check("补刮端点已挂载", "def scrape" in pathlib.Path("app/api/routers/library.py").read_text(encoding="utf-8"))
check("前端含补刮按钮", "补刮 NFO" in app_js)
check("README 说明 NFO 刮削", "NFO" in README and "刮削" in README)

# ---- WebDAV（M6） ----
dav_src = pathlib.Path("app/providers/panstorage/webdav.py").read_text(encoding="utf-8")
check("WebDAV 用 PROPFIND 列目录", "PROPFIND" in dav_src)
check("WebDAV 用 MKCOL 建目录", "MKCOL" in dav_src)
check("WebDAV 支持容量查询", "quota-available-bytes" in dav_src)
check("WebDAV 明确不支持分享转存", "supports_save = False" in dav_src)
check("WebDAV 提供 Basic Auth 头（供 302 播放）", "def auth_header" in dav_src)
check("WebDAV 对路径做 percent 编码", "urllib.parse.quote" in dav_src)
check("README 说明 WebDAV 覆盖面", "WebDAV" in README)

# ---- STRM 同步 + 302（M7） ----
strm_src = pathlib.Path("app/services/strm_sync.py").read_text(encoding="utf-8")
for func in ("sync_storage", "sync_all", "resolve_play_url", "list_records", "stats",
             "_clean_invalid"):
    check(f"STRM 服务函数 {func}", f"def {func}" in strm_src)
check("STRM 支持 proxy/direct 两种链接模式",
      '"direct"' in strm_src and "STRM_LINK_MODE" in strm_src)
check("STRM 清理源文件消失的失效记录", "alive" in strm_src)
strm_router = pathlib.Path("app/api/routers/strm.py").read_text(encoding="utf-8")
check("STRM 播放端点返回 302", "RedirectResponse" in strm_router and "status_code=302" in strm_router)
check("STRM 播放端点匿名（不挂 CurrentUser）",
      "async def play(record_id: int) -> RedirectResponse:" in strm_router)
for key in ("STRM_LINK_MODE", "STRM_SYNC_INTERVAL_MINUTES", "STRM_CLEAN_INVALID",
            "STRM_SYNC_METADATA"):
    check(f"配置项 {key} 存在", hasattr(settings, key))
    check(f"README 提及 CF_{key}", f"CF_{key}" in README)
check("默认链接模式为 proxy（链接不过期）", settings.STRM_LINK_MODE == "proxy",
      f"实际 {settings.STRM_LINK_MODE}")
check("前端含 STRM 页", "pageStrm" in app_js and '"strm"' in app_js)
check("README 说明 STRM 与 302", "STRM" in README and "302" in README)

# ---- 网盘分享追更（M8） ----
ps_src = pathlib.Path("app/services/pan_subscribe.py").read_text(encoding="utf-8")
for func in ("check_one", "check_all", "match_files", "apply_rename", "_should_run"):
    check(f"分享追更函数 {func}", f"def {func}" in ps_src)
check("分享追更做增量（记已转存文件名）", "saved_files" in ps_src)
check("不支持增量的网盘用哨兵防重复整体转存", "__whole_share__" in ps_src)
check("错误正则不会搞崩巡检", "def _compile" in ps_src and "re.error" in ps_src)
check("连续失败到阈值标记失效", "PAN_SUBSCRIBE_MAX_FAILURES" in ps_src and "invalid" in ps_src)
check("支持按星期与到期时间限制执行", "weekdays" in ps_src and "expire_at" in ps_src)
check("夸克支持列举与逐文件转存分享",
      "async def list_share" in pathlib.Path("app/providers/panstorage/quark.py").read_text(encoding="utf-8")
      and "async def save_share_files" in pathlib.Path("app/providers/panstorage/quark.py").read_text(encoding="utf-8"))
check("基类给出 list_share/save_share_files 降级默认",
      "async def list_share" in pan_base and "async def save_share_files" in pan_base)
for key in ("PAN_SUBSCRIBE_INTERVAL_MINUTES", "PAN_SUBSCRIBE_MAX_FAILURES"):
    check(f"配置项 {key} 存在", hasattr(settings, key))
    check(f"README 提及 CF_{key}", f"CF_{key}" in README)
check("前端含分享追更页", "pagePanSub" in app_js and '"pansub"' in app_js)
check("README 说明分享追更", "分享追更" in README)

# ---- 洗版（M9） ----
upgrade_src = pathlib.Path("app/services/upgrade.py").read_text(encoding="utf-8")
for func in ("evaluate", "check_subscribe", "replace_library_file", "run"):
    check(f"洗版函数 {func}", f"def {func}" in upgrade_src)
check("洗版有评分阈值防横跳", "UPGRADE_SCORE_DELTA" in upgrade_src)
check("洗版有次数上限", "UPGRADE_MAX_TIMES" in upgrade_src and "upgrade_count" in upgrade_src)
check("洗版只针对 best_version 订阅", "best_version" in upgrade_src)
check("洗版默认关闭（会删已入库文件）", settings.UPGRADE_ENABLED is False)
check("洗版先下载后删旧文件（避免留空洞）",
      "upgrade_for" in upgrade_src and "replace_library_file" in upgrade_src)
check("入库时才执行旧文件替换",
      "replace_library_file" in pathlib.Path("app/services/library.py").read_text(encoding="utf-8"))
for key in ("UPGRADE_ENABLED", "UPGRADE_SCORE_DELTA", "UPGRADE_MAX_TIMES"):
    check(f"配置项 {key} 存在", hasattr(settings, key))
    check(f"README 提及 CF_{key}", f"CF_{key}" in README)
check("前端含洗版试算报告", "upgradeReport" in app_js)
check("README 说明洗版", "洗版" in README)

# ---- 媒体分类归档（M10） ----
cat_src = pathlib.Path("app/core/categories.py").read_text(encoding="utf-8")
for func in ("detect", "directory_for"):
    check(f"分类函数 {func}", f"def {func}" in cat_src)
check("分类覆盖 6 类", "CATEGORY_NAMES" in cat_src
      and all(word in cat_src for word in ("电影", "电视剧", "动漫", "纪录片", "综艺", "儿童")))
check("分类优先用 TMDB genre", "_GENRE_MAP" in cat_src)
check("分类判不出返回 None 不猜", "return None" in cat_src)
check("core/categories 无 IO", "httpx" not in cat_src and "async def" not in cat_src)
organizer_src = pathlib.Path("app/core/organizer.py").read_text(encoding="utf-8")
check("整理时按分类建二级目录", "CATEGORY_ENABLED" in organizer_src and "directory_for" in organizer_src)
check("配置项 CATEGORY_ENABLED 存在", hasattr(settings, "CATEGORY_ENABLED"))
check("README 提及 CF_CATEGORY_ENABLED", "CF_CATEGORY_ENABLED" in README)
check("分类默认关闭（不擅自改动已有库布局）", settings.CATEGORY_ENABLED is False)

# ---- 老库升级路径 ----
initdb_src = pathlib.Path("app/db/init_db.py").read_text(encoding="utf-8")
check("老库自动补新增列", "def migrate_columns" in initdb_src and "ADD COLUMN" in initdb_src)
check("补列在 init_db 里调用", "migrate_columns()" in initdb_src)
check("补列覆盖 v1.4.0 新字段",
      "quality_score" in initdb_src and "upgrade_count" in initdb_src)
check("补列覆盖 v1.5.0 新字段",
      all(word in initdb_src for word in ("role", "note", "rule_group_id")))
check("老库 role 默认 admin（不把老管理员锁在外面）",
      "'admin'" in initdb_src and "def sync_user_roles" in initdb_src)

# ---- 竞品对标文档 ----
gap_doc = docs_dir / "09-竞品对标与差距分析.md"
if gap_doc.exists():
    gap_text = gap_doc.read_text(encoding="utf-8")
    for repo in ("quark-auto-save", "SmartStrm", "MediaWarp", "TgtoDrive"):
        check(f"对标文档提及 {repo}", repo in gap_text)
    check("对标文档给出差距矩阵", "差距" in gap_text)
    check("对标文档写明不做的事与原因", "不做" in gap_text)
check("复查脚本存在", pathlib.Path("scripts/research_refs.py").exists())
check("scripts/README 提及 research_refs.py", "research_refs.py" in SCRIPTS_README)

# ---- 可编辑运行期配置（M11） ----
from app.services import config_store  # noqa: E402

cfgstore_src = pathlib.Path("app/services/config_store.py").read_text(encoding="utf-8")
check("可编辑配置白名单存在", "EDITABLE" in cfgstore_src)
check("可编辑配置项 52 个", len(config_store.EDITABLE) == 52, f"实际 {len(config_store.EDITABLE)}")
for func in ("coerce", "update", "reset", "apply_overrides", "describe"):
    check(f"配置仓库函数 {func}", f"def {func}" in cfgstore_src)
# 不可热改的键绝不能进白名单：改了不生效比改不了更糟
for forbidden in ("DATA_DIR", "PORT", "SECRET_KEY", "DB_URL", "HOST"):
    check(f"白名单排除 {forbidden}", forbidden not in config_store.EDITABLE)
check("配置改动先全校验再落库", "先全部校验" in cfgstore_src or "全部校验" in cfgstore_src)
check("配置热生效写回 settings 单例", "object.__setattr__" in cfgstore_src)
check("cron 校验复用 scheduler.validate_cron", "validate_cron" in cfgstore_src)
check("启动时应用配置覆盖",
      "apply_overrides" in pathlib.Path("app/main.py").read_text(encoding="utf-8"))
system_src = pathlib.Path("app/api/routers/system.py").read_text(encoding="utf-8")
check("设置页可提交修改", '@router.put("/settings"' in system_src)
check("设置可恢复默认", '/settings/reset' in system_src)
check("前端设置页按类型渲染控件", "settingControl" in app_js)
check("前端只提交改过的项", "changed" in app_js)
check("README 说明设置可在线修改", "在线修改" in README or "界面改配置" in README)

# ---- 多用户权限（M12） ----
from app.schemas.enums import UserRole  # noqa: E402

deps_src = pathlib.Path("app/api/deps.py").read_text(encoding="utf-8")
check("角色枚举三档", len(list(UserRole)) == 3, str([r.value for r in UserRole]))
check("角色带 rank 排序",
      UserRole.VIEWER.rank < UserRole.OPERATOR.rank < UserRole.ADMIN.rank)
for func in ("role_of", "require_role"):
    check(f"鉴权依赖 {func}", f"def {func}" in deps_src)
check("OperatorUser/AdminUser 依赖存在",
      "OperatorUser" in deps_src and "AdminUser" in deps_src)
check("role_of 对脏值兜底", "except (ValueError, TypeError)" in deps_src)
users_src = pathlib.Path("app/api/routers/users.py").read_text(encoding="utf-8")
check("不能删除自己", "不能删除自己" in users_src)
check("不能停用自己", "不能停用自己" in users_src)
check("最后一个管理员不能降级", "最后一个" in users_src)
check("is_superuser 由 role 推导", "is_superuser=role is UserRole.ADMIN" in users_src)
check("登录返回角色", '"role"' in pathlib.Path("app/api/routers/auth.py").read_text(encoding="utf-8"))
check("服务端不信 JWT 里的 role 副本",
      "不信任令牌" in pathlib.Path("app/api/routers/auth.py").read_text(encoding="utf-8"))
check("前端含用户权限页", '"users"' in app_js and "pageUsers" in app_js)
check("前端按角色隐藏入口", "canDo" in app_js and "visiblePages" in app_js)
check("README 说明三档角色", "operator" in README and "viewer" in README)

# ---- 站点健康巡检（M13） ----
health_src = pathlib.Path("app/services/site_health.py").read_text(encoding="utf-8")
for func in ("check_site", "check_all", "overview", "list_records",
             "unhealthy_sites", "downloader_health"):
    check(f"健康服务函数 {func}", f"def {func}" in health_src)
check("健康三档状态", all(word in health_src for word in ("ok", "degraded", "down")))
check("搜索类站点真搜一次（Cookie 过期只体现在搜索上）",
      "PROBE_KEYWORD" in health_src)
check("0 结果算 degraded 而非 ok", "degraded" in health_src and "PROBE_KEYWORD" in health_src)
check("慢站点有阈值", "SLOW_MS" in health_src)
check("历史记录有上限", "KEEP_PER_SITE" in health_src)
check("裁剪历史前先 flush（否则永远多留一条）", "flush()" in health_src)
check("只在状态翻转时通知", "alert" in health_src)
check("手动探测不发通知", "notify" in health_src)
for key in ("SITE_HEALTH_ENABLED", "SITE_HEALTH_INTERVAL_MINUTES",
            "SITE_HEALTH_FAIL_THRESHOLD", "SITE_AUTO_DISABLE"):
    check(f"配置项 {key} 存在", hasattr(settings, key))
    check(f"README 提及 CF_{key}", f"CF_{key}" in README)
check("自动禁用默认关闭（别擅自停掉用户的站点）", settings.SITE_AUTO_DISABLE is False)
check("前端含站点健康页", '"sitehealth"' in app_js and "pageSiteHealth" in app_js)

# ---- 下载器负载均衡（M14） ----
sites_src = pathlib.Path("app/services/sites.py").read_text(encoding="utf-8")
for func in ("_task_counts", "healthy_downloaders", "downloader_candidates"):
    check(f"下载器调度函数 {func}", f"def {func}" in sites_src)
check("三种调度策略", all(word in sites_src for word in ("priority", "least_tasks", "round_robin")))
check("不健康下载器排后而不剔除", "排后" in sites_src or "而不是" in sites_src)
check("投递失败自动换下一个下载器",
      "downloader_candidates" in pathlib.Path("app/services/download.py").read_text(encoding="utf-8"))
for key in ("DOWNLOADER_STRATEGY", "DOWNLOADER_FAILOVER"):
    check(f"配置项 {key} 存在", hasattr(settings, key))
    check(f"README 提及 CF_{key}", f"CF_{key}" in README)
check("策略默认 priority（与旧行为一致）", settings.DOWNLOADER_STRATEGY == "priority")

# ---- 榜单自动订阅（M15） ----
from app.services import ranking as ranking_service  # noqa: E402

ranking_src = pathlib.Path("app/services/ranking.py").read_text(encoding="utf-8")
for func in ("list_rules", "create", "update", "delete", "fetch_candidates",
             "filter_candidates", "run_rule", "run"):
    check(f"榜单服务函数 {func}", f"def {func}" in ranking_src)
check("榜单来源 4 个", len(ranking_service.SOURCES) == 4, str(list(ranking_service.SOURCES)))
check("榜单记 handled_ids（用户删掉的订阅不该被加回来）", "handled_ids" in ranking_src)
check("handled_ids 有截断上限", "500" in ranking_src)
check("榜单单次有上限", "RANKING_MAX_PER_RUN" in ranking_src)
check("榜单支持 dry_run 试算", "dry_run" in ranking_src)
check("筛选逻辑是纯函数（可离线单测）", "def filter_candidates" in ranking_src)
for key in ("RANKING_INTERVAL_MINUTES", "RANKING_MAX_PER_RUN"):
    check(f"配置项 {key} 存在", hasattr(settings, key))
    check(f"README 提及 CF_{key}", f"CF_{key}" in README)
check("TMDB Provider 提供榜单接口",
      "def ranking" in pathlib.Path("app/providers/metadata/tmdb.py").read_text(encoding="utf-8"))
check("前端含榜单订阅页", '"ranking"' in app_js and "pageRanking" in app_js)

# ---- 自定义过滤规则组（M16） ----
rules_src = pathlib.Path("app/core/rules.py").read_text(encoding="utf-8")
for func in ("level_matches", "match_level", "annotate", "describe"):
    check(f"规则组函数 {func}", f"def {func}" in rules_src)
check("core/rules 无 IO", "httpx" not in rules_src and "async def" not in rules_src
      and "session_scope" not in rules_src)
check("未命中层号足够大（兜底资源排最后）", "UNMATCHED_LEVEL" in rules_src)
check("层级有序、层内按分排序", "rule_level" in rules_src and "score" in rules_src)
groups_src = pathlib.Path("app/services/rule_groups.py").read_text(encoding="utf-8")
for func in ("default_group", "load_group", "preview"):
    check(f"规则组服务函数 {func}", f"def {func}" in groups_src)
check("默认组唯一", "_clear_other_defaults" in groups_src)
check("删除规则组时解绑订阅", "rule_group_id = None" in groups_src)
check("过滤链接入规则组",
      "group" in pathlib.Path("app/core/filters.py").read_text(encoding="utf-8"))
check("搜索侧传入规则组",
      "rule_group_id" in pathlib.Path("app/services/search.py").read_text(encoding="utf-8"))
from app.db.init_db import DEFAULT_RULE_GROUPS  # noqa: E402

check("内置规则组模板 4 个", len(DEFAULT_RULE_GROUPS) == 4, f"实际 {len(DEFAULT_RULE_GROUPS)}")
check("内置模板均不设为默认（不悄悄改用户的排序）",
      all(not g.get("is_default") for g in DEFAULT_RULE_GROUPS))
check("前端含过滤规则组页", '"rules"' in app_js and "pageRuleGroups" in app_js)
check("前端可试算规则组", "previewRuleGroup" in app_js)

# ---- v1.6.0：搜索每站诊断 + 单站安全阀 ----
search_src = pathlib.Path("app/services/search.py").read_text(encoding="utf-8")
check("搜索有 SiteOutcome 诊断结构", "class SiteOutcome" in search_src)
check("搜索有 search_detailed", "def search_detailed" in search_src)
check("搜索按轮转交错合并", "def apply_site_quota" in search_src)
for status in ("ok", "empty", "timeout", "error"):
    check(f"诊断状态 {status}", f'"{status}"' in search_src)
check("SEARCH_MAX_PER_SITE 可在线改", "SEARCH_MAX_PER_SITE" in cfgstore_src)
check("README 说明每站诊断", "每站诊断" in README)
check("README 说明公平性靠交错而非砍量", "轮转交错" in README and "安全阀" in README)

# ---- v1.6.0：热榜画板与封面降级 ----
trending_src = pathlib.Path("app/services/trending.py").read_text(encoding="utf-8")
check("榜单聚合作品级元数据", "absorb_media" in trending_src)
generic_src = pathlib.Path("app/providers/indexer/generic_api.py").read_text(encoding="utf-8")
check("站点元数据映射表", "DEFAULT_MEDIA_MAP" in generic_src)
check("站点元数据可自定义映射", "media_map" in generic_src)
for field in ("poster", "rating", "year", "genres", "total_episodes"):
    check(f"元数据字段 {field}", f'"{field}"' in generic_src)
check("前端有画板渲染", "rankingBoard" in app_js)
check("前端封面有占位降级", "posterBox" in app_js and "poster-ph" in app_js)
check("封面裂图退占位", 'addEventListener("error"' in app_js)
check("封面防盗链", 'referrerpolicy: "no-referrer"' in app_js)
check("画板样式存在", ".board-card" in pathlib.Path("web/assets/style.css").read_text(encoding="utf-8"))
check("README 说明画板不依赖 TMDB", "不依赖 TMDB" in README)

# ---- v1.6.0：yt-dlp 公开视频下载 ----
ytdlp_src = pathlib.Path("app/providers/downloader/ytdlp.py").read_text(encoding="utf-8")
check("yt-dlp Provider 已注册", "ytdlp" in names)
check("yt-dlp 有付费墙拦截", "def is_blocked" in ytdlp_src)
for domain in ("v\\.qq\\.com", "iqiyi\\.com", "youku\\.com", "mgtv\\.com", "netflix"):
    check(f"付费墙规则含 {domain}", domain in ytdlp_src)
check("yt-dlp 有探测缓存", "_PROBE_CACHE" in ytdlp_src)
check("yt-dlp 识别限流", "def is_rate_limited" in ytdlp_src)
check("yt-dlp 无 ffmpeg 时降级", "shutil.which" in ytdlp_src)
check("WEBVIDEO 资源类型存在",
      "WEBVIDEO" in pathlib.Path("app/schemas/enums.py").read_text(encoding="utf-8"))
check("yt-dlp 在 requirements", "yt-dlp" in pathlib.Path("requirements.txt").read_text(encoding="utf-8"))
check("前端有网络视频弹窗", "webVideoDialog" in app_js)
check("README 说明只下公开内容", "只下**公开可访问**的内容" in README)
check("README 说明拒绝 VIP 解析", "不做** VIP" in README)

# ---- v1.6.0：慢请求不得覆盖已切走的页面 ----
check("前端有过期渲染守卫", "PAGE_BY_TITLE" in app_js and "owner !== store.page" in app_js)

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

# ---- v1.7.0：网盘文件管理能力位 ----
pan_base_src = pathlib.Path("app/providers/panstorage/base.py").read_text(encoding="utf-8")
for method in ("async def rename", "async def move", "async def copy",
               "async def search", "async def keep_alive"):
    check(f"网盘基类有 {method}", method in pan_base_src)
check("网盘基类有能力位汇总", "def capabilities" in pan_base_src)
for flag in ("supports_rename", "supports_move", "supports_search", "supports_keepalive"):
    check(f"网盘能力位 {flag} 已定义", flag in pan_base_src)
# 降级铁律：基类默认实现必须返回而不是 raise NotImplementedError
check(
    "网盘新增能力默认优雅降级（不抛异常）",
    "raise NotImplementedError" not in pan_base_src,
)
quark_src = pathlib.Path("app/providers/panstorage/quark.py").read_text(encoding="utf-8")
for api_path in ("/file/rename", "/file/move", "/file/search"):
    check(f"夸克实现 {api_path}", api_path in quark_src)

# ---- v1.7.0：豆瓣封面 ----
douban_src = pathlib.Path("app/providers/metadata/douban.py").read_text(encoding="utf-8")
check("豆瓣用公开 suggest 接口（无需 Key）", "subject_suggest" in douban_src)
check("豆瓣有缓存", "_CACHE" in douban_src)
check("豆瓣有限流退避", "is_rate_limited" in douban_src and "_BACKOFF_SECONDS" in douban_src)
check("榜单有封面补全", "async def enrich_posters" in
      pathlib.Path("app/services/trending.py").read_text(encoding="utf-8"))

# ---- v1.7.0：图片代理与 SSRF 防护 ----
img_src = pathlib.Path("app/api/routers/images.py").read_text(encoding="utf-8")
check("图片代理有域名白名单", "ALLOWED_HOSTS" in img_src)
check("图片代理只允许 http/https", 'parsed.scheme not in ("http", "https")' in img_src)
check("图片代理校验响应为图片", 'candidate_type.startswith("image/")' in img_src)
check("图片代理防后缀混淆", 'host.endswith("." + suffix)' in img_src)
# v1.8.0：豆瓣 img9 是坏镜像（恒返回 200+text/html 反爬页），必须排除并轮换重试
check("图片代理排除豆瓣坏镜像 img9", 'DOUBAN_BAD_MIRRORS' in img_src
      and '"img9"' in img_src)
check("图片代理支持豆瓣镜像轮换", "douban_candidates" in img_src
      and "for candidate in candidates" in img_src)
check("豆瓣榜单入库前改写坏镜像",
      "_normalize_cover" in pathlib.Path(
          "app/providers/metadata/douban_chart.py").read_text(encoding="utf-8"))
check("前端豆瓣封面走代理", "posterSrc" in app_js and "images/proxy" in app_js)

# ---- v1.7.0：YouTube / Bilibili 搜索 ----
wv_src = pathlib.Path("app/providers/indexer/webvideo.py").read_text(encoding="utf-8")
check("B 站先预热首页拿 Cookie 破 412", "HOME_URL" in wv_src)
check("YouTube 复用 yt-dlp ytsearch", "ytsearch" in wv_src)
check("视频搜索产出 WEBVIDEO", "ResourceKind.WEBVIDEO" in wv_src)

# ---- v1.7.0：资源双动作（转存 / 下载）----
base_src = pathlib.Path("app/providers/base.py").read_text(encoding="utf-8")
check("Resource 有 actions 能力位", "def actions" in base_src)
check("Resource.to_dict 下发 actions", '"actions"' in base_src)
check("前端按能力位渲染操作", "function resourceActions" in app_js)
check("前端有网盘转存按钮", "function saveButton" in app_js)

# ---- v1.7.0：新增资源站 ----
check("人人影视 Provider 存在", pathlib.Path("app/providers/indexer/yyets.py").exists())
check("WordPress 影视站 Provider 存在",
      pathlib.Path("app/providers/indexer/wp_film.py").exists())
check("fetch_text 支持强制编码（GB2312 老站）",
      "encoding: str | None = None" in
      pathlib.Path("app/utils/http.py").read_text(encoding="utf-8"))

# ---- v1.7.0：网盘保活定时任务 ----
sched_src = pathlib.Path("app/services/scheduler.py").read_text(encoding="utf-8")
check("网盘保活任务已注册", "JOB_PAN_KEEPALIVE" in sched_src)
check("网盘保活任务可解析", '"pan_keepalive": (pan_service.keep_alive_all, {})' in sched_src)
check("网盘保活配置项存在",
      "PAN_KEEPALIVE_INTERVAL_MINUTES" in
      pathlib.Path("app/core/config.py").read_text(encoding="utf-8"))

# ---- v1.8.0：发现榜（豆瓣分类 + B 站排行）----
douban_chart_src = pathlib.Path("app/providers/metadata/douban_chart.py").read_text(encoding="utf-8")
check("豆瓣榜走公开 search_subjects", "search_subjects" in douban_chart_src)
check("豆瓣榜带 Referer（不带会被拒）", "movie.douban.com/explore" in douban_chart_src)
# 实测踩坑：type 只有 movie/tv，动漫/综艺得靠 tv 的 tag 区分；
# 且 tag=动画 返回 0 条，必须用「日本动画」
check("豆瓣榜 tag 用日本动画（动画返 0 条）", "日本动画" in douban_chart_src)
check("豆瓣榜四个分类齐全",
      all(k in douban_chart_src for k in ("movie", "tv", "anime", "show")))
check("豆瓣榜有缓存与退避",
      "_CACHE" in douban_chart_src and "is_rate_limited" in douban_chart_src)

bili_chart_src = pathlib.Path("app/providers/indexer/bili_chart.py").read_text(encoding="utf-8")
# 裸请求排行榜返回 code=-352（风控），必须先访问首页拿 buvid3
check("B 站榜先预热首页破 -352 风控", "www.bilibili.com" in bili_chart_src)
# rid=13/167 在 ranking/v2 返回 -400：番剧/国创属 PGC，接口不同
check("B 站 UGC 走 ranking/v2", "ranking/v2" in bili_chart_src)
check("B 站 PGC 走 pgc/season/rank", "pgc/season/rank" in bili_chart_src)
check("B 站榜有缓存", "_CACHE" in bili_chart_src)

discover_src = pathlib.Path("app/services/discover.py").read_text(encoding="utf-8")
check("发现榜 5 个分类（含 bilibili）", '"bilibili"' in discover_src)
# 相对纯榜单站的价值点：标注本地站点已有多少片源
check("发现榜标注本地已有片源", "_annotate_local" in discover_src)
check("发现榜聚合总览", "async def overview" in discover_src)
# 真实列名是 site（不是 site_name），照抄猜测的列名会静默失效
check("本地片源统计用真实列名 site", "ResourceRecord.site" in discover_src)
check("标题解析来自 app.core.meta", "from app.core.meta import" in discover_src)

trending_router = pathlib.Path("app/api/routers/trending.py").read_text(encoding="utf-8")
for route in ("/discover", "/discover/categories", "/discover/{category}",
              "/bilibili/{partition}"):
    check(f"发现榜端点 {route} 存在", f'"{route}"' in trending_router)

# ---- v1.8.0：网盘扫码登录 ----
panlogin_src = pathlib.Path("app/services/panlogin/__init__.py").read_text(encoding="utf-8")
check("扫码会话有 TTL", "SESSION_TTL" in panlogin_src)
# 安全铁律：Cookie 是最高敏感度凭据，给前端的视图绝不能带上
check("扫码会话视图不含 cookie",
      '"""给前端的视图：**绝不包含 cookie**。"""' in panlogin_src)
check("扫码会话只存内存不落库", "_SESSIONS: dict" in panlogin_src)
check("会话可区分过期与不存在", "async def peek_session" in panlogin_src)

pan115_login_src = pathlib.Path("app/services/panlogin/pan115.py").read_text(encoding="utf-8")
check("115 扫码用官方 qrcodeapi", "qrcodeapi.115.com" in pan115_login_src)
check("115 换 Cookie 走 passportapi", "passportapi.115.com" in pan115_login_src)

baidu_login_src = pathlib.Path("app/services/panlogin/baidu.py").read_text(encoding="utf-8")
check("百度扫码走 passport", "passport.baidu.com" in baidu_login_src)
# 踩坑：unicast 返回双层 JSON；且 status/errno 的成功值是 0，
# 用 `x or -1` 会被假值吃掉，必须走 _as_int
check("百度 unicast 按双层 JSON 解析", "channel_v" in baidu_login_src)
check("百度整数转换不被 0 假值坑", "def _as_int" in baidu_login_src)
check("百度扫码失败引导改用 Cookie 导入", "Cookie 导入" in baidu_login_src)

quark_login_src = pathlib.Path("app/services/panlogin/quark.py").read_text(encoding="utf-8")
# 夸克登录需签名公参（x-pan-client-id/tm/token），逆向属对抗风控，沿用 ADR-34 不做
check("夸克如实说明不支持扫码", "x-pan-client-id" in quark_login_src)
check("夸克只有 Cookie 校验（无 start）", "async def start" not in quark_login_src)

pan_login_src = pathlib.Path("app/services/pan_login.py").read_text(encoding="utf-8")
check("登录能力由后端声明", "PROVIDERS: dict" in pan_login_src)
check("夸克声明 qrcode=False", '"qrcode": False' in pan_login_src)
# 校验不过就写库，只会让后续定时任务静默失败
check("校验不过不写库", "校验不过就不写库" in pan_login_src)
check("扫码成功后销毁会话", "await drop_session(token)" in pan_login_src)

pan_router = pathlib.Path("app/api/routers/pan.py").read_text(encoding="utf-8")
for route in ("/login/providers", "/login/qrcode", "/login/qrcode/{token}",
              "/login/complete", "/login/cookie", "/login/verify"):
    check(f"网盘登录端点 {route} 存在", f'"{route}"' in pan_router)

pan115_store_src = pathlib.Path("app/providers/panstorage/pan115.py").read_text(encoding="utf-8")
check("115 存储 Provider 能力全开", "supports_search = True" in pan115_store_src)
check("115 用 fid 区分文件与目录", "fid" in pan115_store_src)

check("前端有网盘登录弹窗", "panLoginDialog" in app_js)
check("前端有发现榜画板", "discoverBoard" in app_js)
check("前端发现榜可切列表", "discoverTable" in app_js)

# ---- v1.8.0：文档必须给出可操作的接入与部署步骤 ----
site_guide = docs_dir / "10-站点接入指南.md"
check("站点接入指南存在", site_guide.exists())
if site_guide.exists():
    guide_text = site_guide.read_text(encoding="utf-8")
    check("指南含 torznab 地址拼法", "api/v2.0/indexers" in guide_text)
    check("指南含 api_generic 字段映射", "api_generic" in guide_text)
    check("指南含 html_generic 选择器说明", "html_generic" in guide_text)
    check("指南说明 Cookie 从哪来", "F12" in guide_text or "开发者工具" in guide_text)
    check("指南含站点诊断读法", "诊断" in guide_text)
    check("指南含 403 站点手工 Cookie 处置", "403" in guide_text)

ops_text = (docs_dir / "07-运维手册.md").read_text(encoding="utf-8")
check("运维手册含 docker compose up", "docker compose up -d" in ops_text)
check("运维手册说明 PUID/PGID 怎么查", "PUID" in ops_text and "id -u" in ops_text)
# 下载目录不一致会让硬链接失效、变成整份拷贝，是最常见的部署事故
check("运维手册强调下载目录须与下载器一致", "硬链接" in ops_text)
check("运维手册含群晖/威联通路径说明", "/volume1" in ops_text)
check("运维手册含健康检查", "/api/health" in ops_text)
check("运维手册含备份迁移", "备份" in ops_text)


print()
print("=" * 60)
failed = [c for c in checks if not c[0]]
print(f"README 事实校验：{len(checks) - len(failed)}/{len(checks)} 通过")
for _, name, detail in failed:
    print(f"  FAIL {name} {detail}")
sys.exit(1 if failed else 0)
