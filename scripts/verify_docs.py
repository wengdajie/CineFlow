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
check("Provider 总数 37", len(providers) == 37, f"实际 {len(providers)}")
check("README 声明 35 个 Provider", "35 个注册 Provider" in README)
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

check("20 张表", len(Base.metadata.tables) == 20, f"实际 {len(Base.metadata.tables)}")
check("README 声明 20 张表", "20 张表" in README)
check("ranking_rules 表已随榜单订阅下线（ADR-96）",
      "ranking_rules" not in Base.metadata.tables)
for table in ("audit_logs", "pan_saves", "pan_subscribes", "strm_records",
              "site_health", "filter_rule_groups"):
    check(f"新增表 {table} 存在", table in Base.metadata.tables)

# ---- 默认站点 ----
from app.db.init_db import DEFAULT_SITES  # noqa: E402
from app.services import presets as presets_service  # noqa: E402

check("默认示例站点 22 条", len(DEFAULT_SITES) == 22, f"实际 {len(DEFAULT_SITES)}")
check("文档声明 22 条示例站点", "写入 22 条" in README)
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
    "Torrents-CSV 公开搜索",
    "AnimeTosho 动漫",
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
check("内置调度任务 14 个", len(job_ids) == 14, str(job_ids))
check("README 声明 14 内置任务", "14 内置任务" in README)
for job in ("cineflow.radar", "cineflow.pan_transfer", "cineflow.pan_subscribe",
            "cineflow.pan_keepalive",
            # cineflow.scrape / cineflow.ranking 已下线（v1.24.0，ADR-96）
            "cineflow.strm_sync", "cineflow.upgrade",
            "cineflow.site_health", "cineflow.zhuiju_sync"):
    check(f"任务已注册 {job}", job in job_ids)

