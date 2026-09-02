"""网盘登录测试（扫码会话 / Cookie 导入 / 115 存储 Provider）。

全程离线。重点覆盖两类容易出事的地方：
1. **Cookie 绝不能泄漏到前端响应**（``to_dict`` 不含 cookie）
2. 校验不通过时**不能写库**（否则后续任务静默失败）
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select

from app.providers.panstorage.pan115 import Pan115Storage
from app.services import pan_login, panlogin
from app.services.panlogin import baidu as baidu_login
from app.services.panlogin import pan115 as pan115_login
from app.services.panlogin import quark as quark_login


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean():
    panlogin.reset_state()
    yield
    panlogin.reset_state()


@pytest.fixture
def cleanup_pan_sites():
    """把网盘站点表**快照-还原**，抹掉本用例造成的一切改动。

    ``apply_cookie`` 会往库里写/改一条**已启用**的网盘站点。测试库是 session 级
    共享的，留下痕迹会让别的用例（比如 ``/pan/save`` 的"无可用网盘 → 400"降级
    断言）拿到完全不同的结果——这种跨文件的隐式依赖排查起来最费时间。

    这里不再"按 provider 删几条"：那种写法漏掉任何一个 provider 就会留下
    一个**已启用**的网盘，让后续用例莫名其妙地失败（实测被 quark 坑过一次）。
    改成整表快照 + 还原，新增网盘时零维护。
    """
    from app.db.models import SiteConfig
    from app.db.session import session_scope
    from app.schemas.enums import ProviderKind

    def snapshot():
        with session_scope() as session:
            return {
                row.id: {
                    "name": row.name,
                    "provider": row.provider,
                    "url": row.url,
                    "enabled": row.enabled,
                    "cookie": row.cookie,
                    "options": dict(row.options or {}),
                    "last_status": row.last_status,
                }
                for row in session.execute(
                    select(SiteConfig).where(
                        SiteConfig.kind == ProviderKind.PANSTORAGE.value
                    )
                ).scalars()
            }

    before = snapshot()
    yield
    with session_scope() as session:
        rows = session.execute(
            select(SiteConfig).where(
                SiteConfig.kind == ProviderKind.PANSTORAGE.value
            )
        ).scalars().all()
        for row in rows:
            original = before.get(row.id)
            if original is None:
                session.delete(row)  # 用例新建的，删掉
                continue
            for field, value in original.items():
                setattr(row, field, value)


class _FakeResponse:
    def __init__(self, payload: Any = None, text: str = "", status: int = 200):
        self._payload = payload
        self.text = text
        self.status_code = status

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    def __init__(self, handler):
        self._handler = handler
        self.cookies = _FakeJar()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url: str, **kwargs):
        return self._handler("GET", url, kwargs)

    async def post(self, url: str, **kwargs):
        return self._handler("POST", url, kwargs)


class _FakeCookie:
    def __init__(self, name, value):
        self.name = name
        self.value = value


class _FakeJar:
    def __init__(self):
        self.jar: list[_FakeCookie] = []


# ---------------- 会话安全 ----------------
def test_session_dict_never_leaks_cookie():
    """扫码会话序列化给前端时**绝不能**带 cookie。"""
    s = panlogin.LoginSession(token="t", provider="pan115")
    s.cookie = "SUPER_SECRET=1"
    view = s.to_dict()
    assert "cookie" not in view
    assert "SUPER_SECRET" not in str(view)


def test_session_expiry():
    s = panlogin.LoginSession(token="t", provider="pan115")
    assert s.expired is False
    s.created_at -= panlogin.SESSION_TTL + 10
    assert s.expired is True


def test_session_store_roundtrip():
    s = panlogin.LoginSession(token="tok", provider="pan115")
    run(panlogin.put_session(s))
    assert run(panlogin.get_session("tok")) is s
    run(panlogin.drop_session("tok"))
    assert run(panlogin.get_session("tok")) is None


def test_expired_session_is_garbage_collected():
    s = panlogin.LoginSession(token="old", provider="pan115")
    s.created_at -= panlogin.SESSION_TTL + 10
    run(panlogin.put_session(s))
    assert run(panlogin.get_session("old")) is None


# ---------------- 115 扫码 ----------------
def test_115_start_parses_token(monkeypatch):
    payload = {
        "state": 1,
        "data": {
            "uid": "abc123",
            "time": 1787991301,
            "sign": "sig",
            "qrcode": "https://115.com/scan/dg-abc123",
        },
    }
    monkeypatch.setattr(
        pan115_login, "async_client", lambda **kw: _FakeClient(lambda *a: _FakeResponse(payload))
    )
    s = run(pan115_login.start())
    assert s.status == "waiting"
    assert s.extra["uid"] == "abc123"
    assert "qrcode?uid=abc123" in s.qr_image


def test_115_start_handles_bad_shape(monkeypatch):
    monkeypatch.setattr(
        pan115_login, "async_client", lambda **kw: _FakeClient(lambda *a: _FakeResponse({"state": 0}))
    )
    s = run(pan115_login.start())
    assert s.status == "failed"
    assert "二维码" in s.message


def test_115_poll_waiting(monkeypatch):
    monkeypatch.setattr(
        pan115_login,
        "async_client",
        lambda **kw: _FakeClient(lambda *a: _FakeResponse({"data": {"status": 0}})),
    )
    s = panlogin.LoginSession(token="t", provider="pan115", extra={"uid": "u", "time": 1, "sign": "s"})
    out = run(pan115_login.poll(s))
    assert out.status == "waiting"


def test_115_poll_scanned(monkeypatch):
    monkeypatch.setattr(
        pan115_login,
        "async_client",
        lambda **kw: _FakeClient(lambda *a: _FakeResponse({"data": {"status": 1}})),
    )
    s = panlogin.LoginSession(token="t", provider="pan115", extra={"uid": "u", "time": 1, "sign": "s"})
    out = run(pan115_login.poll(s))
    assert out.status == "scanned"


def test_115_poll_confirmed_exchanges_cookie(monkeypatch):
    def handler(method, url, kwargs):
        if "get/status" in url:
            return _FakeResponse({"data": {"status": 2}})
        return _FakeResponse({"data": {"cookie": {"UID": "1", "CID": "2"}, "user_id": 9}})

    monkeypatch.setattr(pan115_login, "async_client", lambda **kw: _FakeClient(handler))
    s = panlogin.LoginSession(token="t", provider="pan115", extra={"uid": "u", "time": 1, "sign": "s"})
    out = run(pan115_login.poll(s))
    assert out.status == "success"
    assert "UID=1" in out.cookie and "CID=2" in out.cookie


def test_115_poll_cancelled(monkeypatch):
    monkeypatch.setattr(
        pan115_login,
        "async_client",
        lambda **kw: _FakeClient(lambda *a: _FakeResponse({"data": {"status": -2}})),
    )
    s = panlogin.LoginSession(token="t", provider="pan115", extra={"uid": "u", "time": 1, "sign": "s"})
    out = run(pan115_login.poll(s))
    assert out.status == "expired"


def test_115_verify_translates_html_to_readable(monkeypatch):
    """Cookie 失效时 115 返回登录页 HTML；错误消息必须可读而不是 JSON 解析错。"""
    monkeypatch.setattr(
        pan115_login,
        "async_client",
        lambda **kw: _FakeClient(lambda *a: _FakeResponse(None, text="<html>login</html>")),
    )
    ok, msg, _ = run(pan115_login.verify("X=1"))
    assert ok is False
    assert "过期" in msg or "无效" in msg
    assert "Expecting value" not in msg


def test_115_verify_empty_cookie():
    ok, msg, _ = run(pan115_login.verify(""))
    assert ok is False and "为空" in msg


def test_115_verify_success(monkeypatch):
    monkeypatch.setattr(
        pan115_login,
        "async_client",
        lambda **kw: _FakeClient(lambda *a: _FakeResponse({"state": True, "count": 12})),
    )
    ok, _msg, extra = run(pan115_login.verify("X=1"))
    assert ok is True and extra["count"] == 12


# ---------------- 百度扫码 ----------------
def test_baidu_start(monkeypatch):
    monkeypatch.setattr(
        baidu_login,
        "async_client",
        lambda **kw: _FakeClient(lambda *a: _FakeResponse({"errno": 0, "sign": "sg1"})),
    )
    s = run(baidu_login.start())
    assert s.status == "waiting"
    assert s.extra["sign"] == "sg1"
    assert "sign=sg1" in s.qr_image


def test_baidu_extract_bduss_double_json():
    """unicast 返回的是双层 JSON（内层被当字符串塞进外层）。"""
    text = '{"channel_v":"{\\"status\\":0,\\"v\\":\\"TICKET123\\"}"}'
    assert baidu_login._extract_bduss(text) == "TICKET123"


def test_baidu_extract_bduss_regex_fallback():
    """格式微调时退回正则，不能完全失效。"""
    assert baidu_login._extract_bduss('garbage "v":"TK9" tail') == "TK9"


def test_baidu_extract_bduss_none():
    assert baidu_login._extract_bduss("") == ""
    assert baidu_login._extract_bduss('{"channel_v":"{\\"status\\":1}"}') == ""


def test_baidu_verify_success(monkeypatch):
    monkeypatch.setattr(
        baidu_login,
        "async_client",
        lambda **kw: _FakeClient(
            lambda *a: _FakeResponse({"errno": 0, "total": 100, "used": 20})
        ),
    )
    ok, _msg, extra = run(baidu_login.verify("BDUSS=x"))
    assert ok is True and extra["total"] == 100


def test_baidu_verify_bad_errno(monkeypatch):
    monkeypatch.setattr(
        baidu_login,
        "async_client",
        lambda **kw: _FakeClient(lambda *a: _FakeResponse({"errno": -6})),
    )
    ok, msg, _ = run(baidu_login.verify("BDUSS=x"))
    assert ok is False and "-6" in msg


# ---------------- 夸克（只导 Cookie） ----------------
def test_quark_verify_detects_logged_out(monkeypatch):
    monkeypatch.setattr(
        quark_login,
        "async_client",
        lambda **kw: _FakeClient(
            lambda *a: _FakeResponse({"code": 31001, "message": "require login"}, status=401)
        ),
    )
    ok, msg, _ = run(quark_login.verify("x=1"))
    assert ok is False and "过期" in msg


def test_quark_verify_success(monkeypatch):
    monkeypatch.setattr(
        quark_login,
        "async_client",
        lambda **kw: _FakeClient(
            lambda *a: _FakeResponse({"status": 200, "data": {"nickname": "阿明"}})
        ),
    )
    ok, _msg, extra = run(quark_login.verify("x=1"))
    assert ok is True and extra["nickname"] == "阿明"


# ---------------- 编排层 ----------------
def test_providers_declare_capabilities():
    """夸克必须声明为不支持扫码——前端据此渲染，不在前端写死。"""
    provs = {p["provider"]: p for p in pan_login.providers()}
    assert provs["pan115"]["qrcode"] is True
    assert provs["baidu"]["qrcode"] is True
    assert provs["quark"]["qrcode"] is False
    assert all(p["cookie"] for p in provs.values())


def test_start_qrcode_rejects_unsupported():
    result = run(pan_login.start_qrcode("quark"))
    assert result["success"] is False
    assert "不支持扫码" in result["message"]


def test_start_qrcode_rejects_unknown_provider():
    result = run(pan_login.start_qrcode("nosuchpan"))
    assert result["success"] is False


def test_poll_unknown_token():
    result = run(pan_login.poll_qrcode("nope"))
    assert result["success"] is False


def test_poll_returns_expired_for_stale_session():
    s = panlogin.LoginSession(token="t2", provider="pan115")
    s.created_at -= panlogin.SESSION_TTL + 5
    panlogin._SESSIONS["t2"] = s  # 直接塞，绕过 gc
    result = run(pan_login.poll_qrcode("t2"))
    assert result["data"]["status"] == "expired"
    # 过期提示只给一次，之后会话就该没了（凭据不留在内存里过夜）
    assert run(panlogin.peek_session("t2")) is None


def test_apply_cookie_refuses_when_verify_fails(monkeypatch):
    """校验不过就**不能写库**——写进去只会让后续任务静默失败。"""

    async def bad_verify(cookie):
        return False, "Cookie 无效", {}

    monkeypatch.setattr(quark_login, "verify", bad_verify)
    result = run(pan_login.apply_cookie("quark", "x=1", verify=True))
    assert result["success"] is False
    assert "未保存" in result["message"]


def test_apply_cookie_rejects_empty():
    result = run(pan_login.apply_cookie("quark", "   ", verify=False))
    assert result["success"] is False


def test_apply_cookie_reuses_seed_site(client, cleanup_pan_sites):
    """有同 provider 的站点（含内置预设）时**复用**它，不再建重复的一条。

    v1.16.0 起 ``pan115`` / ``baidu`` 有了内置预设站点，所以这里命中的是
    "更新已有记录"这条路径 —— 这正是期望行为：否则用户扫码一次就多出一条
    同名网盘，站点列表越用越脏。
    """
    result = run(
        pan_login.apply_cookie(
            "pan115", "UID=abc", site_name="测试115", verify=False
        )
    )
    assert result["success"] is True
    assert result["data"]["created"] is False, "有预设站点时不该另建一条"
    site_id = result["data"]["site_id"]

    # 第二次仍然更新同一条
    again = run(pan_login.apply_cookie("pan115", "UID=def", verify=False))
    assert again["data"]["created"] is False
    assert again["data"]["site_id"] == site_id

    # 凭据确实写进去了，且站点被启用（否则登录了却不生效）
    from app.db.models import SiteConfig
    from app.db.session import session_scope

    with session_scope() as session:
        site = session.get(SiteConfig, site_id)
        assert site.cookie == "UID=def"
        assert (site.options or {}).get("cookie") == "UID=def"
        assert site.enabled is True


def test_apply_cookie_creates_site_when_absent(client, cleanup_pan_sites):
    """没有任何同 provider 站点时才新建（用一个不存在预设的 provider 验证）。"""
    from app.db.models import SiteConfig
    from app.db.session import session_scope

    # 先把 quark 预设站点挪走，制造"无同 provider 站点"的场景
    with session_scope() as session:
        rows = session.execute(
            select(SiteConfig).where(SiteConfig.provider == "quark")
        ).scalars().all()
        moved = [(row.id, row.provider) for row in rows]
        for row in rows:
            row.provider = "quark_parked"
    try:
        result = run(
            pan_login.apply_cookie(
                "quark", "cookie=abc", site_name="新建夸克", verify=False
            )
        )
        assert result["success"] is True
        assert result["data"]["created"] is True
        assert result["data"]["site_name"] == "新建夸克"
    finally:
        # 还原由 cleanup_pan_sites 的整表快照负责，这里只保证异常也能走到它
        del moved


def test_complete_qrcode_requires_success_state():
    s = panlogin.LoginSession(token="t3", provider="pan115")
    run(panlogin.put_session(s))
    result = run(pan_login.complete_qrcode("t3"))
    assert result["success"] is False
    assert "尚未登录" in result["message"]


def test_complete_qrcode_writes_and_drops_session(client, cleanup_pan_sites):
    s = panlogin.LoginSession(token="t4", provider="pan115")
    s.status = "success"
    s.cookie = "UID=zzz"
    run(panlogin.put_session(s))
    result = run(pan_login.complete_qrcode("t4", site_name="扫码建的115"))
    assert result["success"] is True
    # 成功后会话必须销毁，避免 Cookie 留在内存里
    assert run(panlogin.get_session("t4")) is None


def test_verify_cookie_unknown_provider():
    result = run(pan_login.verify_cookie("nope", "x"))
    assert result["success"] is False


# ---------------- 回归：对外接口不得开放跳过校验的口子 ----------------
def test_cookie_import_schema_has_no_verify_switch():
    """``PanCookieImportRequest`` **不能**有 ``verify`` 字段。

    这是冒烟测试抓到的真实漏洞：带上 ``verify=false`` 就能把任意字符串
    （实测 ``x=1``）当 Cookie 写进站点记录并启用该站点，等于把 ADR-40
    「校验不过不写库」整条绕过去。服务层的 verify 参数只给扫码流程内部用。
    """
    from app.schemas.models import PanCookieImportRequest

    assert "verify" not in PanCookieImportRequest.model_fields


def test_cookie_import_route_forces_verify():
    """路由必须写死 ``verify=True``，不能从请求体里取。"""
    import inspect

    from app.api.routers import pan as pan_router

    source = inspect.getsource(pan_router.login_cookie)
    assert "verify=True" in source
    assert "payload.verify" not in source


# ---------------- 115 存储 Provider ----------------
def _storage(**options):
    return Pan115Storage({"name": "115", "provider": "pan115", "options": options})


def test_pan115_capabilities_full():
    caps = _storage().capabilities()
    assert caps["rename"] and caps["move"] and caps["search"] and caps["keepalive"]


def test_pan115_share_id_parsing():
    assert Pan115Storage.parse_share_id("https://115.com/s/swzq1a83z0v?password=b1b2") == "swzq1a83z0v"
    assert Pan115Storage.parse_share_id("https://pan.quark.cn/s/abc") == ""


def test_pan115_rename_rejects_separator():
    """带路径分隔符等于偷偷移动，可能越界，必须拒绝。"""
    ok, msg = run(_storage(cookie="x").rename("/a.mkv", "../b.mkv"))
    assert ok is False
    assert "分隔符" in msg


def test_pan115_keepalive_without_cookie():
    ok, msg = run(_storage().keep_alive())
    assert ok is False and "Cookie" in msg


def test_pan115_save_share_rejects_bad_url():
    result = run(_storage(cookie="x").save_share("https://example.com/s/x"))
    assert result.success is False
    assert "有效" in result.message


def test_pan115_save_share_needs_cookie():
    result = run(_storage().save_share("https://115.com/s/abc"))
    assert result.success is False
    assert "Cookie" in result.message


def test_pan115_parse_files_distinguishes_dir():
    """115 用 fid 区分文件与目录：目录项没有 fid。"""
    files = Pan115Storage._parse_files(
        [
            {"n": "电影", "cid": "100"},
            {"n": "a.mkv", "fid": "200", "s": 1024},
            {"n": ""},
        ]
    )
    assert len(files) == 2
    assert files[0].is_dir is True and files[0].file_id == "100"
    assert files[1].is_dir is False and files[1].size == 1024
