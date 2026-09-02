"""更新检测与在线更新。

**为什么不能只查 GitHub Release**：本项目**至今没有发过一个 Release/Tag**
（本轮实测：``GET /releases/latest`` → 404，``/releases`` 与 ``/tags`` 都是空数组）。
只按 Release 判断的实现会永远回答"已是最新版本" —— 一个**不报错、看起来还正常**
的假功能，而且越是勤于点"检查更新"的用户越被误导。

所以判定分两条路，Release 优先、主干兜底：

1. **有 Release**：取最新（含预发布时按 channel 过滤）版本号比对；
2. **没有 Release**：读主干上的 ``app/core/version.py`` 拿 ``APP_VERSION``，
   再取最新提交做展示。这条路对"持续在 main 上发版"的项目才是真实可用的。

**关于"直接操作更新"**：能不能真更新取决于部署形态，所以先探测再给入口
（ADR-18：不能生效的功能就别给按钮）：

* **源码部署**（有 ``.git`` 且 git 可用）→ 真的执行 ``git pull --ff-only``，
  成功后提示重启进程生效。用 ``--ff-only`` 是刻意的：本地有改动/分叉时
  宁可失败并说清原因，也绝不 ``reset --hard`` 或 merge —— 那会吞掉用户的本地修改。
* **Docker 部署**（镜像里没有 ``.git``）→ **不假装能更新**。容器无法替换自己的镜像，
  返回 ``can_apply=false`` 并给出可直接复制的 ``docker compose pull && up -d``。
  这里刻意不去碰 docker.sock：为了自更新而要求挂载 docker.sock，
  等于把整台宿主机的控制权交给本进程，代价远大于省下的一条命令。
"""

from __future__ import annotations

import re
import subprocess
import time
from typing import Any

from app.core.config import ROOT_DIR, settings
from app.core.logger import get_logger
from app.core.version import APP_VERSION
from app.utils.http import fetch_json, fetch_text

logger = get_logger(__name__)

#: 上游仓库（固定，不接受用户传任意地址：那等于给了一个任意代码执行入口）
REPO = "wengdajie/CineFlow"
API_BASE = "https://api.github.com"
RAW_BASE = "https://raw.githubusercontent.com"

#: 主干上的版本号文件（没有 Release 时的兜底判据）
VERSION_FILE = "app/core/version.py"
DEFAULT_BRANCH = "main"

#: 检查结果缓存秒数。GitHub 未鉴权只有 60 次/小时，
#: 用户在页面上连点几次就可能打满 —— 那时返回的是 403 而不是"没更新"，
#: 会被当成"更新检测坏了"。
CACHE_TTL = 30 * 60

_VERSION_RE = re.compile(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']')
#: 只认 ``1.2.3`` / ``v1.2.3`` / ``1.2.3-beta.1`` 这类；认不出就不比较（宁可不提示）
_SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-+](.+))?$")

_cache: tuple[float, dict[str, Any]] | None = None


def parse_version(text: str | None) -> tuple[int, int, int, str] | None:
    """把版本号解析成可比较的元组，认不出返回 ``None``。"""
    match = _SEMVER_RE.match(str(text or "").strip())
    if not match:
        return None
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        str(match.group(4) or ""),
    )


def is_newer(candidate: str | None, current: str | None) -> bool:
    """``candidate`` 是否严格新于 ``current``。

    任一方解析不出来就返回 ``False`` —— **宁可不提示，也不要误报有新版本**：
    误报会让用户去执行更新操作，而那可能是一次不必要的重启。
    预发布号（``1.2.3-beta``）视为**低于**同号正式版，与 semver 一致。
    """
    left, right = parse_version(candidate), parse_version(current)
    if left is None or right is None:
        return False
    if left[:3] != right[:3]:
        return left[:3] > right[:3]
    # 主版本相同：有预发布后缀的一方更低；都有则按字典序
    left_pre, right_pre = left[3], right[3]
    if bool(left_pre) != bool(right_pre):
        return not left_pre
    return left_pre > right_pre


