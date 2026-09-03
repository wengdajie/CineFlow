"""百度开放平台**存储** Provider：僵尸站点回归 + Cookie 入口误用 + 令牌自动刷新。

这个文件钉住 v1.24.0 复盘查出的两个真缺陷（都只在实机暴露）：

1. **僵尸站点（v1.16.0 修过的 bug 在 v1.23.0 原样复发）**
   ``services/panlogin/baidu_openapi.py`` 让用户能授权成功并建出一条
   ``provider="baidu_openapi"`` 的 panstorage 站点，但
   ``providers/panstorage/`` 下没有对应实现 →
   ``create_provider("baidu_openapi")`` 返回 ``None``。
   实测（v1.23.0）：``GET /pan`` 总览里 **2 个盘**，那条百度开放平台站点
   不在其中；站点列表里它却 ``enabled=True``、``last_status="授权成功"``。
   根因：登录通道与存储通道是两套注册表，加前者不会强制加后者。
   所以这里不只测「baidu_openapi 能实例化」，而是把**不变式**钉住：
   *每个声明支持登录的网盘，都必须能造出存储 Provider*（见
   :func:`test_every_login_provider_has_a_storage_provider`）。

2. **声明不支持 Cookie，却没真的挡住**
   ``PROVIDERS["baidu_openapi"]["cookie"] = False``，前端据此不渲染
   「导入 Cookie」按钮 —— 但 ``apply_cookie()`` 从没读这个能力位。
   实测：``POST /pan/login/cookie {"provider":"baidu_openapi", ...}`` 返回
   ``400 未保存：授权无效（errno=20017）``。看着像挡住了，其实是**碰巧**：
   它把 Cookie 当 access_token 送去 uinfo 校验，失败信息把用户引向
   「重新授权」，而真正的问题是用错了入口（ADR-82：失败必须可区分）。
   若哪天 verify 放宽，就会真的写出一个坏站点。
"""

from __future__ import annotations

import asyncio
import pathlib
import subprocess
import sys
from typing import Any

import httpx
import pytest

from app.providers.panstorage.baidu_openapi import BaiduOpenApiStorage
from app.providers.registry import get_provider_class, load_builtin_providers
from app.services import pan_login


def run(coro):
    return asyncio.run(coro)


def _patch(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """把存储模块与 utils.http 创建的客户端都接到 MockTransport（全程离线）。"""

    def factory(timeout=None, headers=None, follow_redirects=True):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers=headers or {},
            follow_redirects=follow_redirects,
        )

    import app.providers.panstorage.baidu_openapi as mod
    import app.utils.http as uh

    monkeypatch.setattr(mod, "async_client", factory)
    monkeypatch.setattr(uh, "async_client", factory)


def _store(**options: Any) -> BaiduOpenApiStorage:
    base = {"access_token": "tok-good"}
    base.update(options)
    return BaiduOpenApiStorage({"name": "百度开放平台", "options": base})


# ---------------------------------------------------------------------------
# 1. 僵尸站点：注册表必须能造出实例
# ---------------------------------------------------------------------------


def test_baidu_openapi_storage_is_registered() -> None:
    """登录能建站点，注册表就必须能造出对应存储 Provider，否则是僵尸站点。"""
    load_builtin_providers()
    assert get_provider_class("baidu_openapi") is BaiduOpenApiStorage


#: 在**干净子进程**里跑注册不变式的脚本。
#:
#: 为什么不能在本文件内直接断言：本文件顶部 ``from ...baidu_openapi import``
#: 已经触发了 ``@register``，所以即使
#: ``load_builtin_providers()`` 的模块清单里漏掉它，断言照样绿。
#: 注入验证实测证明了这一点：把清单里那行删掉，
#: ``test_every_login_provider_has_a_storage_provider`` **仍然全绿** ——
#: 也就是说它当时根本没在守护「装机默认加载」这件事，
#: 而僵尸站点恰恰只在「用户没手动 import」的真实运行路径上发生。
_REGISTRY_INVARIANT_SCRIPT = """
import sys
sys.path.insert(0, ".")
from app.providers.registry import get_provider_class, load_builtin_providers
from app.services.pan_login import PROVIDERS

load_builtin_providers()
missing = [n for n in PROVIDERS if get_provider_class(n) is None]
print("MISSING=" + ",".join(missing))
"""


