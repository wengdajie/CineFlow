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

#: v1.9.0 起 README 只保留「声明 / 项目介绍 / 安装方案」三块，
#: 原先的特性、配置、API、插件、测试、FAQ 等长章节被拆到 docs/12~15。
#: 事实性校验关心的是「文档里有没有写」，而不是「写在哪个文件」，
#: 所以这里把 README 与拆分出去的四个文档拼成一份语料统一断言，
#: 另外保留 README_ONLY 用于校验必须留在首页的内容（声明与安装）。
README_ONLY = pathlib.Path("README.md").read_text(encoding="utf-8")
_SPLIT_DOCS = (
    "docs/12-功能特性详解.md",
    "docs/13-配置与API参考.md",
    "docs/14-开发指南.md",
    "docs/15-常见问题.md",
    # 「接入你自己的站点」整章并入了站点接入指南（含下载器/媒体服务器/通知/yt-dlp/导航站）
    "docs/10-站点接入指南.md",
)
README = README_ONLY + "\n".join(
    pathlib.Path(p).read_text(encoding="utf-8") for p in _SPLIT_DOCS
)
SCRIPTS_README = pathlib.Path("scripts/README.md").read_text(encoding="utf-8")
#: v1.13.0：配置/端点这类「会被复制粘贴」的内容要单独断言，不能混在 README 语料里
API_DOC = pathlib.Path("docs/13-配置与API参考.md").read_text(encoding="utf-8")
checks = []


def check(name, ok, detail=""):
    checks.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


# ---- Provider 数量与名称 ----
from app.providers.registry import list_providers, load_builtin_providers  # noqa: E402

load_builtin_providers()
providers = list_providers()
names = {p["name"] for p in providers}
check("Provider 总数 32", len(providers) == 32, f"实际 {len(providers)}")
check("README 声明 32 个 Provider", "32 个注册 Provider" in README)
# v1.16.0：百度网盘的**扫码登录**早就有了，存储 Provider 却一直缺失，
# 导致登录成功后建出的站点是「僵尸站点」（总览查不到、转存/保活全静默跳过）。
check("baidu 存储 Provider 已注册", "baidu" in names)
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

check("21 张表", len(Base.metadata.tables) == 21, f"实际 {len(Base.metadata.tables)}")
check("README 声明 21 张表", "21 张表" in README)
for table in ("audit_logs", "pan_saves", "pan_subscribes", "strm_records",
              "site_health", "ranking_rules", "filter_rule_groups"):
    check(f"新增表 {table} 存在", table in Base.metadata.tables)

# ---- 默认站点 ----
from app.db.init_db import DEFAULT_SITES
from app.services import presets as presets_service  # noqa: E402  # noqa: E402

check("默认示例站点 20 条", len(DEFAULT_SITES) == 20, f"实际 {len(DEFAULT_SITES)}")
check("文档声明 20 条示例站点", "写入 20 条" in README)
# v1.16.0：删站的判据是「详情页能否取到真链接」，不是首页状态码。
# 这几个站实测搜不出东西（详见 docs/04 ADR-74），不该再出现在内置清单里。
_REMOVED_SITES = ("yyets", "wp_film")
for _p in _REMOVED_SITES:
    check(
        f"已下线的 provider {_p} 不再内置",
        not any(s["provider"] == _p for s in DEFAULT_SITES),
    )
# v1.16.1：清单瘦身对**升级用户**必须有迁移动作兜底。
# create_default_sites 只增不删，所以老库里 v1.14.0 写入的下线站点会一直留着，
# 表现为「启用了也永远搜不到东西」——只有全新安装才干净（详见 docs/04 ADR-77）。
import inspect as _inspect  # noqa: E402

from app.db import init_db as _init_db_module  # noqa: E402
from app.db.init_db import _RETIRED_SITES, retire_removed_sites  # noqa: E402

check("已下线站点有清理迁移", callable(retire_removed_sites))
check("下线清单含 10 个站点", len(_RETIRED_SITES) == 10, f"实际 {len(_RETIRED_SITES)}")
check(
    "init_db 调用下线清理",
    "retire_removed_sites()" in _inspect.getsource(_init_db_module.init_db),
)
# 一手删一手加回来会让迁移每次启动都删掉刚写入的记录
check(
    "下线清单与内置清单无交集",
    not ({s["name"] for s in DEFAULT_SITES} & set(_RETIRED_SITES)),
)
# 只删「没被用户碰过」的记录，凭据与改过的地址都必须拦住
_retire_src = _inspect.getsource(_init_db_module.retire_removed_sites)
_untouched_src = _inspect.getsource(_init_db_module._looks_untouched)
check("清理前检查是否出厂状态", "_looks_untouched" in _retire_src)
check(
    "出厂判定检查四类凭据",
    all(f in _untouched_src for f in ("api_key", "cookie", "username", "password")),
)
check("出厂判定比对出厂地址", "site.url" in _untouched_src)
check("清理动作会记账避免反复删除", "KEY_RETIRED_SEEDS" in _retire_src)
check("下线清理有回归用例", pathlib.Path("tests/test_seed_retire.py").exists())

check("默认站点含 webdav", any(s["provider"] == "webdav" for s in DEFAULT_SITES))
# 需要填地址/账号的站点必须默认禁用（示例值直接跑必然报错）。
# 白名单：yt-dlp 是本地库调用；kkso 系（kkso.net / zhuiju.us）是 v1.14.0 从
# awesome-zhuiju-free 清单实测筛出的公开网盘搜索站，不需要任何配置即可用。
DEFAULT_ENABLED_SITES = {
    "yt-dlp 视频下载",
    "KK 网盘搜",
    "追剧 zhuiju.us",
    "磁力熊 Cilixiong",
}
check(
    "需配置的示例站点默认禁用",
    all(
        not s.get("enabled", False)
        for s in DEFAULT_SITES
        if s["name"] not in DEFAULT_ENABLED_SITES
    ),
    str([s["name"] for s in DEFAULT_SITES
         if s.get("enabled") and s["name"] not in DEFAULT_ENABLED_SITES]),
)
check(
    "测试与文档共用同一份默认启用白名单",
    DEFAULT_ENABLED_SITES == __import__("tests.test_api", fromlist=["x"]).DEFAULT_ENABLED_SITES,
)
check(
    "白名单里的站点确实都默认启用",
    all(
        any(s["name"] == name and s.get("enabled") for s in DEFAULT_SITES)
        for name in DEFAULT_ENABLED_SITES
    ),
)
check(
    "yt-dlp 默认启用（本地库无需配置）",
    any(s["provider"] == "ytdlp" and s.get("enabled") for s in DEFAULT_SITES),
)
check("默认站点含 mukaku", any(s["provider"] == "mukaku" for s in DEFAULT_SITES))
# api_generic 的**示例站**已下线（example.com 占位，与 /sites/presets 重复），
# 但适配器本身必须还在，且要能在预设模板里找到，否则用户没法自助接入 JSON API 站。
check(
    "api_generic 适配器仍可用（作为预设模板提供）",
    any(p.get("provider") == "api_generic" for p in presets_service.list_presets()),
)
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
check("内置调度任务 16 个", len(job_ids) == 16, str(job_ids))
check("README 声明 16 内置任务", "16 内置任务" in README)
for job in ("cineflow.radar", "cineflow.pan_transfer", "cineflow.pan_subscribe",
            "cineflow.pan_keepalive",
            "cineflow.strm_sync", "cineflow.scrape", "cineflow.upgrade",
            "cineflow.site_health", "cineflow.ranking", "cineflow.zhuiju_sync"):
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
    check("API 端点 179 个", total == 179, f"实际 {total}")
    check("README 声明 179 个端点", "179 个端点" in README and "共 179 个" in README)
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
                 "/api/v1/images/proxy",
                 # v1.10.0：YouTube 榜 + 下载器独立管理（从站点管理搬到设置页）
                 "/api/v1/trending/youtube/{region}",
                 "/api/v1/downloaders", "/api/v1/downloaders/schema",
                 "/api/v1/downloaders/{site_id}",
                 "/api/v1/downloaders/{site_id}/test",
                 # v1.11.0：更新日志（解析 docs/08 变更日志）
                 "/api/v1/system/changelog",
                 # v1.13.0：资源类型→下载方式路由 + 内置 AI 站点分析
                 "/api/v1/downloads/routing",
                 "/api/v1/ai/config", "/api/v1/ai/analyze",
                 "/api/v1/ai/verify", "/api/v1/ai/apply"):
        check(f"端点存在 {path}", path in paths)
