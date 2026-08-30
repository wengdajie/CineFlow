"""文档里写的 API 路径必须真实存在。

**为什么值得一条独立测试**：路径写错**没有任何自检机制**。
`docs/` 是用户唯一的接线说明书，而其中有些路径是要被
**复制粘贴到别的系统里**去的 —— ChatOps 的回调地址就要填进
飞书/钉钉/Telegram 的后台。填错的表现是「机器人死活没反应」，
平台侧只会给个 404，用户根本不会想到是文档抄错了前缀。

这条测试用 `app.openapi()` 做权威来源（`app.routes` 在当前 FastAPI 版本里
被 `_IncludedRouter` 包住，拿不到子路由），因此它天然跟着代码走：
以后重构改了前缀，文档没跟上就会红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.main import app

DOCS = Path(__file__).resolve().parent.parent / "docs"

#: 文档里出现但**不属于本项目**、或刻意不进 OpenAPI 的路径
ALLOW = {
    # include_in_schema=False：给 Docker healthcheck / 反代探活用，不进 schema
    "/api/health",
    # AList 自己的 API（第三方服务），不是 CineFlow 的端点
    "/api/fs/add_offline_download",
}


def _documented() -> dict[str, list[str]]:
    """扫出文档里所有形如 ``METHOD /api/...`` 的路径及其出处。"""
    pattern = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+`?(/api/[A-Za-z0-9_/{}.\-]+)")
    found: dict[str, list[str]] = {}
    for doc in sorted(DOCS.rglob("*.md")):
        for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            for _method, raw in pattern.findall(line):
                path = raw.rstrip("`.,;)：，。").split("?")[0]
                found.setdefault(path, []).append(f"{doc.name}:{number}")
    return found


def _normalize(path: str) -> str:
    """把路径参数统一成 ``{}``，让 ``{id}`` 与 ``{site_id}`` 可比。"""
    return re.sub(r"\{[^}]+\}", "{}", path).rstrip("/")


@pytest.fixture(scope="module")
def real_paths() -> set[str]:
    return {_normalize(path) for path in app.openapi()["paths"]}


def test_documented_endpoints_exist(real_paths):
    """文档里的每个 API 路径都必须能在 OpenAPI 里找到。

    历史上 `docs/05-ChatOps-机器人.md` 与 `docs/06-网盘管理.md` 整章
    漏写了 `/v1` 前缀（`/api/pan/...` 而真实路径是 `/api/v1/pan/...`）,
    同一份文档的另一些段落却是对的 —— 属于典型的抄改遗留。
    """
    broken: list[str] = []
    for path, places in sorted(_documented().items()):
        if path in ALLOW:
            continue
        if _normalize(path) in real_paths:
            continue
        hint = ""
        # 最常见的错法就是漏 /v1，直接把正确答案提示出来
        guess = _normalize(path.replace("/api/", "/api/v1/", 1))
        if guess in real_paths:
            hint = f" → 应为 {path.replace('/api/', '/api/v1/', 1)}"
        broken.append(f"{path}{hint}（{places[0]}）")
    assert not broken, "文档写了不存在的 API 路径：\n  " + "\n  ".join(broken)


def test_allowlist_stays_justified(real_paths):
    """豁免名单里的路径不该悄悄变成真端点。

    `/api/health` 如果哪天进了 schema，就该从名单里拿掉，
    否则这个名单会慢慢变成"掩盖问题的地方"。
    """
    stale = [path for path in ALLOW if _normalize(path) in real_paths]
    assert not stale, f"这些路径已进入 OpenAPI，应从豁免名单移除：{stale}"


def test_health_endpoint_really_exists(client):
    """`/api/health` 不在 schema 里，但必须真的能访问。

    它是 Dockerfile 的 healthcheck 目标，挂了会导致容器一直 unhealthy。
    """
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