def test_every_login_provider_has_a_storage_provider(tmp_path) -> None:
    """不变式：任何能登录的网盘都必须有存储实现，且**默认加载**就能造出来。

    断言的是行为（能不能造出实例），不是"文件在不在" —— 文件存在但没
    ``@register``、没进 ``load_builtin_providers`` 清单、或名字写错，
    都一样是僵尸站点。
    """
    script = tmp_path / "check.py"
    script.write_text(_REGISTRY_INVARIANT_SCRIPT, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, f"子进程失败：{proc.stderr[-2000:]}"
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("MISSING=")]
    assert line, f"没拿到结果：{proc.stdout!r} {proc.stderr[-500:]!r}"
    missing = [n for n in line[0][len("MISSING="):].split(",") if n]
    assert not missing, f"这些网盘能登录却没有存储 Provider（僵尸站点）：{missing}"


def test_storage_kind_and_capabilities_are_honest() -> None:
    """开放平台没有转存接口，能力位必须如实为 False。"""
    storage = _store()
    assert storage.kind == "panstorage"
    caps = storage.capabilities()
    assert caps["save"] is False, "开放平台无转存接口，不能声明支持"
    for able in ("delete", "rename", "move", "search", "keepalive"):
        assert caps[able] is True


def test_save_share_refuses_with_actionable_message() -> None:
    """拒绝转存时要说清「该怎么办」，不能只说不支持（ADR-82）。"""
    result = run(_store().save_share("https://pan.baidu.com/s/1AbCdEfGhIj"))
    assert result.success is False
    assert "不支持转存" in result.message
    # 必须指出替代路径，否则用户不知道下一步做什么
    assert "Cookie" in result.message


def test_service_layer_keeps_provider_refusal_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务层拒绝转存时必须**带上 Provider 写的理由**，不能自己另造一句。

    每个不支持转存的 Provider 都在 ``save_share()`` 里写清了替代路径。
    旧实现在 ``if not storage.supports_save`` 分支里直接 return
    「X 不支持从分享链接转存」，把那句话丢掉了 —— live 实测确认：
    ``POST /pan/save`` 返回 ``400 百度开放平台… 不支持从分享链接转存``，
    用户看不到"该改用哪条路"（ADR-82：拒绝要给下一步）。
    """
    from app.services import pan_storage as svc

    storage = _store()
    monkeypatch.setattr(svc, "get_storage", lambda _sid: storage)
    result = run(
        svc.save_share("https://pan.baidu.com/s/1AbCdEfGhIj", site_id=1)
    )
    assert result["success"] is False
    # Provider 给的替代路径必须原样出现在服务层返回里
    assert "Cookie" in result["message"], result["message"]
    assert "不支持转存" in result["message"], result["message"]


# ---------------------------------------------------------------------------
# 2. Cookie 入口必须按能力位真的挡住
# ---------------------------------------------------------------------------


def test_apply_cookie_rejects_provider_that_declares_no_cookie() -> None:
    """声明 cookie=False 就要在服务层挡住，且**不发任何网络请求**。

    旧实现靠 verify() 碰巧失败来"挡"，报错指向「授权无效」，
    把用户引向重新授权，而真正的问题是入口用错了。
    """
    assert pan_login.PROVIDERS["baidu_openapi"]["cookie"] is False
    result = run(
        pan_login.apply_cookie("baidu_openapi", "BDUSS=whatever", verify=True)
    )
    assert result["success"] is False
    # 必须点名是入口问题，不能是「授权无效」
    assert "不支持 Cookie 导入" in result["message"]
    assert "授权无效" not in result["message"]


def test_apply_cookie_still_works_for_cookie_providers() -> None:
    """反向担保：挡 baidu_openapi 不能把正常的 Cookie 通道一起挡掉。"""
    for name in ("baidu", "pan115", "quark"):
        assert pan_login.PROVIDERS[name]["cookie"] is True


# ---------------------------------------------------------------------------
# 3. access_token 过期自动刷新并回写
# ---------------------------------------------------------------------------


def test_expired_token_is_refreshed_and_request_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """令牌失效 → 刷新 → 重试原请求，调用方无感。

    钉住的是行为链：第一次带旧 token 回 errno=-6，刷新后第二次带新 token
    才成功。只要少了「重试」这一步，list_dir 就会返回空列表 ——
    而空列表与「目录真的是空的」无法区分（ADR-82）。
    """
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("access_token")
        seen.append(str(token))
        if token == "tok-old":
            return httpx.Response(200, json={"errno": -6, "errmsg": "token invalid"})
        return httpx.Response(
            200,
            json={
                "errno": 0,
                "list": [
                    {"server_filename": "a.mkv", "path": "/a.mkv",
                     "isdir": 0, "size": 123, "fs_id": 7},
                ],
            },
        )

    _patch(monkeypatch, handler)

    async def fake_refresh(refresh_token, client_id, client_secret):
        assert refresh_token == "rt-live"
        return {"access_token": "tok-new", "refresh_token": "rt-live",
                "expires_in": 2592000}

    from app.services.panlogin import baidu_openapi as login_mod

    monkeypatch.setattr(login_mod, "refresh", fake_refresh)

    storage = _store(
        access_token="tok-old", refresh_token="rt-live",
        client_id="ak", client_secret="sk",
    )
    files = run(storage.list_dir("/"))

    assert [f.name for f in files] == ["a.mkv"], "刷新后必须重试原请求"
    assert seen == ["tok-old", "tok-new"], f"实际调用序列 {seen}"
    # 新令牌必须回写内存配置，否则同一次调用链里后续请求还用旧的
    assert storage.access_token == "tok-new"


def test_refresh_is_attempted_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """坏 refresh_token 不能打成死循环（每次都失败 → 无限重试）。"""
    calls = {"http": 0, "refresh": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["http"] += 1
        return httpx.Response(200, json={"errno": -6})

    _patch(monkeypatch, handler)

    async def fake_refresh(*_args):
        calls["refresh"] += 1
        return {"access_token": "tok-still-bad"}

    from app.services.panlogin import baidu_openapi as login_mod

    monkeypatch.setattr(login_mod, "refresh", fake_refresh)

    storage = _store(
        access_token="tok-bad", refresh_token="rt", client_id="ak", client_secret="sk"
    )
    assert run(storage.list_dir("/")) == []
    assert calls["refresh"] == 1, "刷新只能试一次"
    assert calls["http"] == 2, f"最多一次重试，实际请求 {calls['http']} 次"


def test_refresh_gate_allows_only_one_attempt_per_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_refreshed_once`` 闸门本身要有测试。

    它与 ``_call(_retry=False)`` 是**两道**独立防线：去掉闸门后
    单次调用链仍被 ``_retry`` 挡住，所以注入验证里那条用例是绿的。
    但闸门守的是另一件事 —— 同一个实例被复用（``storages()`` 会缓存、
    保活任务一轮里会连着调好几个方法）时，坏 ``refresh_token``
    不该被反复送去刷新。这里直接调两次把它钉住。
    """
    attempts = {"n": 0}

    async def fake_refresh(*_args):
        attempts["n"] += 1
        return {"access_token": "tok-new"}

    from app.services.panlogin import baidu_openapi as login_mod

    monkeypatch.setattr(login_mod, "refresh", fake_refresh)
    storage = _store(
        access_token="tok-old", refresh_token="rt", client_id="ak", client_secret="sk"
    )
    assert run(storage._refresh_token()) is True
    assert run(storage._refresh_token()) is False, "同一实例不该刷新第二次"
    assert attempts["n"] == 1