except Exception as exc:
    check("API 端点校验（需先起服务）", False, str(exc)[:80])

# ---- router 数量 ----
router_src = pathlib.Path("app/api/router.py").read_text(encoding="utf-8")
router_count = router_src.count("api_router.include_router(")
check("router 25 个", router_count == 25, f"实际 {router_count}")
check("README 声明 25 个 router", "25 个 router" in README)
for name in ("trending", "schedules", "pan", "chatops", "strm", "pan_subscribes",
             "users", "site_health", "ranking", "rule_groups"):
    check(f"router {name} 已挂载", f"{name}.router" in router_src)

# ---- 前端页面 ----
app_js = pathlib.Path("web/assets/app.js").read_text(encoding="utf-8")
pages_block = app_js[app_js.index("const PAGES"): app_js.index("];", app_js.index("const PAGES"))]
page_count = pages_block.count("{ key:")
check("前端页面 23 个", page_count == 23, f"实际 {page_count}")
check("README 声明点检 23 个页面", "23 个页面" in README)
check("scripts/README 声明 23 个页面", "23 个前端页面" in SCRIPTS_README)
check("前端含热度排行页", '"trending"' in app_js and "pageTrending" in app_js)
check("前端含定时任务页", '"schedules"' in app_js and "pageSchedules" in app_js)
check("README 声明 23 个功能页", "23 个功能页" in README)
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
check("测试文件 54 个", len(test_files) == 54, str(test_files))
check("README 声明 54 个测试文件", "54 个测试文件" in README)
for name in ("test_custom_sites.py", "test_radar.py", "test_trending.py",
             "test_panstorage.py", "test_chatops.py", "test_nfo.py",
             "test_scraper.py", "test_webdav.py", "test_strm_sync.py",
             "test_pan_subscribe.py", "test_upgrade.py", "test_categories.py",
             "test_config_store.py", "test_rules.py", "test_site_health.py",
             "test_ranking.py", "test_rule_groups.py", "test_users.py"):
    check(f"README 提及 {name}", name in README)

# ---- 脚本 ----
for script in ("smoke_test.py", "ui_check.py", "demo_pipeline.py", "live_check.py",
               "verify_docs.py", "research_refs.py", "verify_yaml.py"):
    check(f"脚本存在 {script}", pathlib.Path("scripts", script).exists())
    check(f"scripts/README 提及 {script}", script in SCRIPTS_README)
check("文档声明七个验证脚本", "七个开发期验证工具" in README)
# 测试数写在两处（README 徽章 + 开发指南的注释），历史上更新时漏改过一处。
# 与其把数字写死在门禁里，不如**要求两处一致**：这样以后改一处漏一处就会红，
# 而不是每次都得再改门禁自己。
_badge = re.search(r"tests-(\d+)%20passed", README_ONLY)
check("README 有测试徽章", _badge is not None)
# 这里 docs_dir 还没定义（在下方），直接用相对路径
_guide = re.search(r"pytest -q\s+# (\d+) passed",
                   pathlib.Path("docs/14-开发指南.md").read_text(encoding="utf-8"))
check("开发指南写明测试数", _guide is not None)
if _badge and _guide:
    check("README 徽章与开发指南的测试数一致",
          _badge.group(1) == _guide.group(1),
          f"徽章 {_badge.group(1)} vs 指南 {_guide.group(1)}")
check("README 版本号 1.18.0", "1.18.0" in README_ONLY)
version_src = pathlib.Path("app/core/version.py").read_text(encoding="utf-8")
check("代码版本号为 1.18.0", 'APP_VERSION = "1.18.0"' in version_src)
check("README 声明 314 项接口用例", "314 项真实 HTTP 接口用例" in README)
check("scripts/README 声明 314 项", "314 项接口用例" in SCRIPTS_README)

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
    check("路线图候选指向 v2.0.0", "v2.0.0 候选" in roadmap_text)

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
check("可编辑配置项 69 个", len(config_store.EDITABLE) == 69, f"实际 {len(config_store.EDITABLE)}")
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
      "downloader_candidates" in pathlib.Path("app/services/download_routing.py").read_text(encoding="utf-8")
      and "candidates_for" in pathlib.Path("app/services/download.py").read_text(encoding="utf-8"))
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
check("前端有画板渲染", "discoverBoard" in app_js and "board-card" in app_js)
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
check("运维手册指向飞牛专用指南", "11-飞牛NAS部署指南" in ops_text)

# ---- 飞牛 fnOS 部署指南：钉住那些抄群晖教程会踩的差异 ----
fnos_guide = docs_dir / "11-飞牛NAS部署指南.md"
check("飞牛部署指南存在", fnos_guide.exists())
if fnos_guide.exists():
    fnos_text = fnos_guide.read_text(encoding="utf-8")
    # 飞牛是 /vol1，抄群晖的 /volume1 会挂载到空目录——这是最容易踩的坑
    check("飞牛指南用 /vol1 路径", "/vol1" in fnos_text)
    check("飞牛指南提醒别用群晖 /volume1", "/volume1" in fnos_text)
    check("飞牛指南含 PUID/PGID 实查命令", "id -u" in fnos_text and "id -g" in fnos_text)
    # 硬链接要求同一文件系统，跨存储空间会报 cross-device link
    check("飞牛指南含硬链接同盘校验", "stat -c" in fnos_text
          and "cross-device" in fnos_text)
    check("飞牛指南含健康检查与 scheduler 判读", "/api/health" in fnos_text
          and "scheduler" in fnos_text)
    # 容器内 127.0.0.1 指容器自己，下载器地址必须用服务名
    check("飞牛指南强调下载器用服务名", "qbittorrent:8080" in fnos_text)
    check("飞牛指南含 fnOS 影视 App 联动", "影视" in fnos_text)
    check("飞牛指南含公网暴露前改密提醒", "改密" in fnos_text or "改密码" in fnos_text)
    check("飞牛指南含自检清单", "自检清单" in fnos_text)


# ---- v1.9.0：榜单分页 / 榜单与搜索合体 / README 瘦身 ----
# 这一段专门钉住「文档搬家不能把事实弄丢」以及分页的真实行为。
readme_lines = README_ONLY.count("\n") + 1
# README 精简的目标是「只留声明/介绍/安装」，行数是最直观的回归指标
check("README 已精简到 400 行内", readme_lines <= 400, f"实际 {readme_lines} 行")
check("README 保留学习交流声明",
      ("学习交流" in README_ONLY or "学习与交流" in README_ONLY)
      and "请勿在任何国内平台宣传" in README_ONLY
      and "严禁用于任何商业用途" in README_ONLY)
check("README 保留项目介绍", "这是什么" in README_ONLY)
check("README 保留安装方案", "docker compose up -d" in README_ONLY
      and "python -m app.main" in README_ONLY)
# 长章节必须真的搬走，而不是复制一份留在首页（两份会各自漂移）
check("README 不再内联核心特性长章节", "## ✨ 核心特性" not in README_ONLY)
check("README 不再内联插件开发章节", "## 🧩 插件开发" not in README_ONLY)
check("README 不再内联 API 章节", "## 📡 API" not in README_ONLY)
for split_doc in ("docs/12-功能特性详解.md", "docs/13-配置与API参考.md",
                  "docs/14-开发指南.md", "docs/15-常见问题.md"):
    p = pathlib.Path(split_doc)
    check(f"{split_doc} 存在", p.exists())
    check(f"README 指向 {split_doc}", split_doc in README_ONLY)
    check(f"docs/README 索引 {split_doc}", p.name in docs_index)

# 分页：三层都得认 offset，且 has_more 由服务端给出
import inspect  # noqa: E402

from app.services import discover as _discover  # noqa: E402

for fn_name in ("chart", "bili_categories_chart"):
    sig = inspect.signature(getattr(_discover, fn_name))
    check(f"discover.{fn_name} 支持 offset", "offset" in sig.parameters)
from app.providers.indexer import bili_chart as _blc  # noqa: E402
from app.providers.metadata import douban_chart as _dbc  # noqa: E402

check("豆瓣 Provider 支持 offset",
      "offset" in inspect.signature(_dbc.chart).parameters)
check("B 站 Provider 支持 offset",
      "offset" in inspect.signature(_blc.chart).parameters)
