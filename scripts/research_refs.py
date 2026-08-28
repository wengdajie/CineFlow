#!/usr/bin/env python
"""重新拉取对标项目的 star 数与功能清单。

用途：`docs/09-竞品对标与差距分析.md` 里的数据是某个时间点的快照，
竞品会持续演进。重新评估 v1.4.0+ 范围前跑一遍本脚本，
把结果同步回文档的差距矩阵。

需要外网。GitHub 匿名 API 限速 60 次/小时，本脚本请求量远低于此。
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

HEADERS = {
    "User-Agent": "cineflow-research",
    "Accept": "application/vnd.github+json",
}

#: 用户指定的参考项目 + 检索出的同类高星方案
REFERENCE_REPOS = [
    "jxxghp/MoviePilot",
    "qq85423296/T3FAP",
    "walkingddd/TgtoDrive",
    "Cp0204/quark-auto-save",
    "Cp0204/SmartStrm",
    "AkimioJR/MediaWarp",
    "linyuan0213/nexus-media",
    "qicfan/qmediasync",
    "AmbitiousJun/go-emby2openlist",
]

#: 用于发现新竞品的检索词
QUERIES = [
    "quark auto save",
    "alist strm",
    "emby strm 网盘",
    "追剧 自动化",
    "nfo 刮削 媒体库",
    "MoviePilot plugin",
]

#: 差距矩阵关心的关键能力 -> 在 README 里的特征词
CAPABILITY_HINTS = {
    "STRM 生成": ("strm",),
    "302 直链": ("302",),
    "NFO 刮削": ("nfo", "刮削"),
    "网盘转存": ("转存", "auto-save", "autosave"),
    "增量同步": ("增量",),
    "洗版": ("洗版",),
    "WebDAV": ("webdav",),
    "聊天机器人": ("telegram", "钉钉", "飞书", "机器人", "bot"),
    "插件体系": ("插件", "plugin"),
    "媒体分类归档": ("归档", "分类", "自动归类"),
}


def _get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_readme(repo: str) -> str:
    """取 README 原文；main / master 都试一遍。"""
    for branch in ("main", "master"):
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/README.md"
        try:
            request = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(request, timeout=25) as response:
                return response.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError:
            continue
        except Exception:
            continue
    return ""


def survey_repo(repo: str) -> dict:
    """采集单个项目的 star 数与命中的能力关键词。"""
    info: dict = {"repo": repo, "stars": None, "capabilities": [], "error": ""}
    try:
        meta = _get_json(f"https://api.github.com/repos/{repo}")
        info["stars"] = meta.get("stargazers_count")
        info["description"] = (meta.get("description") or "")[:120]
    except Exception as exc:
        info["error"] = str(exc)[:80]
        return info

    readme = _get_readme(repo).lower()
    if readme:
        info["capabilities"] = [
            name
            for name, hints in CAPABILITY_HINTS.items()
            if any(hint in readme for hint in hints)
        ]
    return info


def discover(query: str, limit: int = 5) -> list[dict]:
    """按关键词检索可能漏掉的新竞品。"""
    url = (
        "https://api.github.com/search/repositories?q="
        + urllib.parse.quote(query)
        + f"&sort=stars&order=desc&per_page={limit}"
    )
    try:
        data = _get_json(url)
    except Exception as exc:
        print(f"  检索失败：{exc}")
        return []
    return [
        {
            "repo": item["full_name"],
            "stars": item["stargazers_count"],
            "description": (item.get("description") or "")[:90],
        }
        for item in data.get("items", [])
    ]


def main() -> int:
    print("=" * 78)
    print("一、对标项目现状（star 数 + README 命中的能力关键词）")
    print("=" * 78)
    known = set()
    for repo in REFERENCE_REPOS:
        result = survey_repo(repo)
        known.add(repo.lower())
        if result["error"]:
            print(f"  {repo:<34} 采集失败：{result['error']}")
            continue
        stars = result["stars"]
        print(f"  {repo:<34} ★{stars if stars is not None else '-':<7}")
        if result["capabilities"]:
            print(f"      能力命中：{' / '.join(result['capabilities'])}")

    print()
    print("=" * 78)
    print("二、关键词检索（发现尚未纳入对标的新方案）")
    print("=" * 78)
    fresh: list[dict] = []
    for query in QUERIES:
        print(f"\n  [{query}]")
        for item in discover(query):
            flag = "" if item["repo"].lower() in known else "  ← 新"
            print(f"    {item['repo']:<40} ★{item['stars']:<7}{flag}")
            if item["repo"].lower() not in known and item["stars"] >= 300:
                fresh.append(item)

    print()
    print("=" * 78)
    print("三、结论")
    print("=" * 78)
    if fresh:
        print("  发现以下高星（★≥300）方案尚未纳入 docs/09 的对标矩阵，建议评估：")
        for item in fresh:
            print(f"    - {item['repo']} ★{item['stars']}：{item['description']}")
        print("\n  请更新 docs/09-竞品对标与差距分析.md 的 §1 与 §2。")
    else:
        print("  未发现需要新增对标的高星方案，docs/09 的矩阵仍然适用。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