def test_no_refresh_token_means_no_refresh_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有 refresh_token 就别去刷新，直接给出「重新授权」的明确指引。"""
    _patch(monkeypatch, lambda r: httpx.Response(200, json={"errno": -6}))
    storage = _store(access_token="tok-bad")
    assert run(storage.list_dir("/")) == []
    hint = storage._auth_hint("浏览")
    assert "没有 refresh_token" in hint and "重新授权" in hint


# ---------------------------------------------------------------------------
# 4. 失败必须可区分（ADR-82）
# ---------------------------------------------------------------------------


def test_missing_token_is_distinguishable_from_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """「没授权」「令牌废了」「连不上」三种失败要给三种不同的话。"""
    no_token = BaiduOpenApiStorage({"options": {}})
    assert "未完成百度开放平台授权" in no_token._auth_hint("浏览")

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    _patch(monkeypatch, boom)
    offline = _store()
    assert run(offline.list_dir("/")) == []
    assert offline._last_errno is None
    assert "连不上" in offline._auth_hint("浏览")


def test_keep_alive_reports_account_name_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """探活成功要带上账号名——否则无法确认授权的是哪个号。"""
    _patch(
        monkeypatch,
        lambda r: httpx.Response(200, json={"errno": 0, "baidu_name": "somebody"}),
    )
    ok, message = run(_store().keep_alive())
    assert ok is True and "somebody" in message


def test_keep_alive_without_token_does_not_pretend_success() -> None:
    ok, message = run(BaiduOpenApiStorage({"options": {}}).keep_alive())
    assert ok is False and "未完成开放平台授权" in message


# ---------------------------------------------------------------------------
# 5. 基本读写路径
# ---------------------------------------------------------------------------


def test_errno_zero_is_success_not_falsy(monkeypatch: pytest.MonkeyPatch) -> None:
    """``errno=0`` 是**成功**，不能被 `or` 当成假值吃掉。

    这是 ``_as_int`` 真正承载行为的地方（quota 那处 default 也是 0，
    ``int(x or 0)`` 与 ``_as_int(x,0)`` 恰好等价，所以那里换不出差异 ——
    注入验证实测确认过）。若默认值写成 -1 或用 ``or``，
    每一次成功响应都会被判成失败，整个 Provider 直接全哑。
    """
    _patch(
        monkeypatch,
        lambda r: httpx.Response(200, json={"errno": 0, "total": 100, "used": 0}),
    )
    quota = run(_store().quota())
    assert (quota.total, quota.used) == (100, 0), "errno=0 被当成失败了"


def test_response_without_errno_field_counts_as_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """响应里**没有** ``errno`` 字段时按成功处理。

    这是 ``_as_int(..., 0)`` 里那个默认值 ``0`` 唯一承载行为的场合 ——
    注入验证把它改成 ``-1`` 时全部用例仍然全绿，因为所有打桩响应都
    显式带了 ``errno``。而实测百度部分接口（``/api/quota``）成功时
    确实不回 ``errno``：默认值若是 -1，容量就永远显示 0，
    表现为「网盘接上了但容量为空」这种查不出原因的软故障。
    """
    _patch(
        monkeypatch,
        lambda r: httpx.Response(200, json={"total": 500, "used": 200}),
    )
    quota = run(_store().quota())
    assert (quota.total, quota.used) == (500, 200), "缺 errno 被误判成失败"


def test_quota_parses_zero_used_correctly(monkeypatch: pytest.MonkeyPatch) -> None:
    """used=0 是合法值（新号/空盘），不能变成"容量未知"。"""
    _patch(
        monkeypatch,
        lambda r: httpx.Response(
            200, json={"errno": 0, "total": 1000, "used": 0}
        ),
    )
    quota = run(_store().quota())
    assert (quota.total, quota.used, quota.free) == (1000, 0, 1000)


def test_list_dir_sorts_dirs_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        lambda r: httpx.Response(
            200,
            json={
                "errno": 0,
                "list": [
                    {"server_filename": "z.mkv", "path": "/z.mkv", "isdir": 0,
                     "size": 5, "fs_id": 1},
                    {"server_filename": "Movies", "path": "/Movies", "isdir": 1,
                     "fs_id": 2},
                ],
            },
        ),
    )
    files = run(_store().list_dir("/"))
    assert [f.name for f in files] == ["Movies", "z.mkv"]
    assert files[0].is_dir is True and files[0].size == 0


def _counting_ok(calls: list[str]):
    """永远成功的 handler，同时记录每一次请求。

    守卫类断言必须搭配「会成功的后端」：如果后端总是失败，那么
    *守卫生效* 与 *守卫失效但请求恰好失败* 两种情况都返回 False，
    测试就分不出来。注入验证实测过这个陷阱 —— 去掉根目录守卫后
    原来的 ``assert delete("/") is False`` **仍然全绿**，因为它没打桩，
    走的是真网络、必然失败。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={"errno": 0, "list": []})

    return handler