# 豆瓣缓存键必须带 offset，否则第二页会命中第一页缓存（v1.9.0 真实踩过）
check("豆瓣缓存键含 offset", "{offset}" in inspect.getsource(_dbc.chart))
# B 站接口无分页参数，只能服务端切片
check("B 站用服务端切片", "offset:" in inspect.getsource(_blc.chart)
      or "offset + limit" in inspect.getsource(_blc.chart))

trending_router = pathlib.Path("app/api/routers/trending.py").read_text(encoding="utf-8")
check("榜单端点暴露 offset 参数", "offset: int = Query(0" in trending_router)
check("榜单端点默认 30 条", "Query(30" in trending_router)

app_js = pathlib.Path("web/assets/app.js").read_text(encoding="utf-8")
check("前端分页大小为 30", "TRENDING_PAGE_SIZE = 30" in app_js)
check("前端有加载更多区域", "board-more" in app_js)
# v1.10.0（ADR-48）：页内搜索面板下线，改为跳转资源搜索页 + 回来复原位置。
# 这里断言死代码真的删干净了，避免只删渲染调用、留一堆用不到的函数。
for gone in ("trendingSearchPanel", "searchFor", "searchHere"):
    check(f"页内搜索死代码 {gone} 已删除", gone not in app_js)
check("榜单跳转搜索页并记录位置",
      "trendingState.restore" in app_js and "restoreScroll" in app_js)
# 切了页签就不该把用户拽回旧位置，所以复原前要比对四个维度
check("榜单复原会校验分类/分区/地区/视图未变",
      "snap.cat !== trendingState.discoverCat" in app_js
      and "snap.biliPartition" in app_js
      and "snap.ytRegion" in app_js
      and "snap.view" in app_js)
# 榜单是下拉分页的，只记 scrollY 会因页面不够高而落到底部
check("榜单复原会补加载回原有条数",
      "trendingState.items.length < snap.count" in app_js and "guard < 10" in app_js)
# 四个下线的页签与随之删掉的死代码都不该再出现
for gone in ("rankingBoard", "rankingView", "rankingTable", "trendingDetail"):
    check(f"死代码 {gone} 已删除", gone not in app_js)
style_css = pathlib.Path("web/assets/style.css").read_text(encoding="utf-8")
check("样式含 .board-more", ".board-more" in style_css)
# 面板删了，样式也必须一起删——留着就是死 CSS，日后没人敢动
for sel in (".trending-split", ".side-panel", ".side-results", ".side-item"):
    check(f"死样式 {sel} 已删除", sel not in style_css)

# ---- v1.10.0 · 六分类榜单（新增 YouTube）----
check("YouTube 榜单 Provider 存在",
      pathlib.Path("app/providers/indexer/yt_chart.py").exists())
yt_src = pathlib.Path("app/providers/indexer/yt_chart.py").read_text(encoding="utf-8")
# 公开 Piped 实例实测 8 个只活 1 个，故障转移是这个数据源能用的前提
check("YouTube 榜多实例故障转移",
      "DEFAULT_INSTANCES" in yt_src and "_instance_order" in yt_src)
check("YouTube 榜记住可用实例", "_PREFERRED" in yt_src)
check("YouTube 榜有失败退避", "_BACKOFF" in yt_src and "_mark_rate_limited" in yt_src)
# Piped 默认返回自家图片代理地址（不稳），必须还原成 ytimg 直链
check("YouTube 封面还原为 ytimg 直链", "i.ytimg.com" in yt_src)
discover_src = pathlib.Path("app/services/discover.py").read_text(encoding="utf-8")
check("发现榜含 youtube 分类", '"youtube"' in discover_src)
# kind 决定前端给「搜资源」还是「直接下载」，漏了 B站/YouTube 会显示错按钮
check("分类下发 kind 区分影视与视频",
      '"kind": "media"' in discover_src and '"kind": "video"' in discover_src)
check("前端按 kind 分流按钮",
      "discoverActions" in app_js and "videoDownloadButton" in app_js)
# 画质选择必须全链路打通，否则是个「选了不生效」的假功能
check("下载接口接收画质参数",
      "video_format" in pathlib.Path("app/api/routers/downloads.py").read_text(encoding="utf-8"))
check("yt-dlp 按所选画质补音轨",
      "video_format" in pathlib.Path("app/providers/downloader/ytdlp.py").read_text(encoding="utf-8"))

# ---- v1.10.0 · 下载器搬到设置页 ----
check("下载器字段清单模块存在",
      pathlib.Path("app/services/downloader_specs.py").exists())
specs_src = pathlib.Path("app/services/downloader_specs.py").read_text(encoding="utf-8")
# 「界面能填但代码不读」的假配置项必须被显式排除（实测抓到 3 个）
check("排除下载器读不到的公共字段", "EXCLUDED_COMMON" in specs_src)
check("字段区分存表列还是 options", '"target"' in specs_src)
check("下载器路由已挂载", "downloaders.router" in router_src)
check("站点管理页不再列下载器", 'item.kind !== "downloader"' in app_js)
check("设置页含下载器表单", "downloaderForm" in app_js and "downloaderCard" in app_js)
check("下载器规格有反向测试",
      pathlib.Path("tests/test_downloader_specs.py").exists())

# ---- v1.10.0 · 设置页布局 ----
# 只读项（服务/目录/安全）改了必须重启，给输入框就是假功能（ADR-18），
# 所以收进默认收起的折叠卡片，而不是删掉——用户仍需查当前生效值。
check("设置页拆分可改与只读组",
      "editableGroups" in app_js and "readonlyGroups" in app_js)
check("只读配置卡片默认收起", "readonlyCard" in app_js)
check("设置页多列布局", "cols-settings" in app_js and ".cols-settings" in style_css)
# multi-column 必须配 break-inside，否则卡片会被劈成两半跨列
check("多列布局防卡片劈裂", "break-inside: avoid" in style_css)

# 后端接口一律不删：榜单订阅还在消费资源热榜口径（ADR-43）
for kept in ("/resources", "/keywords", "/sites", "/live"):
    check(f"资源榜端点 {kept} 仍保留", f'"{kept}"' in trending_router
          or f"'{kept}'" in trending_router)

adr_text = (docs_dir / "04-决策记录.md").read_text(encoding="utf-8")
for adr in ("ADR-42", "ADR-43", "ADR-44", "ADR-48", "ADR-49", "ADR-50"):
    check(f"决策记录含 {adr}", adr in adr_text)
# YouTube 数据源绕过两条弯路（官方 trending 已下线、社区 playlist 是陈旧假数据），
# 这两条否决理由必须写进 ADR，否则下个人会再踩一遍
check("ADR 记录 YouTube 数据源否决方案",
      "Piped" in adr_text and "feed/trending" in adr_text)
check("ADR 说明下载器复用 SiteConfig 表",
      "SiteConfig" in adr_text and "零迁移" in adr_text)
check("ADR 说明 multi-column 而非 grid", "multi-column" in adr_text)
check("ADR 说明豆瓣真分页与 B 站切片差异",
      "page_start" in adr_text and "切片" in adr_text)
check("ADR 说明为什么保留后端榜单接口", "榜单自动订阅" in adr_text)
check("ADR 说明 Bangumi 而非自建放送表",
      "ADR-56" in adr_text and "weekday" in adr_text)
check("ADR 说明 yt-dlp 扁平提取而非 wbi 签名",
      "ADR-57" in adr_text and "wbi" in adr_text and "412" in adr_text)
check("ADR 说明增量判据用视频 ID",
      "ADR-58" in adr_text and "handled_ids" in adr_text)
check("ADR 说明通知不配即全收",
      "ADR-59" in adr_text and "全收" in adr_text)
check("ADR 说明限速只做全局且三家单位不同",
      "ADR-60" in adr_text and "1024" in adr_text and "enabled" in adr_text)
check("ADR 说明宁可没封面也不要错封面",
      "ADR-61" in adr_text and "sub_title" in adr_text)
check("ADR 说明排序后必须再保名额",
      "ADR-65" in adr_text and "enforce_site_share" in adr_text)
check("ADR 说明超时改为站点预算",
      "ADR-66" in adr_text and "SEARCH_TIMEOUT" in adr_text)
check("ADR 说明下载器能力位",
      "ADR-67" in adr_text and "supported_kinds" in adr_text)