def deployment_mode() -> str:
    """判断部署形态：``source``（可自更新） / ``docker`` / ``unknown``。

    判据用 ``.git`` 目录**是否存在**而不是环境变量：镜像里没有 ``.git``
    （Dockerfile 只 COPY 了 app/web/plugins/docs），源码部署一定有。
    这比看 ``/.dockerenv`` 可靠 —— 在容器里跑源码的用户也存在，
    而对他们来说 ``git pull`` 确实是有效的更新方式。
    """
    if (ROOT_DIR / ".git").exists():
        return "source"
    if (ROOT_DIR / "app").exists():
        return "docker"
    return "unknown"


def _git(*args: str, timeout: int = 60) -> tuple[bool, str]:
    """执行 git 命令，返回 ``(成功, 输出)``。"""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(ROOT_DIR),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return False, "系统里没有 git 命令"
    except subprocess.TimeoutExpired:
        return False, f"git {args[0]} 超时（{timeout}s）"
    except Exception as exc:  # pragma: no cover - 环境异常兜底
        return False, f"{type(exc).__name__}: {exc}"
    out = (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
    return proc.returncode == 0, out


async def _latest_release(include_prerelease: bool) -> dict[str, Any] | None:
    """取最新 Release；仓库没有 Release 时返回 ``None``（不是错误）。"""
    data = await fetch_json(
        f"{API_BASE}/repos/{REPO}/releases",
        params={"per_page": 20},
        headers={"Accept": "application/vnd.github+json"},
        timeout=settings.SEARCH_TIMEOUT,
    )
    if not isinstance(data, list) or not data:
        return None
    best: dict[str, Any] | None = None
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("draft"):
            continue
        if item.get("prerelease") and not include_prerelease:
            continue
        tag = str(item.get("tag_name") or "")
        if parse_version(tag) is None:
            continue
        if best is None or is_newer(tag, str(best.get("tag_name") or "")):
            best = item
    return best


async def _latest_from_branch() -> dict[str, Any]:
    """从主干读取版本号与最新提交（没有 Release 时的兜底）。"""
    result: dict[str, Any] = {"version": "", "commit": "", "message": "", "date": ""}
    text = await fetch_text(
        f"{RAW_BASE}/{REPO}/{DEFAULT_BRANCH}/{VERSION_FILE}",
        timeout=settings.SEARCH_TIMEOUT,
    )
    if text:
        match = _VERSION_RE.search(text)
        if match:
            result["version"] = match.group(1)

    commits = await fetch_json(
        f"{API_BASE}/repos/{REPO}/commits",
        params={"per_page": 1, "sha": DEFAULT_BRANCH},
        headers={"Accept": "application/vnd.github+json"},
        timeout=settings.SEARCH_TIMEOUT,
    )
    if isinstance(commits, list) and commits and isinstance(commits[0], dict):
        head = commits[0]
        result["commit"] = str(head.get("sha") or "")[:8]
        commit_info = head.get("commit") or {}
        result["message"] = str(commit_info.get("message") or "").splitlines()[0][:200]
        result["date"] = str((commit_info.get("committer") or {}).get("date") or "")
    return result


def local_commit() -> str:
    """本地代码的提交号（Docker 镜像里没有 git，返回空串）。"""
    if deployment_mode() != "source":
        return ""
    ok, out = _git("rev-parse", "--short=8", "HEAD", timeout=15)
    return out.strip() if ok else ""


async def check(*, force: bool = False, include_prerelease: bool = False) -> dict[str, Any]:
    """检查是否有新版本。

    结果带 ``source`` 字段说明结论是**怎么来的**（``release`` / ``branch``），
    这样"没有新版本"这个结论本身也是可核对的，而不是让用户猜。
    """
    global _cache
    now = time.time()
    if not force and _cache and now - _cache[0] < CACHE_TTL:
        return {**_cache[1], "cached": True}

    mode = deployment_mode()
    result: dict[str, Any] = {
        "current": APP_VERSION,
        "current_commit": local_commit(),
        "latest": "",
        "has_update": False,
        "source": "",
        "notes": "",
        "published_at": "",
        "url": f"https://github.com/{REPO}",
        "mode": mode,
        "can_apply": mode == "source",
        "message": "",
        "cached": False,
        "checked_at": int(now),
    }

    try:
        release = await _latest_release(include_prerelease)
    except Exception as exc:  # pragma: no cover - 网络异常统一走下面的兜底
        logger.warning("读取 Release 失败：%s", exc)
        release = None

    if release:
        tag = str(release.get("tag_name") or "")
        result.update(
            {
                "latest": tag.lstrip("v"),
                "source": "release",
                "notes": str(release.get("body") or "")[:4000],
                "published_at": str(release.get("published_at") or ""),
                "url": str(release.get("html_url") or result["url"]),
                "has_update": is_newer(tag, APP_VERSION),
            }
        )
    else:
        branch = await _latest_from_branch()
        result.update(
            {
                "latest": branch["version"],
                "source": "branch",
                "latest_commit": branch["commit"],
                "notes": branch["message"],
                "published_at": branch["date"],
                "url": f"https://github.com/{REPO}/commits/{DEFAULT_BRANCH}",
                "has_update": is_newer(branch["version"], APP_VERSION),
            }
        )
        if not branch["version"]:
            result["message"] = (
                "无法读取上游版本号（网络不通或 GitHub 限流），请稍后再试"
            )

    if not result["message"]:
        if result["has_update"]:
            result["message"] = f"发现新版本 {result['latest']}（当前 {APP_VERSION}）"
        elif result["latest"]:
            # 同版本号但提交不同时如实说明：main 上可能已有未发版的修复
            same_version_new_commit = (
                result["source"] == "branch"
                and result.get("latest_commit")
                and result["current_commit"]
                and not result["current_commit"].startswith(str(result["latest_commit"]))
            )
            result["message"] = (
                f"版本号相同（{APP_VERSION}），但上游主干有新提交 "
                f"{result['latest_commit']}"
                if same_version_new_commit
                else f"已是最新版本（{APP_VERSION}）"
            )

    _cache = (now, result)
    return result


def apply_update() -> dict[str, Any]:
    """执行更新（仅源码部署）。

    只做 ``git pull --ff-only``：**不 merge、不 reset**。
    本地有改动或已分叉时如实失败并把 git 的原话带出来 ——
    自动 merge 会产生用户看不懂的冲突，``reset --hard`` 则直接丢掉他的修改。
    """
    mode = deployment_mode()
    if mode != "source":
        return {
            "success": False,
            "restart_required": False,
            "message": (
                "当前是容器部署，程序无法替换自己的镜像。"
                "请在宿主机执行：docker compose pull && docker compose up -d"
                "（飞牛用 -f docker-compose.fnos.yml）"
            ),
            "commands": [
                "docker compose pull",
                "docker compose up -d",
            ],
        }

    ok, status = _git("status", "--porcelain", timeout=30)
    if not ok:
        return {"success": False, "restart_required": False, "message": f"git 不可用：{status}"}
    if status.strip():
        changed = len([line for line in status.splitlines() if line.strip()])
        return {
            "success": False,
            "restart_required": False,
            "message": (
                f"本地有 {changed} 个未提交的改动，已中止更新。"
                "请先提交或撤销这些改动（我们不会自动丢弃你的修改）"
            ),
            "detail": status[:2000],
        }

    before = local_commit()
    ok, out = _git("pull", "--ff-only", timeout=180)
    if not ok:
        return {
            "success": False,
            "restart_required": False,
            "message": "git pull 失败（可能是网络不通，或本地分支已与上游分叉）",
            "detail": out[:2000],
        }
    after = local_commit()
    if before and before == after:
        return {
            "success": True,
            "restart_required": False,
            "message": f"已是最新代码（{after}），无需重启",
            "detail": out[:2000],
        }
    logger.info("在线更新完成：%s → %s", before or "?", after or "?")
    return {
        "success": True,
        "restart_required": True,
        "message": (
            f"代码已更新（{before or '?'} → {after or '?'}）。"
            "请重启 CineFlow 进程使新代码生效"
        ),
        "detail": out[:2000],
    }
