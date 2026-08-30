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


#: 文档里出现的**第三方** API（夸克分享、某站搜索入口等），不归本项目管
THIRD_PARTY = {
    # ldysg 站点自己的接口，恰好也叫 /api.php —— 注意它不是 /api/ 前缀
    "/api.php",
    "/share/sharepage/token",
    "/share/sharepage/detail",
    "/share/sharepage/save",
    "/e/search/index.php",
}

def _is_absolute(path: str) -> bool:
    """是否写成了本项目的绝对路径。

    必须按**路径分段**判断：`/api.php` 是某站点自己的接口，
    用 ``startswith("/api")`` 会把它误当成本项目端点。
    """
    return path == "/api" or path.startswith("/api/")


#: ``METHOD /路径`` 连写，最常见的行内写法
_INLINE = re.compile(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+`?(/[A-Za-z0-9_/{}.\-]+)")

#: **端点表格**里方法与路径分列两格的写法：
#: ``| POST | `/api/v1/chatops/webhook/{platform}` | 匿名 | 平台回调入口 |``
#: 行内正则匹配不到这种，而 ChatOps 那张表恰恰是全文最要紧的地方
#: （回调地址要粘进飞书/钉钉后台）。要求整行以方法单元格开头，
#: 才能与「前缀分组表」（``| `/api/v1/auth` | 登录… |``）区分开 ——
#: 后者列的是 router 前缀，本来就不是可访问端点。
_TABLE_ROW = re.compile(
    r"^\|\s*(?:GET|POST|PUT|PATCH|DELETE)(?:\s*/\s*(?:GET|POST|PUT|PATCH|DELETE))*"
    r"\s*\|\s*`(/[A-Za-z0-9_/{}.\-]+)`"
)


def _scan() -> dict[str, list[str]]:
    """扫出文档里所有可识别为「本项目端点」的写法及其出处。

    两种形态都要覆盖：行内 ``METHOD /path``，以及端点表格里
    方法与路径分列两格的 ``| POST | `/api/v1/xxx` | … |``。

    刻意**不**把所有反引号路径都当端点：文档里遍地是挂载点
    （`/volume1/media`）、站点内部路径（`/js/front.js`）、
    router 前缀分组（`/api/v1/auth`）和第三方接口（`/api/auth/login`
    是 AList 的）。一律认成端点只会淹没真问题。
    """
    found: dict[str, list[str]] = {}
    for doc in sorted(DOCS.rglob("*.md")):
        for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            raws = list(_INLINE.findall(line))
            row = _TABLE_ROW.match(line.strip())
            if row:
                raws.append(row.group(1))
            for raw in raws:
                path = raw.rstrip("`.,;)：，。").split("?")[0]
                found.setdefault(path, []).append(f"{doc.name}:{number}")
    return found


def _documented() -> dict[str, list[str]]:
    """只取写成绝对路径（``/api/...``）的那些。"""
    return {p: w for p, w in _scan().items() if _is_absolute(p)}


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


def test_relative_paths_resolve_under_api_v1(real_paths):
    """写成相对形式的路径（``POST /downloads``）必须能用 ``/api/v1`` 补全。

    文档里有大量这种简写，本身是可接受的（上下文明确、也不会被误当成
    可直接粘贴的绝对地址）。但简写也会**过期** —— 端点改名或下线后，
    这些行会静默变成错的。这条测试保证每个简写都仍指向真实端点。

    与 `/api/pan/...` 那类错误的区别：那种**看起来是完整地址**，
    用户会直接拿去用，所以是真故障；简写只在文档内部指代，
    但同样需要跟着代码走。
    """
    broken: list[str] = []
    for path, places in sorted(_scan().items()):
        if _is_absolute(path) or path in THIRD_PARTY:
            continue
        if _normalize("/api/v1" + path) in real_paths:
            continue
        if _normalize(path) in real_paths:
            continue
        broken.append(f"{path}（{places[0]}）")
    assert not broken, (
        "这些相对路径既不是真实端点、也无法用 /api/v1 补全"
        "（端点改名/下线后文档没跟上，或需加入 THIRD_PARTY）：\n  "
        + "\n  ".join(broken)
    )


def test_third_party_list_is_not_ours(real_paths):
    """THIRD_PARTY 里的路径不该是本项目的端点。"""
    ours = [p for p in THIRD_PARTY if _normalize("/api/v1" + p) in real_paths]
    assert not ours, f"这些被标为第三方，其实是本项目端点：{ours}"