# 92% 会员正片是实测数据，必须留在 ADR 里，否则下个人会以为「接上就什么都能下」
check("ADR 说明在线站只索引不接解析网关",
      "ADR-68" in adr_text and "92" in adr_text and "ADR-24" in adr_text)
check("ADR 说明 AI 默认关闭且只出建议",
      "ADR-69" in adr_text and "默认关闭" in adr_text)
# ---- v1.13.0 · 排序后再保名额 / 站点级超时预算 ----
search_src = pathlib.Path("app/services/search.py").read_text(encoding="utf-8")
filters_src = pathlib.Path("app/core/filters.py").read_text(encoding="utf-8")
# 缺陷根因：apply_site_quota 的轮转交错只在排序前成立，filter_and_rank 会全局
# 重排把交错打散，再 [:200] 一刀切就把评分天然低的站整站抹掉（实测 Nyaa/B站/YouTube 各 0 条）
check("排序后再做一次站点名额保护", "def enforce_site_share" in search_src)
check("名额保护按站点分桶轮转", "buckets" in search_src or "分桶" in search_src)
check("盘搜分组归并到同一站点", 'split("·")' in search_src)
check("排序仍是全局重排（名额保护不能靠它）", "def filter_and_rank" in filters_src)
# 超时从「每关键词」改成「每站点预算」：带季集时 build_keywords 产 3 个关键词，
# 旧写法让一个卡死站要花 3×SEARCH_TIMEOUT，gather 还得等最慢的
check("站点级超时预算", "_MIN_KEYWORD_TIMEOUT" in search_src)
check("超时提示写清是站点预算", "预算" in search_src)
check("搜索名额有反向测试", pathlib.Path("tests/test_search_share.py").exists())

# ---- v1.13.0 · 资源类型 → 下载器能力匹配 ----
routing_src = pathlib.Path("app/services/download_routing.py").read_text(encoding="utf-8")
dl_base_src = pathlib.Path("app/providers/downloader/base.py").read_text(encoding="utf-8")
check("下载器声明可收的资源类型", "supported_kinds" in dl_base_src and "def accepts" in dl_base_src)
for func in ("route_of", "label_of", "hint_of", "candidates_for", "check", "describe"):
    check(f"下载路由函数 {func}", f"def {func}" in routing_src)
# 只装 yt-dlp 时投磁力会被标成「正在下载」，是实测复现出来的真缺陷
check("yt-dlp 只收网页视频",
      "WEBVIDEO" in pathlib.Path("app/providers/downloader/ytdlp.py").read_text(encoding="utf-8"))
check("aria2 收直链与种子",
      "DIRECT" in pathlib.Path("app/providers/downloader/aria2.py").read_text(encoding="utf-8"))
check("缺下载器给可行动提示", "hint_of" in pathlib.Path("app/services/download.py").read_text(encoding="utf-8"))
check("网盘缺 aria2 时说明原因", "pan_pending_hint" in routing_src)
check("下载路由端点已暴露", "/routing" in pathlib.Path("app/api/routers/downloads.py").read_text(encoding="utf-8"))
check("前端按能力置灰下载按钮", "downloadRouting" in app_js and "routeFor" in app_js)
check("下载路由有反向测试", pathlib.Path("tests/test_download_routing.py").exists())

# ---- v1.13.0 · MacCMS 在线影视站只做索引 ----
maccms_src = pathlib.Path("app/providers/indexer/maccms.py").read_text(encoding="utf-8")
check("MacCMS 适配器存在", "class MacCmsIndexer" in maccms_src)
check("解析 player_aaaa 播放配置", "parse_player_config" in maccms_src)
check("产出网页视频而非直链", "WEBVIDEO" in maccms_src)
# 底线：这类站「什么都能下」靠的是 VIP 解析网关（依赖盗取会员票据），按 ADR-24 不接。
# 只查可执行代码：docstring 里必须能引用网关地址来解释「为什么不接」，
# 否则这条决策的依据就没地方写了（tests/test_maccms.py 用同一口径）。
_maccms_code = maccms_src
for _doc in re.findall(r'"""(?:.|\n)*?"""', maccms_src):
    _maccms_code = _maccms_code.replace(_doc, "")
for banned in ("xiguadh", "jiexi", "/?url="):
    check(f"可执行代码不接解析网关 {banned}", banned not in _maccms_code.lower())
check("会员正片如实标注", "paywalled" in pathlib.Path("app/providers/base.py").read_text(encoding="utf-8"))
check("前端标出会员资源", "会员" in app_js)
check("MacCMS 有反向测试", pathlib.Path("tests/test_maccms.py").exists())

# ---- v1.13.0 · 内置 AI 站点分析 ----
ai_src = pathlib.Path("app/services/ai_site.py").read_text(encoding="utf-8")
ai_router_src = pathlib.Path("app/api/routers/ai.py").read_text(encoding="utf-8")
check("AI 服务存在", "PROVIDER_CHOICES" in ai_src)
for func in ("is_configured", "describe", "condense", "extract_json", "chat",
             "analyze_site", "verify"):
    check(f"AI 服务函数 {func}", f"def {func}" in ai_src)
# 默认关闭是硬要求：开启意味着把站点页面正文发给第三方模型
check("AI 默认关闭", settings.AI_ENABLED is False)
check("AI 走 OpenAI 兼容接口", "chat/completions" in ai_src)
check("AI 三步走：analyze/verify/apply",
      all(f'"/{name}"' in ai_router_src for name in ("analyze", "verify", "apply")))
check("AI 建议落库留痕", "_ai_generated" in ai_router_src)
check("AI 建站默认不启用", "enabled: bool = False" in ai_router_src)
check("AI 路由已挂载", "ai.router" in router_src)
check("AI 配置项在设置页可改",
      all(key in config_store.EDITABLE for key in
          ("AI_ENABLED", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_TIMEOUT",
           "AI_MAX_PAGE_CHARS", "AI_TEMPERATURE")))
# 敏感项「留空 = 不修改」：脱敏不回显，改隔壁字段会把密钥洗成空串
check("敏感项留空不清空", "def is_secret" in cfgstore_src and "不修改" in cfgstore_src)
check("前端有 AI 分析入口", "aiSiteDialog" in app_js)
check("AI 分析按钮图标存在", "sparkles: '" in app_js)
check("提示条样式存在", ".notice {" in style_css and ".notice.warn" in style_css)
check("AI 有反向测试", pathlib.Path("tests/test_ai_site.py").exists())

# ---- v1.13.0 · 文档必须把能力边界与操作步骤写清楚 ----
features_doc = (docs_dir / "12-功能特性详解.md").read_text(encoding="utf-8")
sites_doc = (docs_dir / "10-站点接入指南.md").read_text(encoding="utf-8")
# 「搜到很多能下的少」如果不写清楚，用户会当成 bug 反复折腾配置
for doc_name, doc_text in (("12-功能特性详解", features_doc), ("10-站点接入指南", sites_doc)):
    check(f"{doc_name} 写明会员正片占比", "92%" in doc_text)
    check(f"{doc_name} 声明不接解析网关", "解析网关" in doc_text)
check("功能文档说明 cz4k 被 WAF 拦", "WAF" in features_doc and "468" in features_doc)
# 名额修复要写进文档：否则下个人看到「轮转交错」会以为问题早就解决了
check("功能文档更正名额保护时机", "排序之后" in features_doc and "632" in features_doc)
check("功能文档说明超时是站点预算", "单站点的总预算" in features_doc)
# 资源类型→下载器的对照表是用户最常查的东西
check("功能文档给出资源类型对照", "supported_kinds" in features_doc or "可用下载器" in features_doc)
check("站点指南给出资源类型对照", "必须装" in sites_doc)
check("站点指南含 AI 三步走", "试跑验证" in sites_doc and "默认不启用" in sites_doc)
check("站点指南含 maccms 模板路径", "从模板添加" in sites_doc and "maccms" in sites_doc)
# AI 密钥「留空不改」这个行为反直觉，必须在文档里明说
check("文档说明敏感项留空不改",
      "留空 = 不修改" in features_doc or "留空表示不修改" in API_DOC)
check("文档给出 OpenAI 兼容服务对照表",
      all(word in features_doc for word in ("deepseek-chat", "11434", "qwen-plus")))
check("配置文档含 7 个 AI 配置项",
      all(f"CF_AI_{key}" in API_DOC for key in
          ("ENABLED", "BASE_URL", "API_KEY", "MODEL", "TIMEOUT", "MAX_PAGE_CHARS", "TEMPERATURE")))