def test_rename_rejects_path_separator_without_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """新名字带 / 会变成「移动」，必须在本地就拒，且一个请求都不发。"""
    calls: list[str] = []
    _patch(monkeypatch, _counting_ok(calls))
    assert run(_store().rename("/a.mkv", "../../evil.mkv")) is False
    assert calls == [], f"守卫失效：把非法改名发给了百度 {calls}"


def test_delete_refuses_root_without_any_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删根目录=清空整个网盘。必须本地拒绝，绝不能把请求发出去。"""
    calls: list[str] = []
    _patch(monkeypatch, _counting_ok(calls))
    assert run(_store().delete("/")) is False
    assert calls == [], f"守卫失效：把「删除根目录」发给了百度 {calls}"


def test_delete_normal_path_does_reach_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """反向担保：守卫不能把正常删除也一起挡掉（否则删除功能等于没有）。"""
    calls: list[str] = []
    _patch(monkeypatch, _counting_ok(calls))
    assert run(_store().delete("/movies/a.mkv")) is True
    assert len(calls) == 1 and calls[0].startswith("POST"), calls


def test_no_token_short_circuits_without_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没令牌时不该发请求（省一次必然失败的往返）。"""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"errno": 0})

    _patch(monkeypatch, handler)
    assert run(BaiduOpenApiStorage({"options": {}}).list_dir("/")) == []
    assert calls["n"] == 0
