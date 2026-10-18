"""从代码生成文档统计；不启动应用、不连接用户数据库或外部服务。"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def collect() -> dict:
    # Settings 导入时创建目录。统计过程必须使用临时配置，不能落到用户数据。
    temporary = Path(tempfile.mkdtemp(prefix="cineflow_facts_"))
    for key in ("DATA_DIR", "DOWNLOAD_DIR", "LIBRARY_DIR", "STRM_DIR", "PLUGIN_DIR"):
        os.environ[f"CF_{key}"] = str(temporary / key.lower())
    os.environ["CF_DB_URL"] = f"sqlite:///{(temporary / 'facts.db').as_posix()}"
    os.environ["CF_CONFIG_FILE"] = str(temporary / "missing.yaml")
    os.environ["CF_SCHEDULER_ENABLED"] = "false"

    from app.api.routers.system import SETTING_GROUPS
    from app.core.version import APP_VERSION
    from app.db.base import Base
    from app.db.init_db import DEFAULT_RULE_GROUPS
    from app.main import app
    from app.providers.registry import list_providers, load_builtin_providers
    from app.services.config_store import EDITABLE
    from app.services.discover import categories
    from app.services.scheduler import builtin_specs
    from app.services.site_catalog import DEFAULT_SITES

    load_builtin_providers()
    frontend = (ROOT / "web/assets/app.js").read_text(encoding="utf-8")
    pages = frontend.split("const PAGES = [", 1)[1].split("];", 1)[0]
    parents = frontend.split("const PAGE_PARENT = {", 1)[1].split("};", 1)[0]
    providers = defaultdict(list)
    for provider in list_providers():
        providers[provider["kind"]].append(provider["name"])
    return {
        "版本": APP_VERSION,
        "API 端点": sum(
            1
            for methods in app.openapi()["paths"].values()
            for method in methods
            if method in {"get", "post", "put", "patch", "delete"}
        ),
        "router": (ROOT / "app/api/router.py").read_text(encoding="utf-8").count("include_router("),
        "数据库表": len(Base.metadata.tables),
        "Provider": sum(map(len, providers.values())),
        "可访问页面": len(re.findall(r"\{ key:", pages)),
        "一级导航": len(re.findall(r"\{ key:", pages)) - len(re.findall(r'\w+:\s*"', parents)),
        "内置调度任务": len(builtin_specs()),
        "默认站点": len(DEFAULT_SITES),
        "配置分组": len(SETTING_GROUPS),
        "兼容配置键": len(EDITABLE),
        "测试模块": len(list((ROOT / "tests").glob("test_*.py"))),
        "规则组模板": len(DEFAULT_RULE_GROUPS),
        "发现榜分类": len(categories()),
        "providers": dict(providers),
    }


def render(facts: dict) -> str:
    lines = [
        "# 项目事实（自动生成）",
        "",
        "运行 `python scripts/project_facts.py --write` 更新；CI 使用 `--check` 校验。",
        "",
        "| 项目 | 当前值 |",
        "|---|---:|",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in facts.items() if key != "providers")
    lines.extend(
        [
            "",
            "调度周期的兼容配置键仍可通过 API 访问，实际只写调度配置；设置页引导至定时任务。",
            "",
            "| Provider 类别 | 数量 | 名称 |",
            "|---|---:|---|",
        ]
    )
    for kind, names in sorted(facts["providers"].items()):
        lines.append(f"| {kind} | {len(names)} | {', '.join(f'`{name}`' for name in names)} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    facts = collect()
    document = ROOT / "docs/project-facts.md"
    expected = render(facts)
    if options.write:
        document.write_text(expected, encoding="utf-8")
        print("Updated docs/project-facts.md")
    elif options.check:
        if not document.exists() or document.read_text(encoding="utf-8") != expected:
            print("项目事实过期，请运行 python scripts/project_facts.py --write")
            return 1
        project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        if f'version = "{facts["版本"]}"' not in project:
            print("pyproject 与 API 版本不一致")
            return 1
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        if f"version-{facts['版本']}" not in readme:
            print("README 版本徽章与代码不一致")
            return 1
        print("Project facts checked")
    else:
        print(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