check("配置文档说明搜索超时语义已变", "单个站点的总预算" in API_DOC)
check("配置文档列出 AI 端点", "/ai/analyze" in API_DOC and "/ai/verify" in API_DOC)
check("配置文档列出下载路由端点", "/downloads/routing" in API_DOC)

changelog_text = (docs_dir / "08-变更日志.md").read_text(encoding="utf-8")
check("变更日志含 v1.9.0", "## v1.9.0" in changelog_text)
check("变更日志含 v1.10.0", "## v1.10.0" in changelog_text)
check("变更日志含 v1.12.0", "## v1.12.0" in changelog_text)
check("变更日志含 v1.12.1", "## v1.12.1" in changelog_text)
check("变更日志含 v1.13.0", "## v1.13.0" in changelog_text)
check("变更日志含 v1.14.0", "## v1.14.0" in changelog_text)
check("变更日志含 v1.17.0", "## v1.17.0" in changelog_text)
check("变更日志含 v1.18.0 节", "## v1.18.0" in changelog_text)
check("变更日志记录破坏性变更", "破坏性变更" in changelog_text)
roadmap_all = (docs_dir / "03-升级路线图.md").read_text(encoding="utf-8")
for milestone in ("M30", "M31", "M32", "M33", "M34", "M35", "M36",
                  "M37", "M38", "M39", "M40", "M41", "M42", "M43", "M44", "M45",
                  "M46", "M47"):
    check(f"路线图含里程碑 {milestone}", f"里程碑 {milestone}" in roadmap_all)


# ---- v1.14.0：awesome-zhuiju-free 社区站点清单接入 ----
config_src = pathlib.Path("app/core/config.py").read_text(encoding="utf-8")
#: /openapi.json 拉不到时 paths 不存在，退化成空集合让相关项显式 FAIL 而不是抛异常
paths = globals().get("paths") or set()
zhuiju_src = pathlib.Path("app/services/zhuiju.py").read_text(encoding="utf-8")
kkso_src = pathlib.Path("app/providers/pan/kkso.py").read_text(encoding="utf-8")
check("社区清单服务存在", pathlib.Path("app/services/zhuiju.py").exists())
check("kkso Provider 存在", pathlib.Path("app/providers/pan/kkso.py").exists())
check("kkso 已注册", "kkso" in names)
check("社区清单测试存在", pathlib.Path("tests/test_zhuiju.py").exists())

# CC-BY-4.0 要求署名：代码常量、接口返回、README 致谢三处都要有
check("上游仓库常量正确", 'UPSTREAM_REPO = "laoma2053/awesome-zhuiju-free"' in zhuiju_src)
check("上游许可证已声明", 'UPSTREAM_LICENSE = "CC-BY-4.0"' in zhuiju_src)
check("README 致谢 awesome-zhuiju-free", "awesome-zhuiju-free" in README_ONLY)
check("README 标注上游许可证", "CC-BY-4.0" in README_ONLY)
check("前端展示上游署名", "awesome-zhuiju-free" in app_js and "CC-BY" in app_js)

# ADR-70 的核心结论：上游只探首页状态码，reachable 不等于搜得到，
# 所以我们自己「真搜一次」并四档判定，只有 searchable 允许一键落库
for token in ("searchable", "reachable_only", "blocked", "unknown"):
    check(f"探测状态 {token}", f'"{token}"' in zhuiju_src)
check("只允许 searchable 落库", 'if str(row.get("probe")) != "searchable":' in zhuiju_src)
check("探测用真实关键词", "PROBE_KEYWORDS" in zhuiju_src)
check("清单缓存落盘", "zhuiju_catalog.json" in zhuiju_src)
check("拉取失败回退缓存", "回退缓存" in zhuiju_src)

# 三个新端点
for path in ("/api/v1/sites/catalog", "/api/v1/sites/catalog/probe",
             "/api/v1/sites/catalog/{entry_id}/apply"):
    check(f"端点存在 {path}", path in paths)

# 定时同步
check("清单同步任务已注册", "JOB_ZHUIJU_SYNC" in scheduler_src)
check("清单同步任务键", 'key="zhuiju_sync"' in scheduler_src)
for key in ("ZHUIJU_SYNC_ENABLED", "ZHUIJU_SYNC_INTERVAL_MINUTES",
            "ZHUIJU_PROBE_ON_SYNC", "ZHUIJU_PROBE_LIMIT"):
    check(f"清单配置项 {key}", key in config_src and key in config_store.EDITABLE)
check("设置页含社区清单分组",
      "社区站点清单（awesome-zhuiju-free）" in pathlib.Path("app/api/routers/system.py").read_text(encoding="utf-8"))

# 默认站点：kkso 系两站默认启用，btsj6 默认禁用
_by_name = {s["name"]: s for s in DEFAULT_SITES}
for site in ("KK 网盘搜", "追剧 zhuiju.us"):
    check(f"默认站点含 {site}", site in _by_name)
    check(f"{site} 默认启用", _by_name.get(site, {}).get("enabled") is True)
check("默认站点含 BT 世界网", "BT 世界网" in _by_name)
check("BT 世界网默认禁用", _by_name.get("BT 世界网", {}).get("enabled") is False)