# ---- API 端点 ----
#: 期望端点数。docs/07「门禁数字的来源」那段散文会与这个值逐一比对（ADR-98），
#: 所以它必须是个变量而不是散落在字面量里 —— 否则两处会各自漂移。
total_expected = 181
try:
    with urllib.request.urlopen(f"{BASE}/openapi.json", timeout=15) as r:
        spec = json.loads(r.read().decode())
    total = sum(
        1
        for _, methods in spec["paths"].items()
        for m in methods
        if m.lower() in ("get", "post", "patch", "delete", "put")
    )
    check(f"API 端点 {total_expected} 个", total == total_expected, f"实际 {total}")
    check("README 声明 181 个端点", "181 个端点" in README and "共 181 个" in README)
    paths = set(spec["paths"])
    for path in ("/api/v1/users", "/api/v1/users/{user_id}",
                 "/api/v1/system/settings", "/api/v1/system/settings/reset",
                 "/api/v1/site-health", "/api/v1/site-health/records",
                 "/api/v1/site-health/check", "/api/v1/site-health/check/{site_id}",
                 # v1.24.0：/ranking-rules 全组下线，换成媒体库本地文件操作
                 "/api/v1/library/browse", "/api/v1/library/rename",
                 "/api/v1/library/move", "/api/v1/library/delete",
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
check("router 24 个", router_count == 24, f"实际 {router_count}")
check("README 声明 24 个 router", "24 个 router" in README)
for name in ("trending", "schedules", "pan", "chatops", "strm", "pan_subscribes",
             "users", "site_health", "rule_groups"):
    check(f"router {name} 已挂载", f"{name}.router" in router_src)

# ---- 前端页面 ----
app_js = pathlib.Path("web/assets/app.js").read_text(encoding="utf-8")
pages_block = app_js[app_js.index("const PAGES"): app_js.index("];", app_js.index("const PAGES"))]
page_count = pages_block.count("{ key:")
check("前端页面 22 个", page_count == 22, f"实际 {page_count}")
check("README 声明点检 22 个页面", "22 个页面" in README)
check("scripts/README 声明 22 个页面", "22 个前端页面" in SCRIPTS_README)
check("前端含热度排行页", '"trending"' in app_js and "pageTrending" in app_js)
check("前端含定时任务页", '"schedules"' in app_js and "pageSchedules" in app_js)
check("README 声明 22 个功能页", "22 个功能页" in README)
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
check("测试文件 59 个", len(test_files) == 59, str(test_files))
check("README 声明 59 个测试文件", "59 个测试文件" in README)
for name in ("test_custom_sites.py", "test_radar.py", "test_trending.py",
             # test_nfo / test_scraper / test_ranking 已随功能下线（ADR-96）
             "test_panstorage.py", "test_chatops.py", "test_library_ops.py",
             "test_webdav.py", "test_strm_sync.py",
             "test_pan_subscribe.py", "test_upgrade.py", "test_categories.py",
             "test_config_store.py", "test_rules.py", "test_site_health.py",
             "test_rule_groups.py", "test_users.py"):
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
# ---- v1.22.0 aria2 磁力 GID 跟随（ADR-91）----
_aria2_src = pathlib.Path("app/providers/downloader/aria2.py").read_text(encoding="utf-8")
check("aria2 会跟随 followedBy 到数据 GID", "followedBy" in _aria2_src and "_follow" in _aria2_src)
_dl_tests = pathlib.Path("tests/test_downloaders.py").read_text(encoding="utf-8")
check("aria2 磁力 GID 跟随有回归用例", "test_aria2_magnet_get_follows_to_data_gid" in _dl_tests)
check("README 版本号 1.27.0", "1.27.0" in README_ONLY)
version_src = pathlib.Path("app/core/version.py").read_text(encoding="utf-8")
check("代码版本号为 1.27.0", 'APP_VERSION = "1.27.0"' in version_src)
# pyproject 的版本号此前漂移了 25 个版本（1.1.0 vs 1.26.0）——
# 因为门禁只校验 README 与 version.py，没人看打包元数据。
# 这条断言让「三处版本号」必须同源。
_pyproject = pathlib.Path("pyproject.toml").read_text(encoding="utf-8")
check("pyproject 版本号与代码一致", 'version = "1.27.0"' in _pyproject)
check("README 声明 314 项接口用例", "314 项真实 HTTP 接口用例" in README)
check("scripts/README 声明 321 项", "321 项接口用例" in SCRIPTS_README)

# ---- 服务模块 ----
# scraper / ranking 已删除（ADR-96），library 顶上来负责本地文件操作
for module in ("radar", "discovery", "presets", "trending", "settings_store",
               "pan_storage", "library", "strm_sync", "pan_subscribe", "upgrade",
               "config_store", "site_health", "rule_groups"):
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
check("ADR-91 记录磁力元数据 GID 陷阱",
      "ADR-91" in adr_text and "followedBy" in adr_text)
check("ADR-92 记录 qB 204 登录", "ADR-92" in adr_text and "204" in adr_text)
check("ADR-93 记录百度分享正则放宽", "ADR-93" in adr_text)
check("ADR-94 记录 400 不等于网络问题", "ADR-94" in adr_text)
check("ADR-95 记录百度开放平台授权", "ADR-95" in adr_text and "refresh_token" in adr_text)
_bo = pathlib.Path("app/services/panlogin/baidu_openapi.py").read_text(encoding="utf-8")
check("百度开放平台走设备码授权", "device_token" in _bo and "authorization_pending" in _bo)
check("百度开放平台声明不支持转存", "转存" in _bo and "互补" in _bo)
_qb = pathlib.Path("app/providers/downloader/qbittorrent.py").read_text(encoding="utf-8")
check("qB 接受 204 登录", "(200, 204)" in _qb)
_bd = pathlib.Path("app/providers/panstorage/baidu.py").read_text(encoding="utf-8")
check("百度记录 HTTP 状态码以区分登录态", "_last_http_status" in _bd)
check("百度分享正则支持 yun/eyun", "yun|eyun" in _bd)
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

# ---- 刮削已下线（原 M5，v1.24.0 移除，ADR-96） ----
# 原来这里有 20 条「刮削功能存在」的断言。用户明确划界「项目不负责刮削」后
# 整块反转成「确实已经拆干净」—— 删功能最容易留下半截：模块删了配置还在、
# 端点删了前端按钮还在，那种状态比不删更糟。
check("core/nfo.py 已删除", not pathlib.Path("app/core/nfo.py").exists())
check("services/scraper.py 已删除", not pathlib.Path("app/services/scraper.py").exists())
check("test_nfo.py 已删除", not pathlib.Path("tests/test_nfo.py").exists())
check("test_scraper.py 已删除", not pathlib.Path("tests/test_scraper.py").exists())
for key in ("SCRAPE_ENABLED", "SCRAPE_IMAGES", "SCRAPE_OVERWRITE", "SCRAPE_CRON",
            "SCRAPE_BATCH"):
    check(f"配置项 {key} 已移除", not hasattr(settings, key))
    check(f"README 不再提 CF_{key}", f"CF_{key}" not in README)
library_router_src = pathlib.Path("app/api/routers/library.py").read_text(encoding="utf-8")
check("补刮端点已摘除", "/scrape" not in library_router_src)
check("前端无补刮按钮", "补刮 NFO" not in app_js and "/library/scrape" not in app_js)
check("刮削任务已从调度移除",
      "JOB_SCRAPE" not in pathlib.Path("app/services/scheduler.py").read_text(encoding="utf-8"))
check("ScrapeRequest schema 已移除",
      "ScrapeRequest" not in pathlib.Path("app/schemas/models.py").read_text(encoding="utf-8"))
# 未被推翻的部分：改名引擎不是刮削，必须还在
check("整理改名引擎保留（organizer + meta 不属于刮削）",
      pathlib.Path("app/core/organizer.py").exists()
      and "def render_name" in pathlib.Path("app/core/organizer.py").read_text(encoding="utf-8"))

# ---- 媒体库本地文件操作（M54，v1.24.0） ----
# 换掉刮削后，媒体库页要能干真正被需要的活：看本地下载 + 改名/移动/删除。
library_svc = pathlib.Path("app/services/library.py").read_text(encoding="utf-8")
for func in ("library_roots", "resolve_library_path", "browse_library",
             "rename_entry", "move_entry", "delete_entry"):
    check(f"媒体库文件操作函数 {func}", f"def {func}" in library_svc)
for route in ("/browse", "/rename", "/move", "/delete"):
    check(f"媒体库端点 {route} 已挂载", f'"{route}"' in library_router_src)
check("路径操作限定在媒体库与下载目录之内（挡穿越）",
      "LIBRARY_DIR" in library_svc and "DOWNLOAD_DIR" in library_svc
      and "超出允许范围" in library_svc)
check("新名称拒绝路径分隔符", "_INVALID_NAME_CHARS" in library_svc)
check("删非空目录需显式递归确认", "recursive" in library_svc and "非空" in library_svc)
check("不允许删根目录", "不允许删除根目录" in library_svc)
check("路径变动后同步 library_files 索引",
      "def _reindex_paths" in library_svc and "def _forget_paths" in library_svc)
check("跨盘移动用 shutil.move（媒体库与下载盘常不同盘）", "shutil.move" in library_svc)
check("前端含本地文件浏览器", "libraryBrowser" in app_js and "libraryCwd" in app_js)
check("前端浏览目录直接读盘而非读索引", "/library/browse" in app_js)
check("媒体库文件操作有独立测试", pathlib.Path("tests/test_library_ops.py").exists())
# 兄弟目录同前缀边界（K:\mediaX vs K:\media）：ADR-96 点名了这条，就必须真有测试钉住。
# 注入验证实测：去掉 `+ os.sep` 后原有越界用例**全绿**，是这条补上才转红的。
_lib_tests = pathlib.Path("tests/test_library_ops.py").read_text(encoding="utf-8")
check("同前缀兄弟目录被判为库外", "test_is_within_requires_separator_boundary" in _lib_tests
      and "test_sibling_dir_with_shared_prefix_is_outside" in _lib_tests)
# scan 清僵尸行：换 LIBRARY_DIR 后旧库那行文件已不存在、又在允许范围外，
# delete_entry 清不掉（正确），扫描是唯一入口 —— 不清就成了污染统计的静默错。
check("扫描会清理失效索引行", '"removed"' in library_svc and "文件已不存在" in library_svc)
check("失效索引清理有测试", "test_scan_removes_index_rows_whose_file_is_gone" in _lib_tests
      and "test_scan_removes_rows_outside_allowed_roots_when_file_gone" in _lib_tests)
# RETIRED 白名单能让文档检查跳过某路径，所以必须有反向担保证明"跳过是因为它真没了"
_doc_ep = pathlib.Path("tests/test_doc_endpoints.py").read_text(encoding="utf-8")
check("已下线端点白名单有反向担保", "RETIRED" in _doc_ep
      and "test_retired_endpoints_are_really_gone" in _doc_ep)
check("媒体库文件操作测试自带建表（可单文件跑）", "def _tables" in _lib_tests)

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
check("可编辑配置项 63 个", len(config_store.EDITABLE) == 63, f"实际 {len(config_store.EDITABLE)}")
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

# ---- 榜单自动订阅已下线（原 M15，v1.24.0 移除，ADR-96） ----
# 用户要求「去掉榜单订阅」。这块原有 22 条断言，同样整体反转成「拆干净了」。
check("services/ranking.py 已删除", not pathlib.Path("app/services/ranking.py").exists())
check("routers/ranking.py 已删除", not pathlib.Path("app/api/routers/ranking.py").exists())
check("test_ranking.py 已删除", not pathlib.Path("tests/test_ranking.py").exists())
for key in ("RANKING_INTERVAL_MINUTES", "RANKING_MAX_PER_RUN"):
    check(f"配置项 {key} 已移除", not hasattr(settings, key))
    check(f"README 不再提 CF_{key}", f"CF_{key}" not in README)
check("ranking 路由未注册",
      "ranking" not in pathlib.Path("app/api/router.py").read_text(encoding="utf-8"))
check("RankingRule 表已移除",
      "class RankingRule" not in pathlib.Path("app/db/models.py").read_text(encoding="utf-8"))
check("前端无榜单订阅页", "pageRanking" not in app_js and "ranking-rules" not in app_js)
# 未被推翻的部分（ADR-43 结论仍成立）：本地资源热榜喂搜索/发现页，是另一件事
check("本地资源热榜保留", pathlib.Path("app/services/trending.py").exists()
      and pathlib.Path("app/api/routers/trending.py").exists())

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
check("设置页分组 17 组", len(SETTING_GROUPS) == 17, f"实际 {len(SETTING_GROUPS)}")

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

check("变更日志含 v1.20.0", "## v1.20.0" in _chg118)
check("ADR-84 记录公开源收录判据",
      "## ADR-84 ·" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
check("ADR-85 记录别名双用",
      "## ADR-85 ·" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
check("ADR-86 记录零配置默认启用",
      "## ADR-86 ·" in (docs_dir / "04-决策记录.md").read_text(encoding="utf-8"))
check("路线图含里程碑 M52", "里程碑 M52" in roadmap_all)
check("竞品文档含 v1.20.0 对标结论",
      "v1.20.0 对标结论" in (docs_dir / "09-竞品对标与差距分析.md").read_text(encoding="utf-8"))
check("12.2 提及 Prowlarr", "Prowlarr" in (docs_dir / "09-竞品对标与差距分析.md").read_text(encoding="utf-8"))
check("12.3 提及跨语言标题匹配差异化",
      "跨语言标题匹配" in (docs_dir / "09-竞品对标与差距分析.md").read_text(encoding="utf-8"))
check("新 Provider 文件存在", pathlib.Path("app/providers/indexer/torrents_csv.py").exists())
check("新 Provider 文件存在", pathlib.Path("app/providers/indexer/animetosho.py").exists())
check("别名模块存在", pathlib.Path("app/services/title_alias.py").exists())
check("BT 覆盖测试文件存在", pathlib.Path("tests/test_bt_coverage.py").exists())


# ---- v1.21.0：发现即可接入 / 落地目录记忆 / 百度病因分流 ----
_adr = (docs_dir / "04-决策记录.md").read_text(encoding="utf-8")
check("变更日志含 v1.21.0", "## v1.21.0" in _chg118)
check("ADR-87 记录发现即可接入", "## ADR-87 ·" in _adr)
check("ADR-88 记录交互式探测收窄", "## ADR-88 ·" in _adr)
check("ADR-89 记录落地目录服务端记忆", "## ADR-89 ·" in _adr)
check("ADR-90 记录百度 bdstoken 病因分流", "## ADR-90 ·" in _adr)
check("路线图含里程碑 M53", "里程碑 M53" in roadmap_all)

_disc_src = pathlib.Path("app/services/discovery.py").read_text(encoding="utf-8")
check("发现候选可探测", "async def probe_discovered" in _disc_src)
check("发现候选可落库", "def suggest_from_probe" in _disc_src)
# 交互式探测必须收窄：照搬定时任务参数曾把请求拖到 175s（ADR-88）
check("交互式探测只用 1 个关键词", "INTERACTIVE_KEYWORDS" in _disc_src)
check("交互式探测有墙钟预算",
      "PROBE_BUDGET_SECONDS = " in _disc_src
      and "budget: float = PROBE_BUDGET_SECONDS" in _disc_src
      and "timeout=budget" in _disc_src)
check("探测超时报 unknown 而非 blocked",
      '"probe": "unknown"' in _disc_src and "asyncio.wait_for" in _disc_src)
# 只放行 searchable，否则等于把搜不到的站塞进搜索链路（ADR-70）
check("落库只放行 searchable", 'if str(probe.get("probe")) != "searchable"' in _disc_src)

_pan_src = pathlib.Path("app/services/pan_storage.py").read_text(encoding="utf-8")
check("落地目录会被记住", "def remember_target_dir" in _pan_src)
check("落地目录有建议值", "def suggested_target_dir" in _pan_src)
check("建议目录下发给前端", '"suggested_target_dir": suggested_target_dir(storage)' in _pan_src)
# 失败的目录不该被记住，否则下次默认值是必然失败的值（ADR-89）
check("只在转存成功后回写记忆", "if result.success:\n        remember_target_dir" in _pan_src)

_bd_src = pathlib.Path("app/providers/panstorage/baidu.py").read_text(encoding="utf-8")
check("百度按病因分流提示", "def _auth_hint" in _bd_src)
check("百度 bdstoken 有 HTML 兜底", "async def _token_from_html" in _bd_src)
check("百度留下 errno 供上层判别", "_last_errno" in _bd_src)

check("发现接入回归用例存在", pathlib.Path("tests/test_site_add_and_dirs.py").exists())
check("前端发现列表可直接添加",
      'api("/sites/discover/apply"' in app_js)

check("README 致谢 Auto_Bangumi", "Auto_Bangumi" in README_ONLY)


# ---- v1.24.0 / M55：登录通道与存储通道必须成对（ADR-97）----
check("ADR-97 记录登录/存储成对不变式", "## ADR-97 ·" in _adr)
check("路线图含里程碑 M55", "里程碑 M55" in roadmap_all)
check("变更日志含 v1.24.0", "## v1.24.0" in _chg118)

# 僵尸站点：v1.16.0 给 baidu 修过、v1.23.0 给 baidu_openapi 又犯一遍。
# 这里断言的是**行为**（造得出实例），不是文件在不在 —— 文件存在但没
# @register / 没进 load_builtin_providers 清单 / name 写错，都一样是僵尸站点。
_bo_store = pathlib.Path("app/providers/panstorage/baidu_openapi.py")
check("百度开放平台存储 Provider 存在", _bo_store.exists())
check("baidu_openapi 已注册（否则授权成功却建出僵尸站点）",
      "baidu_openapi" in names)
# 不变式本体：任何能登录的网盘都必须有存储实现
from app.services.pan_login import PROVIDERS as _LOGIN_PROVIDERS  # noqa: E402

_orphans = [n for n in _LOGIN_PROVIDERS if n not in names]
check("每个登录通道都有对应存储 Provider", not _orphans, f"缺失 {_orphans}")

_bo_src = _bo_store.read_text(encoding="utf-8")
# 开放平台没有 /share/transfer，如实声明比假装支持重要
check("开放平台如实声明不支持转存", "supports_save = False" in _bo_src)
check("拒绝转存时给出替代路径（ADR-82：拒绝要给下一步）",
      "另外用 Cookie 方式添加" in _bo_src)
# access_token 只有 30 天；只刷内存不落库等于每次调用都刷一遍
check("access_token 到期自动刷新", "async def _refresh_token" in _bo_src
      and "TOKEN_INVALID_ERRNOS" in _bo_src)
check("刷新后回写站点（否则重启又用回旧 token）",
      "def _store_tokens" in _bo_src and "site.api_key = access" in _bo_src)
check("刷新有一次性闸门（防吊销 token 打死循环）", "_refreshed_once" in _bo_src)
# 能力位声明了不支持 Cookie，就必须在服务层真的挡住
_pl_src = pathlib.Path("app/services/pan_login.py").read_text(encoding="utf-8")
check("apply_cookie 按能力位拒绝", 'if not meta["cookie"]:' in _pl_src)

_bo_tests = pathlib.Path("tests/test_baidu_openapi_storage.py")
check("百度开放平台存储有独立测试", _bo_tests.exists())
_bo_t = _bo_tests.read_text(encoding="utf-8")
check("不变式有测试钉住",
      "test_every_login_provider_has_a_storage_provider" in _bo_t)
# 第一版不变式写在测试进程内，被文件顶部的 import 自己满足了 —— 注入验证
# 删掉 load_builtin_providers 清单那行时**仍然全绿**。必须隔离进程。
check("不变式在干净子进程内验证（否则测的是自己的 import 副作用）",
      "_REGISTRY_INVARIANT_SCRIPT" in _bo_t and "subprocess.run" in _bo_t)
# 守卫类断言要搭配"会成功的后端"，否则去掉守卫也会因为网络失败而假绿
check("守卫用例证明「一个请求都没发」",
      "_counting_ok" in _bo_t and "assert calls == []" in _bo_t)
check("守卫有反向担保（正常删除仍能到后端）",
      "test_delete_normal_path_does_reach_backend" in _bo_t)
# 服务层不能把 Provider 写的替代路径丢掉（live 实测查出）
_ps_src = pathlib.Path("app/services/pan_storage.py").read_text(encoding="utf-8")
check("拒绝转存时带出 Provider 的理由",
      "detail = await storage.save_share(" in _ps_src
      and "detail.message or" in _ps_src)
check("拒绝理由有测试钉住",
      "test_service_layer_keeps_provider_refusal_reason" in _bo_t)


# ---- v1.24.0 / M56：半截删除 + 没人校验的门禁清单（ADR-98 / ADR-99）----
check("ADR-98 记录半截删除与门禁散文（v1.24.0）", "## ADR-98 ·" in _adr)
check("ADR-99 记录遍历双闸门（v1.24.0）", "## ADR-99 ·" in _adr)
check("路线图含里程碑 M56", "里程碑 M56" in roadmap_all)

# 空壳分组：功能删了、标题留着 → 用户点进去是空白
_sys_src = pathlib.Path("app/api/routers/system.py").read_text(encoding="utf-8")
_group_titles = {g["title"] for g in SETTING_GROUPS}
check("设置分组不再含已下线的榜单订阅", "榜单自动订阅" not in _group_titles)
check("设置分组不再含已下线的刮削", "刮削与分类" not in _group_titles)
check("设置分组含替代它们的分类归档", "分类归档" in _group_titles)
check("渲染层过滤空壳分组（漏删时用户看不到）", "if not items:" in _sys_src)
check("空壳分组会打警告（运维能发现漏删）",
      "logger.warning" in _sys_src and "get_logger" in _sys_src)
_cfg_t = pathlib.Path("tests/test_config_store.py").read_text(encoding="utf-8")
check("悬空键有门禁（既有断言的逆命题）",
      "test_setting_groups_have_no_dangling_keys" in _cfg_t)
check("空壳分组有门禁", "test_no_empty_setting_group_is_exposed" in _cfg_t)
check("已下线分组有反向断言", "test_retired_setting_groups_are_gone" in _cfg_t)
# 删功能时断言要反转而不是删掉
_api_t = pathlib.Path("tests/test_api.py").read_text(encoding="utf-8")
check("旧分组断言已反转而非删除",
      '"刮削与分类" not in titles' in _api_t and '"榜单自动订阅" not in titles' in _api_t)

# 全盘遍历闸门：深度限制防目录环，防不住"盘本来就大"
_strm_src = pathlib.Path("app/services/strm_sync.py").read_text(encoding="utf-8")
check("遍历有墙钟预算（压在网关超时之下）", "WALK_BUDGET_SECONDS" in _strm_src)
check("遍历有目录数上限", "WALK_MAX_DIRS" in _strm_src)
check("截断能与「跑完了」区分开", "truncated" in _strm_src)
check("stats 初始化就带 truncated（早退路径不能给调用方 KeyError）",
      '"truncated": False,' in _strm_src)
check("截断时不再说「没有找到视频文件」",
      "遍历达到上限提前收工" in _strm_src)
check("截断冒泡到顶层 message", 'summary["truncated"]' in _strm_src)
_strm_t = pathlib.Path("tests/test_strm_sync.py").read_text(encoding="utf-8")
for _t in ("test_walk_stops_at_dir_budget_and_reports_truncation",
           "test_walk_stops_at_time_budget",
           "test_truncated_sync_does_not_claim_no_videos",
           "test_sync_all_surfaces_truncation_in_top_level_message"):
    check(f"遍历闸门用例 {_t}", _t in _strm_t)
check("闸门有反向担保（小盘不被误标截断）",
      "test_small_pan_is_not_marked_truncated" in _strm_t)

# 只给"最像瓶颈的那一步"设限是不够的：实测元数据阶段（359s）比遍历（90s）还慢
check("元数据阶段有独立闸门", "METADATA_BUDGET_SECONDS" in _strm_src
      and "METADATA_MAX_FILES" in _strm_src)
check("单盘同步有覆盖全部阶段的总预算", "SYNC_STORAGE_BUDGET_SECONDS" in _strm_src)
check("元数据用剩余预算而非独立预算",
      "deadline - time.monotonic()" in _strm_src)
check("元数据被砍会回报（返回二元组）", "return count, truncated" in _strm_src)
# 慢盘曾把总预算吃干，让实测 0.0s 就能扫完的本地盘永远轮不到
check("配额在盘之间公平分配", "remaining / max(1, len(targets) - index)" in _strm_src)
# 「一个目录都没看过」与「只扫了一部分」是两种结果，混起来会重复计数
check("区分「没轮到扫」与「只扫了一部分」", '"not_scanned": True' in _strm_src)
check("顶层截断含没轮到的盘",
      'bool(truncated_names) or bool(skipped_for_budget)' in _strm_src)
check("截断消息用站点名而非 ID（ID 无法据此行动）",
      'item.get("site_name") or item.get("site_id")' in _strm_src)
# 本轮最严重的缺陷：截断时清理把上一轮生成的 636 个 STRM 全删了
check("截断时跳过失效清理（否则反复删建）",
      "if clean and not dry_run and not truncated:" in _strm_src)
for _t in ("test_metadata_download_stops_at_time_budget",
           "test_metadata_download_stops_at_file_count",
           "test_sync_storage_budget_covers_metadata_phase",
           "test_metadata_truncation_marks_sync_as_truncated",
           "test_slow_storage_does_not_starve_later_storages",
           "test_truncated_sync_does_not_clean_unseen_records"):
    check(f"闸门用例 {_t}", _t in _strm_t)
# 修复不能变成"再也不清理"
check("清理有反向担保（完整同步仍会清失效）",
      "test_complete_sync_still_cleans_disappeared_records" in _strm_t)
check("元数据闸门有反向担保（正常数量不被误标）",
      "test_small_metadata_set_is_not_marked_truncated" in _strm_t)
check("ADR-99 记录分阶段实测数据", "255 个 × 1.4s" in _adr)
check("ADR-99 记录截断误删事故", "removed: 636" in _adr or "`removed: 636`" in _adr)

# smoke test 自身的可靠性：超时不能伪装成取值错误，且必须幂等
_smoke_src = pathlib.Path("scripts/smoke_test.py").read_text(encoding="utf-8")
check("smoke test 区分超时与取值错误（原先 45s 超时报成断言失败）",
      "except TimeoutError" in _smoke_src and "TIMEOUT after" in _smoke_src)
check("smoke test 跑前清残留（否则 409 会被误诊成别的问题）",
      "def purge_by_name" in _smoke_src)

# ---- v1.24.0 / M57：一次实机 BT 下载查出的整条入库链路（ADR-100）----
check("ADR-100 记录下载器主文件是启发式（v1.24.0）", "## ADR-100 ·" in _adr)
check("路线图含里程碑 M57", "里程碑 M57" in roadmap_all)
check("ADR-100 记录实测的 files[0] 文件序", "140 B" in _adr and "9 个 `.srt` 之后" in _adr)

# aria2 的 files 按 torrent 内文件序返回，files[0] 常常是字幕
_a2_src = pathlib.Path("app/providers/downloader/aria2.py").read_text(encoding="utf-8")
check("aria2 有主内容挑选函数（不再用 files[0]）", "def _content_path(" in _a2_src)
check("aria2 主内容按文件大小挑", "int(entry.get(\"length\") or 0)" in _a2_src)
check("aria2 多文件种子返回父目录（随行字幕一起入库）",
      "return parent or largest" in _a2_src)
check("aria2 单文件种子返回文件本身（返回目录会搂进别的任务）",
      "if len(sized) == 1:" in _a2_src)
check("aria2 不再直接用 files[0] 当主内容", "content_path=files[0]" not in _a2_src)
_dl_t = pathlib.Path("tests/test_downloaders.py").read_text(encoding="utf-8")
for _t in ("test_aria2_content_path_is_not_the_first_file",
           "test_aria2_content_path_ignores_leading_subtitle_run",
           "test_aria2_single_file_content_path_is_the_file_itself",
           "test_aria2_content_path_falls_back_to_dir_when_no_files"):
    check(f"aria2 主内容用例 {_t}", _t in _dl_t)

# 入库静默 no-op：{"tasks":0,...} 曾同时表示"没活干"和"全跳过了"（ADR-82 复现）
_lib_src = pathlib.Path("app/services/library.py").read_text(encoding="utf-8")
check("入库统计有 skipped 计数", '"skipped": 0' in _lib_src)
check("入库跳过有明细", "skipped_detail" in _lib_src)
check("入库跳过会推 WARNING 通知（不能只写日志）",
      "NotifyLevel.WARNING.value" in _lib_src)
check("content_path 是非媒体单文件时回退到目录",
      "if not results and source.is_file():" in _lib_src)
_lib_t = pathlib.Path("tests/test_library_ops.py").read_text(encoding="utf-8")
for _t in ("test_transfer_falls_back_to_directory_when_content_path_is_subtitle",
           "test_transfer_counts_skipped_tasks_instead_of_silent_log",
           "test_transfer_applies_task_year_on_direct_path"):
    check(f"入库链路用例 {_t}", _t in _lib_t)

# 命名不许编造信息：逐段清洗只处理空字段，字面量会绕过它
_org_src = pathlib.Path("app/core/organizer.py").read_text(encoding="utf-8")
check("缺画质时留空串而非字面量 unknown",
      '"quality": " ".join(quality_parts),' in _org_src
      and '"quality": " ".join(quality_parts) or "unknown"' not in _org_src)
# 任务标题带年份、种子内文件名不带；Emby 靠 `片名 (年份)` 消歧义
check("transfer_directory 接受外部年份", "year: int | None = None," in _org_src)
check("外部年份不覆盖文件名自带的年份（后者更具体）",
      "if year is not None and info.year is None:" in _org_src)
_lib_year = _lib_src.count("year=info.year")
check("library 两个调用点都把任务标题年份传下去（一处传漏 = 一个假绿）",
      _lib_year == 2, f"实际 {_lib_year} 处")
_org_t = pathlib.Path("tests/test_organizer.py").read_text(encoding="utf-8")
check("命名不编造 unknown 有用例",
      "test_missing_quality_does_not_produce_literal_unknown" in _org_t
      and "test_real_quality_is_still_kept_in_name" in _org_t)
check("外部年份有反向担保（不覆盖文件名年份）",
      "test_external_year_does_not_override_filename_year" in _org_t
      and "test_transfer_directory_applies_external_year" in _org_t)

# ---- v1.25.0 / M58：网盘直链的 UA 是鉴权的一部分（ADR-101）----
check("ADR-101 记录直链 UA 与前端断链（v1.25.0）", "## ADR-101 ·" in _adr)
check("路线图含里程碑 M58", "里程碑 M58 ·" in roadmap_all)
check("ADR-101 记录参考脚本的 50MB/403 实证", "50MB" in _adr and "403" in _adr)
check("ADR-101 明确不做分享链接逆向（与 ADR-34 一致）",
      "sharedownload" in _adr and "ADR-34" in _adr)

# 直链的 UA 必须能一路传到真正发 HTTP 请求的那一方
_dlbase = pathlib.Path("app/providers/downloader/base.py").read_text(encoding="utf-8")
check("下载器抽象接受 user_agent", "user_agent: str | None = None," in _dlbase)
check("base 说明 UA 是下载这条链接要用的 UA", "不是下载器自己的 UA" in _dlbase)
_a2 = pathlib.Path("app/providers/downloader/aria2.py").read_text(encoding="utf-8")
check("aria2 写 user-agent 选项（--user-agent 才真正生效）",
      'options["user-agent"] = user_agent' in _a2)
check("aria2 的 header 是列表，Cookie 与 UA 并存",
      'headers.append(f"Cookie: {cookie}")' in _a2
      and 'headers.append(f"User-Agent: {user_agent}")' in _a2)
# 每个下载器签名都要对齐（不用 **kwargs 兜，约定 9）
for _mod in ("qbittorrent", "transmission", "xunlei", "ytdlp"):
    _src = pathlib.Path(f"app/providers/downloader/{_mod}.py").read_text(encoding="utf-8")
    check(f"{_mod} 的 add() 接 user_agent", "user_agent: str | None = None," in _src)

# UA 要求是网盘自己的鉴权规则 → 做成 Provider 属性
_psbase = pathlib.Path("app/providers/panstorage/base.py").read_text(encoding="utf-8")
check("网盘基类声明 direct_link_user_agent",
      "direct_link_user_agent: str | None = None" in _psbase)
_bd = pathlib.Path("app/providers/panstorage/baidu.py").read_text(encoding="utf-8")
_bdo = pathlib.Path("app/providers/panstorage/baidu_openapi.py").read_text(encoding="utf-8")
check("百度（网页）声明直链 UA", "direct_link_user_agent = NETDISK_UA" in _bd)
check("百度（开放平台）声明直链 UA", "direct_link_user_agent = NETDISK_UA" in _bdo)
_ps = pathlib.Path("app/services/pan_storage.py").read_text(encoding="utf-8")
check("换直链一起返回 UA 要求",
      '"user_agent": storage.direct_link_user_agent or None,' in _ps)
check("转存结果带 site_id（否则换直链无从下手）",
      '"site_id": storage.config.get("id") or site_id,' in _ps)

# 缺失的第三段：转存 → 换直链 → 投下载器
_dls = pathlib.Path("app/services/download.py").read_text(encoding="utf-8")
check("存在网盘落地本地的实现", "async def _land_pan_to_local(" in _dls)
check("落地被真正接进 add_download", "landed = await _land_pan_to_local(" in _dls)
check("落地时把 UA 一起投给下载器",
      "url, save_path=save_path, user_agent=user_agent" in _dls)
check("没有下载器时算跳过而不是失败", '{"skipped": True}' in _dls)
check("落地失败保持 transferred 但写明原因", "云端确实存下了" in _dls)

# 前端：后端有能力 ≠ 用户能用到
_appjs = pathlib.Path("web/assets/app.js").read_text(encoding="utf-8")
check("前端读后端的 needs_app_credentials", "needs_app_credentials" in _appjs)
check("前端有 SecretKey 输入", "client_secret" in _appjs)
check("前端记住 AppKey", "cf_baidu_appkey" in _appjs)
check("前端不持久化 SecretKey",
      'setItem("cf_baidu_secret"' not in _appjs
      and "setItem(BAIDU_SECRET" not in _appjs)

_pl_t = pathlib.Path("tests/test_pan_login.py").read_text(encoding="utf-8")
for _t in ("test_frontend_sends_app_credentials_for_openapi_login",
           "test_frontend_does_not_persist_the_secret_key"):
    check(f"开放平台授权用例 {_t}", _t in _pl_t)
_dr_t = pathlib.Path("tests/test_download_routing.py").read_text(encoding="utf-8")
for _t in ("test_pan_landing_sends_direct_link_with_required_ua",
           "test_pan_landing_without_downloader_is_skipped_not_failed",
           "test_add_download_pan_actually_calls_landing",
           "test_save_share_payload_carries_site_id"):
    check(f"网盘落地用例 {_t}", _t in _dr_t)
_dl_t2 = pathlib.Path("tests/test_downloaders.py").read_text(encoding="utf-8")
for _t in ("test_aria2_add_passes_user_agent_for_pan_direct_link",
           "test_aria2_add_keeps_cookie_and_user_agent_together",
           "test_aria2_add_without_user_agent_sets_no_ua_option",
           "test_all_downloaders_accept_user_agent_param"):
    check(f"aria2 UA 用例 {_t}", _t in _dl_t2)

# ---- v1.26.0 / M59：BT 覆盖面的四个独立缺陷（ADR-102）----
_registry_src = pathlib.Path("app/providers/registry.py").read_text(encoding="utf-8")
_catalog_src = pathlib.Path("app/services/site_catalog.py").read_text(encoding="utf-8")

check("ADR-102 记录诊断口径与最终结果同源（v1.26.0）", "## ADR-102 ·" in _adr)
check("路线图有里程碑 M59", "里程碑 M59 ·" in roadmap_all)
check("ADR-102 写明 68 条全无关的实测证据", "68" in _adr and "1999" in _adr)
check("ADR-102 区分 empty/filtered/error 三种零结果",
      "filtered" in _adr and "empty" in _adr)

# 两个新磁力源：Provider、注册表、实测清单三处都要在（约定 11：少一处就是僵尸站点）
for _name in ("bt4g", "bitsearch"):
    _src = pathlib.Path(f"app/providers/indexer/{_name}.py").read_text(encoding="utf-8")
    check(f"{_name} Provider 存在且自注册", "@register" in _src)
    check(f"{_name} 已进 load_builtin_providers 清单",
          f'"app.providers.indexer.{_name}",' in _registry_src)
    check(f"{_name} 已进 BT 实测站点清单", f'"id": "{_name}"' in _catalog_src)
    check(f"{_name} 不伪造全站最新", "async def fetch_latest" in _src)

# bitsearch 的两个实测陷阱必须在代码里留下痕迹
_bs = pathlib.Path("app/providers/indexer/bitsearch.py").read_text(encoding="utf-8")
check("bitsearch 磁力链反 HTML 转义", "html.unescape(magnet_match.group(1))" in _bs)
check("bitsearch 非 200 抛错而非当成零结果", "fetch_text_result(" in _bs)
check("bitsearch 做种/下载数按图标取而非按顺序",
      'fa-arrow-up"' in _bs and 'fa-arrow-down"' in _bs)
check("bitsearch 区分「零结果」与「解析失效」", "_NO_RESULT_MARKER" in _bs)

# bt4g 必须校验根节点：HTML 错误页也是合法 XML
_b4 = pathlib.Path("app/providers/indexer/bt4g.py").read_text(encoding="utf-8")
check("bt4g 校验 RSS 根节点", 'root.tag.lower() != "rss"' in _b4)

# ---- v1.27.0 / M60：站点会自己失效（ADR-103）----
check("ADR-103 记录站点侧失效与四个可验证问题（v1.27.0）", "## ADR-103 ·" in _adr)
check("路线图有里程碑 M60", "里程碑 M60 ·" in roadmap_all)
check("ADR-103 写明 BT4G 5 个镜像全失效的实测", "5 个镜像" in _adr or "5/5" in _adr)
check("ADR-103 引用 ADR-34 拒绝绕验证码", "ADR-34" in _adr)

# 人机验证识别：拦截页回 HTTP 200，靠状态码分不出来
_http_src = pathlib.Path("app/utils/http.py").read_text(encoding="utf-8")
check("http 层提供 is_cf_challenge", "def is_cf_challenge(" in _http_src)
check("人机验证有长度闸门防误判大页面", "8192" in _http_src)
check("人机验证标记含 cf_chl_out", "cf_chl_out" in _http_src)

# 两个 Provider 都要接进去，且 health_check 不能把异常抛给 API 层
for _name in ("bt4g", "bitsearch"):
    _src = pathlib.Path(f"app/providers/indexer/{_name}.py").read_text(encoding="utf-8")
    check(f"{_name} 接入人机验证识别", "is_cf_challenge(" in _src)
    check(f"{_name} health_check 捕获 FetchError 而非抛出",
          "except FetchError as exc:" in _src)

# Bitsearch 是唯一可用的通用磁力源：翻页 + 镜像回退 + 部分成功不丢结果
check("bitsearch 有备用域名", "FALLBACK_BASES" in _bs)
check("bitsearch 支持翻页", "_DEFAULT_MAX_PAGES" in _bs and "max_pages" in _bs)
check("bitsearch 跨页按 infohash 去重", "def _dedupe_key(" in _bs)
check("bitsearch 翻页失败保留已取回结果", "first_error" in _bs)

# 已知失效的预设必须默认关闭，且 site_payload 真的读这个字段
check("site_payload 尊重预设的 enabled 字段", 'preset.get("enabled") is False' in _catalog_src)
check("bt4g 预设如实标记未验证", '"verified": False' in _catalog_src)

_v127 = pathlib.Path("tests/test_bt_coverage_v127.py").read_text(encoding="utf-8")
for _t in ("test_大页面即使含挑战路径也不误判",
           "test_解析拦截页报人机验证而非改版",
           "test_第二页失败保留第一页结果",
           "test_主域名不可达时换备用域名",
           "test_请求启用也不会启用已失效的源"):
    check(f"v1.27.0 回归用例 {_t}", _t in _v127)

# 假种识别的两道闸门
_filters_src = pathlib.Path("app/core/filters.py").read_text(encoding="utf-8")
check("假种第一道闸门：体积 + 高清标签", "_FAKE_TORRENT_MIN_BYTES" in _filters_src)
check("假种第二道闸门：体积 + 剧集标记", "_FAKE_VIDEO_MIN_BYTES" in _filters_src
      and "_FAKE_EPISODE_MARKERS" in _filters_src)
check("假种检测接进过滤链路", "_is_fake_torrent(resource)" in _filters_src)

# 诊断口径：非流式与流式两处都要修正 kept（约定 12）
check("recount_kept 存在且被调用", _search_src.count("recount_kept(") >= 2)
check("非流式路径报 filtered", 'outcome.status = "filtered"' in _search_src)
check("流式路径也修正 kept", "outcome.kept = len(items)" in _search_src)
check("季号写法变体保留季号", "_season_spelling_variants" in _search_src)

# 前端要真的能显示 filtered，否则后端加了状态用户看不到
check("前端有 filtered 状态文案", '"全被过滤"' in app_js)
check("前端 filtered 排序权重靠前", "filtered: 3" in app_js)

# 「门禁数字的来源」这段散文自己必须被门禁覆盖 —— 它曾是全文档漂移最严重处
# 原文写「榜单来源 4 个」，而榜单订阅已在 v1.24.0 下线；真正还在的可数对象是
# 发现榜分类，所以这一项换成它而不是留个已死的数字。
from app.services import discover as _discover_service  # noqa: E402

_discover_categories = _discover_service.categories()
_doc07 = (docs_dir / "07-运维手册.md").read_text(encoding="utf-8")
_DOC07_GATE_NUMBERS = (
    ("API 端点", f"API 端点 **{total_expected}**"),
    ("router", f"router **{router_count}**"),
    ("数据库表", f"数据库表 **{len(Base.metadata.tables)}**"),
    ("Provider", f"Provider **{len(providers)}**"),
    ("前端页面", f"前端页面 **{page_count}**"),
    ("示例站点", f"示例站点 **{len(DEFAULT_SITES)}**"),
    ("内置定时任务", f"内置定时任务 **{len(job_ids)}**"),
    ("设置页分组", f"设置页 **{len(SETTING_GROUPS)} 组**"),
    ("可编辑配置", f"可编辑配置 **{len(config_store.EDITABLE)} 项**"),
    ("内置规则组模板", f"内置规则组模板 **{len(DEFAULT_RULE_GROUPS)} 个**"),
    ("发现榜分类", f"发现榜分类 **{len(_discover_categories)} 个**"),
)
for _label, _frag in _DOC07_GATE_NUMBERS:
    check(f"docs/07 门禁清单数字对得上：{_label}", _frag in _doc07, _frag)
check("docs/07 记录这段散文自己曾漂移", "自己不在门禁里" in _doc07)
# 同一批数字散落在其他文档，一并钉住
check("docs/01 设置页数字正确",
      f"设置页（{len(SETTING_GROUPS)} 组，其中 {len(config_store.EDITABLE)} 项可在线改）"
      in (docs_dir / "01-项目现状.md").read_text(encoding="utf-8"))
check("docs/02 可编辑项数字正确",
      f"白名单里的 {len(config_store.EDITABLE)} 项"
      in (docs_dir / "02-架构设计.md").read_text(encoding="utf-8"))
_doc12 = (docs_dir / "12-功能特性详解.md").read_text(encoding="utf-8")
check("docs/12 可编辑项数字正确", f"**{len(config_store.EDITABLE)} 项**" in _doc12)
check("docs/12 分组数字正确", f"配置按 **{len(SETTING_GROUPS)} 组**" in _doc12)

print()
print("=" * 60)
failed = [c for c in checks if not c[0]]
print(f"README 事实校验：{len(checks) - len(failed)}/{len(checks)} 通过")
for _, name, detail in failed:
    print(f"  FAIL {name} {detail}")
sys.exit(1 if failed else 0)