# 文档必须写清「上游 reachable 不等于能搜到」这个反直觉结论
_zhuiju_docs = "\n".join(
    (docs_dir / f).read_text(encoding="utf-8")
    for f in ("04-决策记录.md", "10-站点接入指南.md", "08-变更日志.md")
)
check("文档记录 ADR-70", "ADR-70" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
check("文档说明 reachable 不等于可搜索", "reachable" in _zhuiju_docs and "searchable" in _zhuiju_docs)
check("文档致谢上游项目", "awesome-zhuiju-free" in _zhuiju_docs)

# ---- v1.15.0：搜索熔断 + 下载「真实结局」 ----
breaker_src = pathlib.Path("app/services/search_breaker.py").read_text(encoding="utf-8")
search_src = pathlib.Path("app/services/search.py").read_text(encoding="utf-8")
routing_src = pathlib.Path("app/services/download_routing.py").read_text(encoding="utf-8")
dl_router_src = pathlib.Path("app/api/routers/downloads.py").read_text(encoding="utf-8")
check("搜索熔断服务存在", pathlib.Path("app/services/search_breaker.py").exists())
check("搜索熔断测试存在", pathlib.Path("tests/test_search_breaker.py").exists())

# ① 下载必须如实回报结局：失败不能报 success:true
check("下载按真实状态回报成败",
      "ok = task.status != TaskStatus.FAILED.value" in dl_router_src)
check("下载失败带出原因", 'payload["message"] = task.error' in dl_router_src)
check("网盘 pending 给下一步提示", "pan_pending_hint()" in dl_router_src)
check("前端按 success 分别提示", "res.success === false" in app_js)
check("toast 支持 warn 变体", ".toast.warn" in
      pathlib.Path("web/assets/style.css").read_text(encoding="utf-8"))

# ② 前置检查要看连通性，判据必须是 down（写死 "error" 会静默失效）
check("routing 检查下载器连通性", "downloader_reachability" in routing_src)
check("连通性判据用 SiteHealthStatus.DOWN",
      "SiteHealthStatus.DOWN.value" in routing_src)
check("routing 带出 unreachable", '"unreachable": dead' in routing_src)

# ③ 慢站熔断：只对「吃满预算且零结果」计数
for token in ("record_timeout", "record_success", "is_open", "skip_reason", "snapshot"):
    check(f"熔断器函数 {token}", f"def {token}" in breaker_src)
check("熔断在搜索链路生效", "search_breaker.is_open(provider.site_name)" in search_src)
check("命中后清零熔断计数",
      "search_breaker.record_success(provider.site_name)" in search_src)
check("跳过状态写进诊断", '"skipped"' in search_src)
check("熔断只认超时且零结果", 'outcome.status == "timeout"' in search_src)
for key in ("SEARCH_BREAKER_ENABLED", "SEARCH_BREAKER_THRESHOLD",
            "SEARCH_BREAKER_COOLDOWN_MINUTES"):
    check(f"熔断配置项 {key}", key in config_src and key in config_store.EDITABLE)
for path in ("/api/v1/search/breaker", "/api/v1/search/breaker/reset"):
    check(f"端点存在 {path}", path in paths)

# ④ Docker 版本不一致：:latest 是可变 tag，必须 pull_policy: always
for compose in ("docker-compose.yml", "docker-compose.fnos.yml"):
    text = pathlib.Path(compose).read_text(encoding="utf-8")
    check(f"{compose} 声明 pull_policy", "pull_policy: always" in text)
    check(f"{compose} 说明为何需要", "可变 tag" in text)

_v15_docs = "\n".join(
    (docs_dir / f).read_text(encoding="utf-8")
    for f in ("04-决策记录.md", "08-变更日志.md", "15-常见问题.md")
)
check("文档记录 ADR-71", "ADR-71" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
check("文档记录 ADR-72", "ADR-72" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
check("文档说明镜像不更新的原因", "pull_policy" in _v15_docs)
check("文档说明熔断机制", "熔断" in _v15_docs)

# ⑤ 静默缺口：白名单里能改的项，设置页必须有入口（本轮实测发现 4 项没挂分组）
from app.api.routers.system import SETTING_GROUPS  # noqa: E402

_shown_keys = {k for g in SETTING_GROUPS for k in g["keys"]}
_orphan = sorted(k for k in config_store.EDITABLE if k not in _shown_keys)
check("可改配置全部有设置页入口", _orphan == [], f"没挂分组：{_orphan}")
check("设置页分组 18 组", len(SETTING_GROUPS) == 18, f"实际 {len(SETTING_GROUPS)}")

# ⑥ 后端返回的每站诊断必须真的渲染出来（v1.6.0 就有数据，界面一直没用）
# 判据要钉住「定义 + 真的被调用」：只查函数名前缀的话，改个名字照样能通过
check("搜索页渲染站点情况卡片",
      "function siteReportCard(sites, onReset)" in app_js
      and "siteReportCard(searchState.sites" in app_js)
# 只截固定字符数会把后面 ORDER 表里的同名 key 也算进来，必须严格切到对象结尾
_status_body = app_js.split("const SITE_STATUS = {")[1].split("};")[0]
check("站点情况覆盖五种状态",
      all(f"{key}:" in _status_body
          for key in ("ok", "empty", "timeout", "skipped", "error")),
      _status_body.replace("\n", " ")[:80])
check("搜索逐站累积站点诊断", "searchState.sites.push(event.site)" in app_js)
check("done 事件用完整诊断覆盖累积值", "searchState.sites = event.sites" in app_js)
check("有站点未出货时提示不报全绿", "个站点未出货" in app_js)
check("站点情况提供解除熔断入口", "/search/breaker/reset" in app_js)

# ⑦ 后端白名单里带专用 Referer 的图床，前端必须真的走代理
_img_src = pathlib.Path("app/api/routers/images.py").read_text(encoding="utf-8")
check("Bangumi 图床在后端白名单", '"bgm.tv"' in _img_src)
check("Bangumi 封面走图片代理", '"bgm.tv"' in app_js.split("const PROXY_HOSTS")[1][:200])

# ⑧ 切页必须关掉遗留弹窗：遮罩留在 #modal-root 会让新页面「看得见点不动」
check("存在关闭遗留弹窗的函数", "function closeAllModals()" in app_js)
check("closeAllModals 在 render 里被调用",
      "closeAllModals();" in app_js.split("async function render()")[1][:400])
check("closeAllModals 清空 modal-root",
      'getElementById("modal-root")' in
      app_js.split("function closeAllModals()")[1][:260])

# ⑨ 同源封面图会占满浏览器连接池，切屏后必须把脱离文档的图请求还回来
check("存在封面在飞清单", "const livePosters = []" in app_js)
# 判据不能只查子串：注释掉的 "// livePosters.push(image);" 也含这段文本，
# 改坏验证会漏。必须确认存在一行**未被注释**的调用。
check("封面登记进在飞清单",
      any(line.strip().startswith("livePosters.push(image)")
          for line in app_js.splitlines()))
check("存在脱离文档的封面回收函数", "function abortDetachedPosters()" in app_js)
# 判据必须是 isConnected（脱离文档）而不是「切页全清」：全清会把当前屏
# 正在加载的封面一起掐掉，新页面封面会全变占位块
check("回收判据用 isConnected",
      "!image.isConnected" in app_js.split("function abortDetachedPosters()")[1][:600])
# shell() 是所有页面渲染的唯一出口，挂在 render() 上会漏掉 pageTrending 的自调用
check("shell 收尾回收封面连接",
      "abortDetachedPosters();" in app_js.split("function shell(")[1].split("\n  }")[0])
check("榜单重绘 listBox 后也回收",
      app_js.count("abortDetachedPosters();") >= 2)
# 钉死标题行本身："ADR-73" 这种裸子串被 "ADR-73X" 之类改名照样能过
check("ADR-73 记录连接池取舍",
      "## ADR-73 ·" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
_uic = pathlib.Path("scripts/ui_check.py").read_text(encoding="utf-8")
check("ui_check 放行主动中止的封面请求",
      'if "err_aborted" in lowered and _looks_like_image(lowered):' in _uic
      and "def _looks_like_image(" in _uic)
# 子资源报错不带 URL 的话根本没法定位是谁挂了，必须把 location 带出来
check("ui_check 控制台报错带出处", 'msg.location or {}' in _uic)
# 失败请求记成 "GET <url> -> <failure>"，拿整行判后缀永远是 False
check("ui_check 从失败行里切出 URL 再判后缀", "def _request_url(" in _uic)
# 控制台报错记成 "... <- url"，与失败请求的 "GET url -> failure" 格式不同，两种都要能切
check("ui_check 认得控制台报错的出处格式", '" <- " in lowered' in _uic)
# 外站图 404/403 是上游下架，属噪声；但**本机**的图片代理报错是真缺陷，
# 本轮正是靠它抓出 Bangumi 原图超时 502 与 TLS 抖动 502，绝不能一起放行
_noise_body = _uic.split("def _is_noise(")[1].split("\nreal_errors")[0]
check("ui_check 放行外站图 404/403",
      '"status of 404" in lowered' in _noise_body)
check("ui_check 不放行本机图片代理报错",
      'BASE.replace("http://", "") not in lowered' in _noise_body
      and _noise_body.count('BASE.replace("http://", "") not in lowered') >= 2)

# ⑩ Bangumi 卡片封面必须取 common 而不是原图 large（原图会把代理 15s 超时打爆 → 502 裂图）
_bgm_src = pathlib.Path("app/providers/metadata/bangumi.py").read_text(encoding="utf-8")
check("Bangumi 有封面尺寸优先级表", "COVER_SIZE_PRIORITY" in _bgm_src)
check("Bangumi 封面优先 common", 'COVER_SIZE_PRIORITY = ("common"' in _bgm_src)
# large 排在 medium 后面：谁把原图挪回第一位，这条就红
_prio = _bgm_src.split("COVER_SIZE_PRIORITY = (")[1].split(")")[0]
check("Bangumi 原图不得排在缩略图之前",
      _prio.index('"large"') > _prio.index('"medium"'), _prio)
check("Bangumi 用 pick_cover 取封面", "poster = pick_cover(item.get(\"images\"))" in _bgm_src)
check("Bangumi 封面尺寸有实测依据", "13216 ms" in _bgm_src)

# ⑪ 图片代理必须能吸收连接层抖动：bgm.tv 的 TLS 实测会被间歇掐断，
# 而镜像轮换只对豆瓣有效（其它图床只有 1 个候选），一次抖动就 502 → 随机裂图
check("图片代理声明单候选重试次数", "MAX_ATTEMPTS_PER_CANDIDATE = " in _img_src)
_retry_block = _img_src.split("for candidate in candidates:")[1][:900]
check("图片代理对连接异常重试",
      "for attempt in range(MAX_ATTEMPTS_PER_CANDIDATE)" in _retry_block)
# 拿到响应必须 break：403/404 重试多少次都一样，重试反而拖慢
check("图片代理不重试 HTTP 状态码错误", "break" in _retry_block)
check("重试次数有实测依据", "UNEXPECTED_EOF" in _img_src)
check("图片代理重试有回归用例",
      "def test_proxy_retries_after_tls_drop(" in
      pathlib.Path("tests/test_image_proxy.py").read_text(encoding="utf-8"))

# ---- v1.17.0：流式搜索 + Jackett 批量接入 ----
# ① 流式搜索：整页等待 = 最慢站耗时，这条是本轮「搜索慢」的根因
_search_src = pathlib.Path("app/services/search.py").read_text(encoding="utf-8")
_search_api = pathlib.Path("app/api/routers/search.py").read_text(encoding="utf-8")
check("搜索服务提供流式接口", "async def search_stream(" in _search_src)
_stream_body = _search_src.split("async def search_stream(")[1].split("def _title_variants(")[0]
# 判据必须钉死 as_completed：换回 gather 就退化成「等所有站收齐」，
# 接口还在、事件还在，但用户那边又变成一次性出结果（本轮注入缺陷验证过）
check("流式搜索按完成顺序下发", "asyncio.as_completed(tasks)" in _stream_body)
_stream_code = "\n".join(
    line for line in _stream_body.splitlines()
    if not line.lstrip().startswith("#") and "``asyncio.gather``" not in line
)
check("流式搜索不用 gather 等齐", "asyncio.gather(" not in _stream_code)
for event in ("start", "site", "done"):
    check(f"流式事件含 {event}", f'"type": "{event}"' in _stream_body)
# 单站异常必须变成一条有名有姓的诊断，否则站点总数对不上（ADR-20）
check("流式搜索单站异常不拖垮整体", "except Exception as exc:" in _stream_body)
check("流式搜索保留单站上限", "per_site_cap" in _stream_body)
check("流式搜索保留全局上限", "global_cap" in _stream_body)
# 客户端断开后未完成任务会继续吃满超时预算，占住并发名额与站点配额
check("流式搜索收尾取消未完成任务",
      "task.cancel()" in _stream_body and "finally:" in _stream_body)
check("流式端点存在 /api/v1/search/stream", "/api/v1/search/stream" in paths)
check("流式响应用 NDJSON", 'media_type="application/x-ndjson"' in _search_api)
# 反代缓冲会攒够一块才转发，流式在用户那边退化成一次性出结果（部署面最常见的坑）
check("流式响应关闭反代缓冲", '"X-Accel-Buffering": "no"' in _search_api)
check("流式响应禁止转换与缓存", "no-cache, no-transform" in _search_api)
# 流一开始就没法改状态码，异常只能写进流里
check("流式异常写进流而不是抛 500", '{"type": "error"' in _search_api
      or '"type": "error"' in _search_api)

# ② Jackett 批量接入：手工拼 torznab 地址是「20 个站填 20 次」，不符合日常使用
check("Jackett 服务存在", pathlib.Path("app/services/jackett.py").exists())
_jk = pathlib.Path("app/services/jackett.py").read_text(encoding="utf-8")
check("Jackett 索引器清单路径", 'INDEXERS_PATH = "/api/v2.0/indexers"' in _jk)
# 落库地址**不能**带结尾 /api：TorznabIndexer._endpoint() 会自己补，
# 带上就拼成 /torznab/api/api → 404，站点看着配好了却永远 0 条（本轮实测踩到）
_torznab_fn = _jk.split("def torznab_url(")[1].split("def caps_url(")[0]
check("torznab_url 不带结尾 /api",
      'return f"{root}{INDEXERS_PATH}/{indexer_id}/results/torznab"' in _torznab_fn)
_caps_fn = _jk.split("def caps_url(")[1].split("def _as_bool(")[0]
check("caps_url 带 /api", 'torznab_url(base, indexer_id)}/api' in _caps_fn)
check("Jackett 落库地址复用 torznab_url",
      '"url": torznab_url(base_url, indexer_id)' in _jk)
# 探测用 t=caps 而不是真搜一次：caps 不消耗站点搜索配额，
# 也不会因「这词该站确实没有」误判成站点坏了（ADR-75 的反面教训）
check("Jackett 探测用 caps 不消耗搜索配额", '"t": "caps"' in _jk)
# 失败原因必须分三档，笼统报「获取失败」等于让用户瞎试
_listing = _jk.split("async def list_indexers(")[1].split("async def test_indexer(")[0]
check("连不上时提示 Docker 网络", "宿主机" in _listing and "127.0.0.1" in _listing)
check("Key 错时明确指出", "API Key 不正确" in _listing)
check("一个站都没配时指引 Add Indexer", "Add Indexer" in _listing)
# Prowlarr 不返回 configured 字段，缺失时必须默认当作已配置，不能把真实站点悄悄藏掉
check("configured 字段缺失时默认视为已配置",
      "if configured is not None and not _as_bool(configured):" in _listing)
_sites_src = pathlib.Path("app/api/routers/sites.py").read_text(encoding="utf-8")
for path in ("/api/v1/sites/jackett/indexers", "/api/v1/sites/jackett/import",
             "/api/v1/sites/jackett/test"):
    check(f"端点存在 {path}", path in paths)
# FastAPI 按注册顺序匹配：jackett 路由若排在 /{site_id} 之后，
# POST /sites/jackett/test 会被 /sites/{site_id}/test 先吃掉并按 int 解析 → 422
check("Jackett 路由注册在 /{site_id} 之前",
      _sites_src.index('@router.post("/jackett/test"')
      < _sites_src.index('@router.patch("/{site_id}"'))
# 一条撞重名不该让整批失败；已存在的更新地址与 Key，重装 Jackett 时不必先删再加
_import_body = _sites_src.split("async def jackett_import(")[1].split("async def jackett_test(")[0]
check("批量导入逐条落库不整批回滚", "skipped.append(" in _import_body)
check("已存在的站点更新地址与 Key",
      "dup.url = data[\"url\"]" in _import_body
      and "dup.api_key = data[\"api_key\"]" in _import_body)
check("站点名加 Jackett 前缀避免撞名", 'name_prefix: str = "Jackett"' in _jk)
check("Jackett 站点复用 torznab Provider", "TorznabIndexer.name" in _jk)

# ③ 前端：流式读取 + Jackett 导入向导
check("前端有 NDJSON 流式读取函数", "function apiStream(" in app_js)
_stream_js = app_js.split("function apiStream(")[1].split("const STATUS_MAP")[0]
# 半行必须留在 buffer 里等下一个 chunk，否则 JSON 会被截断解析失败
check("前端处理半行缓冲", "buffer = lines.pop();" in _stream_js)
check("前端单行解析失败不中断整流", "console.warn(" in _stream_js)
# 不支持 body 流时退回一次性读完再回放：体验退化但功能不坏
check("前端对不支持流的环境有退路", "response.body.getReader" in _stream_js)
check("前端流式可中止", "controller.abort()" in _stream_js)
check("搜索走流式端点", '"/search/stream"' in app_js)
# 换关键词不取消上一次，两次结果会交叉写进同一个列表
check("重新搜索先取消上一次", "searchState.stream" in app_js)
check("主动取消不当成故障", "AbortError" in app_js)
check("搜索中展示站点进度", "searchState.progress" in app_js
      and "搜索中 " in app_js)
# 还没搜完就说「没有匹配的资源」是错的
check("搜索中空表文案不误报无结果", "结果会陆续出现" in app_js)
check("前端有 Jackett 导入向导", "function jackettDialog(" in app_js)
check("站点页有接入 Jackett 入口", '"接入 Jackett"' in app_js)
check("Jackett 地址与 Key 记住", 'localStorage.setItem("cf_jackett_url"' in app_js)
_jk_js = app_js.split("function jackettDialog(")[1][:6000]
# 后端标 already_added，前端必须据此禁用勾选并打「已导入」标，
# 否则用户会反复导入同一批站（虽然后端幂等，但界面看不出来）
check("后端标注索引器是否已导入", 'item["already_added"] = item["id"] in existing' in _sites_src)
check("已导入的索引器不可重复勾选", "box.disabled = !!row.already_added;" in _jk_js)
check("已导入的索引器有标记", 'text: "已导入"' in _jk_js)
check("流式与 Jackett 有回归用例",
      pathlib.Path("tests/test_jackett_stream.py").exists())
_jk_tests = pathlib.Path("tests/test_jackett_stream.py").read_text(encoding="utf-8")
check("回归用例覆盖 torznab 地址不带 api", "class Test" in _jk_tests and "torznab" in _jk_tests)
check("回归用例覆盖流式事件顺序", "search_stream" in _jk_tests)
# 文档：站点接入指南必须把 Jackett 摆成首选路径，FAQ 要能自助排障
check("ADR-78 记录流式优先于聚合等待",
      "## ADR-78 ·" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
check("ADR-79 记录 Jackett 批量接入",
      "## ADR-79 ·" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
_site_doc = (docs_dir / "10-站点接入指南.md").read_text(encoding="utf-8")
check("站点指南含接入 Jackett 小节", "接入 Jackett" in _site_doc)
check("站点指南提示 Docker 里要填宿主机 IP", "宿主机" in _site_doc and "9117" in _site_doc)
_faq = (docs_dir / "15-常见问题.md").read_text(encoding="utf-8")
check("FAQ 解释结果为何逐步出现", "逐步出现" in _faq or "陆续出现" in _faq)
check("FAQ 解释 Jackett 导入的站搜不到", "Jackett" in _faq)
check("API 文档列出流式端点", "/search/stream" in API_DOC)
check("API 文档列出 Jackett 端点", "/sites/jackett/import" in API_DOC)
check("路线图含里程碑 M50", "里程碑 M50" in roadmap_all)

# ---- v1.18.0：多站点 RSS 追新（聚合流分流）+ 更新检测 ----
_rssd = pathlib.Path("app/core/rss_dialects.py")
check("RSS 方言层存在", _rssd.exists())
_rssd_src = _rssd.read_text(encoding="utf-8") if _rssd.exists() else ""
# Nyaa 的 enclosure 是空的，体积与做种数只在 nyaa: 命名空间里。
# 只读 enclosure 会让整站结果 size=0/seeders=0 —— 不报错、只是被整站滤光。
check("方言层提取 nyaa:size", "nyaa_size" in _rssd_src)
check("方言层提取 nyaa:seeders", "nyaa_seeders" in _rssd_src)
# dmhy 写成 <strong>Size</strong>: 456.7MB，不剥标签的正则匹配不上
check("方言层先剥 HTML 标签再抠体积", "_HTML_TAG" in _rssd_src)
# 镜像域名极多，只看 URL 会让镜像站全退化成 generic 然后继续静默丢字段
check("方言判定看 feed 自述优先于 URL",
      "feed_title, feed_link, url" in _rssd_src)
check("方言层有字段差异说明（界面要能解释为什么没有做种数）",
      "DIALECT_NOTES" in _rssd_src)
check("方言覆盖 mikan/nyaa/dmhy",
      all(k in _rssd_src for k in ('"mikan"', '"nyaa"', '"dmhy"')))

_rsssvc = pathlib.Path("app/services/rss_feeds.py")
check("RSS 追新服务存在", _rsssvc.exists())
_rsssvc_src = _rsssvc.read_text(encoding="utf-8") if _rsssvc.exists() else ""
# pubDate 不可靠（重发/修种刷新时间），按时间判增量会重复下载或漏下
check("RSS 增量用 guid 而非发布时间", "handled_guids" in _rsssvc_src)
check("RSS guid 有上限不会无限膨胀", "MAX_HANDLED" in _rsssvc_src)
# 同站多条 feed 并发拉取会被 429（Auto_Bangumi 踩过）
check("RSS 同站请求留间隔", "_per_host_delay" in _rsssvc_src)
check("RSS 首次拉取只记账不补历史", "skip_existing" in _rsssvc_src)
check("RSS 复用雷达的订阅匹配口径（避免两套规则漂移）",
      "radar_service.match_subscribe" in _rsssvc_src)

_models_src = pathlib.Path("app/db/models.py").read_text(encoding="utf-8")
check("rss_feeds 表存在", '__tablename__ = "rss_feeds"' in _models_src)
# 推断错的代价是把整站新番下回来，所以做成用户可配开关
check("RssFeed 有 aggregate 开关", "aggregate: Mapped[bool]" in _models_src)

_upd = pathlib.Path("app/services/update_check.py")
check("更新检测服务存在", _upd.exists())
_upd_src = _upd.read_text(encoding="utf-8") if _upd.exists() else ""
# 本仓库至今没有任何 Release，只按 Release 实现会永远回答"已是最新版本"
check("更新检测在无 Release 时读主干兜底", "_latest_from_branch" in _upd_src)
check("更新检测返回判定依据 source", '"source": "branch"' in _upd_src)
check("更新只做 --ff-only", '"--ff-only"' in _upd_src)
# 自动 merge 产生看不懂的冲突，reset --hard 直接丢用户改动
check("更新不 reset --hard / 不 merge",
      '"--hard"' not in _upd_src and '_git("merge"' not in _upd_src)
# 挂 docker.sock 等于把宿主机控制权交给本进程。
# 这里用 AST 而不是全文计数：模块 docstring 里**必须**能解释"为什么不碰它"，
# 计数法会把这段解释本身当成违规（实测踩到）。要禁的是代码里真去用它。
_upd_docstrings = set()
_upd_literals = []
if _upd_src:
    import ast as _ast

    _upd_tree = _ast.parse(_upd_src)
    for _node in _ast.walk(_upd_tree):
        if isinstance(_node, (_ast.Module, _ast.FunctionDef,
                              _ast.AsyncFunctionDef, _ast.ClassDef)):
            _doc = _ast.get_docstring(_node, clean=False)
            if _doc:
                _upd_docstrings.add(_doc)
        if isinstance(_node, _ast.Constant) and isinstance(_node.value, str):
            _upd_literals.append(_node.value)
_sock_in_code = [
    s for s in _upd_literals if "docker.sock" in s and s not in _upd_docstrings
]
check("自更新不碰 docker.sock（仅文档里解释原因）",
      not _sock_in_code, str(_sock_in_code)[:80])
check("容器部署不假装能更新", 'mode == "source"' in _upd_src)

try:
    check("端点存在 /api/v1/rss-feeds", "/api/v1/rss-feeds" in paths)
    check("端点存在 /api/v1/rss-feeds/preview", "/api/v1/rss-feeds/preview" in paths)
    check("端点存在 /api/v1/rss-feeds/dialects", "/api/v1/rss-feeds/dialects" in paths)
    check("端点存在 /api/v1/rss-feeds/check-all", "/api/v1/rss-feeds/check-all" in paths)
    check("端点存在 /api/v1/rss-feeds/{feed_id}/check",
          "/api/v1/rss-feeds/{feed_id}/check" in paths)
    check("端点存在 /api/v1/system/update/check",
          "/api/v1/system/update/check" in paths)
    check("端点存在 /api/v1/system/update/apply",
          "/api/v1/system/update/apply" in paths)
except NameError:
    check("v1.18.0 端点校验（需先起服务）", False, "openapi 未取到")

check("前端有 RSS 追新页", '"rssfeeds"' in app_js and "pageRssFeeds" in app_js)
check("前端有检查更新入口", "checkUpdate" in app_js)
# 图标名写错不报错、只会静默画成占位点
check("前端注册 rss/eye 图标", "rss: '<path" in app_js and "eye: '<path" in app_js)
check("前端 RSS 页给出常用地址样例", "RSS_SAMPLES" in app_js)
check("前端标注方言字段差异", "RSS_DIALECT_LABEL" in app_js)

check("回归用例文件存在", pathlib.Path("tests/test_rss_feeds.py").exists())
_chg118 = (docs_dir / "08-变更日志.md").read_text(encoding="utf-8")
check("变更日志含 v1.18.0", "## v1.18.0" in _chg118)
check("ADR-80 记录 RSS 方言层",
      "## ADR-80 ·" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
check("ADR-81 记录更新检测取舍",
      "## ADR-81 ·" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
check("路线图含里程碑 M51", "里程碑 M51" in roadmap_all)
check("功能文档含 RSS 追新章节", "RSS 追新" in (docs_dir / "12-功能特性详解.md").read_text(encoding="utf-8"))
check("功能文档说明聚合流取舍", "聚合流" in (docs_dir / "12-功能特性详解.md").read_text(encoding="utf-8"))
check("站点指南区分搜索型 RSS 与追新型 RSS", "RSS 追新" in _site_doc)
check("FAQ 解释 RSS 加了没反应", "首次拉取" in _faq)
check("FAQ 解释 Docker 怎么更新", "docker compose pull" in _faq)
check("API 文档列出 RSS 端点", "/rss-feeds/preview" in API_DOC)
check("API 文档列出更新检测端点", "/system/update/check" in API_DOC)
check("README 致谢 Auto_Bangumi", "Auto_Bangumi" in README_ONLY)

print()
print("=" * 60)
failed = [c for c in checks if not c[0]]
print(f"README 事实校验：{len(checks) - len(failed)}/{len(checks)} 通过")
for _, name, detail in failed:
    print(f"  FAIL {name} {detail}")
sys.exit(1 if failed else 0)
