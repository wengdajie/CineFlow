"""百度网盘存储 Provider + 网盘服务层返回值解包（全程离线）。

这个文件回归的是本轮查出的三个真缺陷：

1. ``providers/panstorage/baidu.py`` **根本不存在** —— 百度扫码登录能成功建站点，
   但 ``create_provider("baidu")`` 返回 None，站点成了僵尸：总览查不到、
   转存/浏览/保活全部静默跳过，用户却看到"登录成功"。
2. ``_pick_for_share`` 的 hints 键写成 ``"115"``，而 Provider 名是 ``"pan115"``，
   115 分享永远匹配不到 115 网盘。
3. 115 的 ``rename/move/copy`` 返回 ``(bool, str)``，服务层按 ``bool`` 用，
   非空元组恒真 —— **失败被误报成成功**。
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.providers.panstorage.baidu import BaiduPanStorage
from app.providers.panstorage.local_dir import LocalDirStorage
from app.providers.panstorage.pan115 import Pan115Storage
from app.providers.registry import get_provider_class
from app.services import pan_storage as pan_service


def run(coro):
    return asyncio.run(coro)


def _patch(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    """把 baidu 模块与 utils.http 里创建的客户端都接到 MockTransport 上。"""

    def factory(timeout=None, headers=None, follow_redirects=True):
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers=headers or {},
            follow_redirects=follow_redirects,
        )

    import app.providers.panstorage.baidu as mod
    import app.utils.http as uh

    monkeypatch.setattr(mod, "async_client", factory)
    monkeypatch.setattr(uh, "async_client", factory)


def _api(payloads: dict[str, Any], calls: list[Any] | None = None):
    """按路径返回预设 JSON 的 handler。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append((request.method, request.url.path, dict(request.url.params)))
        body = payloads.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"errno": 404})
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)

    return handler


TOKEN_OK = {"errno": 0, "result": {"bdstoken": "TOKEN123"}}


# ------------------------------------------------ 注册与能力
def test_baidu_provider_is_registered():
    """没有这条，百度扫码登录建出来的站点就是僵尸站点。

    ⚠️ 只断言 ``get_provider_class("baidu")`` 是**不够**的：本文件顶部
    ``from ...baidu import BaiduPanStorage`` 本身就会触发 ``@register``，
    于是即使生产的加载清单里漏了它，这条也会通过。生产路径是
    ``load_builtin_providers()`` 按**显式模块清单**导入，漏登记就没人导入
    → ``create_provider("baidu")`` 返回 None → 僵尸站点复现。
    所以这里同时校验模块在清单里。
    """
    import inspect

    from app.providers.registry import load_builtin_providers

    source = inspect.getsource(load_builtin_providers)
    assert "app.providers.panstorage.baidu" in source, (
        "baidu 没登记进 load_builtin_providers 的模块清单，"
        "生产环境不会导入它，扫码登录会建出僵尸站点"
    )
    assert get_provider_class("baidu") is BaiduPanStorage


def test_baidu_site_is_usable_via_create_provider():
    """走生产入口 create_provider 必须能造出网盘实例（而不是 None）。"""
    from app.providers.panstorage.base import BasePanStorage
    from app.providers.registry import create_provider

    provider = create_provider("baidu", {"name": "百度网盘（扫码登录）", "cookie": "c"})
    assert isinstance(provider, BasePanStorage)


def test_baidu_declares_full_management():
    caps = BaiduPanStorage({}).capabilities()
    assert caps["rename"] and caps["move"] and caps["search"] and caps["keepalive"]


def test_baidu_without_cookie_degrades_not_raises():
    """没配 Cookie 时所有操作都要优雅降级，不能抛异常。"""
    store = BaiduPanStorage({})
    assert run(store.list_dir("/")) == []
    assert run(store.quota()).total == 0
    assert run(store.keep_alive())[0] is False
    assert run(store.search("x")) == []
    assert run(store.download_url("/a.mkv")) is None
    assert run(store.save_share("https://pan.baidu.com/s/1abc")).success is False


# ------------------------------------------------ 只读
def test_baidu_list_dir_parses_and_sorts(monkeypatch):
    """目录排在文件前；isdir/size/fs_id 都要正确取出。"""
    _patch(
        monkeypatch,
        _api(
            {
                "/api/list": {
                    "errno": 0,
                    "list": [
                        {"server_filename": "b.mkv", "path": "/b.mkv", "isdir": 0, "size": 12, "fs_id": 2},
                        {"server_filename": "剧集", "path": "/剧集", "isdir": 1, "fs_id": 1},
                    ],
                }
            }
        ),
    )
    store = BaiduPanStorage({"cookie": "BDUSS=x"})
    files = run(store.list_dir("/"))
    assert [f.name for f in files] == ["剧集", "b.mkv"]
    assert files[0].is_dir is True and files[1].size == 12
    assert files[1].file_id == "2"


def test_baidu_list_dir_derives_name_from_path(monkeypatch):
    """server_filename 缺失时要能从 path 兜出文件名，而不是整条丢掉。"""
    _patch(
        monkeypatch,
        _api({"/api/list": {"errno": 0, "list": [{"path": "/影视/x.mkv", "isdir": 0}]}}),
    )
    files = run(BaiduPanStorage({"cookie": "c"}).list_dir("/影视"))
    assert [f.name for f in files] == ["x.mkv"]


def test_baidu_quota(monkeypatch):
    _patch(monkeypatch, _api({"/api/quota": {"errno": 0, "total": 1000, "used": 250}}))
    quota = run(BaiduPanStorage({"cookie": "c"}).quota())
    assert (quota.total, quota.used, quota.percent) == (1000, 250, 25.0)


def test_baidu_errno_zero_is_success_not_falsy(monkeypatch):
    """``errno=0`` 是成功，但 0 在 Python 里是假值 —— 不能被 ``or`` 吃掉。"""
    _patch(monkeypatch, _api({"/api/quota": {"errno": 0, "total": 5, "used": 1}}))
    ok, message = run(BaiduPanStorage({"cookie": "c"}).keep_alive())
    assert ok is True, f"errno=0 被判成失败了：{message}"


def test_baidu_keep_alive_detects_expired_cookie(monkeypatch):
    _patch(monkeypatch, _api({"/api/quota": {"errno": -6}}))
    ok, message = run(BaiduPanStorage({"cookie": "stale"}).keep_alive())
    assert ok is False
    assert "过期" in message


# ------------------------------------------------ 写操作
def test_baidu_writes_carry_bdstoken(monkeypatch):
    """百度写操作缺 bdstoken 会直接 errno=-6，必须现取并带上。"""
    calls: list[Any] = []
    _patch(
        monkeypatch,
        _api(
            {
                "/api/gettemplatevariable": TOKEN_OK,
                "/api/filemanager": {"errno": 0},
            },
            calls,
        ),
    )
    assert run(BaiduPanStorage({"cookie": "c"}).rename("/a.mkv", "b.mkv")) is True
    tokens = [p.get("bdstoken") for m, path, p in calls if path == "/api/filemanager"]
    assert tokens == ["TOKEN123"]


def test_baidu_write_fails_without_token(monkeypatch):
    """取不到 bdstoken（Cookie 过期）时不能假装成功。"""
    _patch(monkeypatch, _api({"/api/gettemplatevariable": {"errno": -6}}))
    assert run(BaiduPanStorage({"cookie": "stale"}).rename("/a.mkv", "b.mkv")) is False


def test_baidu_rename_rejects_path_separators(monkeypatch):
    """带分隔符的"新名字"是移动而非改名，必须拒绝（与 local_dir 一致）。"""
    _patch(monkeypatch, _api({"/api/gettemplatevariable": TOKEN_OK, "/api/filemanager": {"errno": 0}}))
    store = BaiduPanStorage({"cookie": "c"})
    assert run(store.rename("/a.mkv", "../evil.mkv")) is False
    assert run(store.rename("/a.mkv", "sub/evil.mkv")) is False
    assert run(store.rename("/a.mkv", "sub\\evil.mkv")) is False
    assert run(store.rename("/a.mkv", "   ")) is False
    assert run(store.rename("/", "x.mkv")) is False


def test_baidu_refuses_to_delete_root(monkeypatch):
    """删根目录必须拒绝 —— 这是能一键清空整个网盘的操作。"""
    _patch(monkeypatch, _api({"/api/gettemplatevariable": TOKEN_OK, "/api/filemanager": {"errno": 0}}))
    assert run(BaiduPanStorage({"cookie": "c"}).delete("/")) is False


def test_baidu_filemanager_failure_is_reported(monkeypatch):
    """百度回 errno=-8（同名冲突）时要如实返回 False。"""
    _patch(monkeypatch, _api({"/api/gettemplatevariable": TOKEN_OK, "/api/filemanager": {"errno": -8}}))
    store = BaiduPanStorage({"cookie": "c"})
    assert run(store.rename("/a.mkv", "b.mkv")) is False
    assert run(store.move("/a.mkv", "/x")) is False
    assert run(store.copy("/a.mkv", "/x")) is False


def test_baidu_move_and_copy_bodies(monkeypatch):
    """move/copy 要带 dest + newname，否则百度会拒。"""
    sent: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/gettemplatevariable":
            return httpx.Response(200, json=TOKEN_OK)
        if request.url.path == "/api/filemanager":
            sent.append((dict(request.url.params).get("opera"), request.content.decode()))
            return httpx.Response(200, json={"errno": 0})
        return httpx.Response(404, json={"errno": 404})

    _patch(monkeypatch, handler)
    store = BaiduPanStorage({"cookie": "c"})
    assert run(store.move("/影视/a.mkv", "/归档")) is True
    assert run(store.copy("/影视/a.mkv", "/备份")) is True
    opera = [s[0] for s in sent]
    assert opera == ["move", "copy"]
    for _, body in sent:
        assert "a.mkv" in body


def test_baidu_search(monkeypatch):
    _patch(
        monkeypatch,
        _api(
            {
                "/api/search": {
                    "errno": 0,
                    "list": [{"server_filename": "凡人修仙传.mkv", "path": "/x/凡人修仙传.mkv", "isdir": 0, "size": 9}],
                }
            }
        ),
    )
    files = run(BaiduPanStorage({"cookie": "c"}).search("凡人修仙传"))
    assert [f.path for f in files] == ["/x/凡人修仙传.mkv"]


def test_baidu_search_empty_keyword():
    assert run(BaiduPanStorage({"cookie": "c"}).search("  ")) == []


def test_baidu_search_respects_limit(monkeypatch):
    _patch(
        monkeypatch,
        _api(
            {
                "/api/search": {
                    "errno": 0,
                    "list": [
                        {"server_filename": f"{i}.mkv", "path": f"/{i}.mkv", "isdir": 0}
                        for i in range(10)
                    ],
                }
            }
        ),
    )
    assert len(run(BaiduPanStorage({"cookie": "c"}).search("x", limit=3))) == 3


# ------------------------------------------------ 直链
def test_baidu_download_url_follows_dlink_with_netdisk_ua(monkeypatch):
    """dlink 用浏览器 UA 请求必 403，必须带 netdisk UA 并取 302 的 Location。

    只把 dlink 返回给下载器等于把失败推迟到下载阶段。
    """
    seen: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/filemetas":
            return httpx.Response(200, json={"errno": 0, "info": [{"dlink": "https://d.pcs.baidu.com/file/x"}]})
        seen.append(request.headers.get("user-agent", ""))
        return httpx.Response(302, headers={"location": "https://cdn.baidu.com/real.mkv"})

    _patch(monkeypatch, handler)
    url = run(BaiduPanStorage({"cookie": "c"}).download_url("/a.mkv"))
    assert url == "https://cdn.baidu.com/real.mkv"
    assert seen and "netdisk" in seen[0], f"取直链没带 netdisk UA：{seen}"


def test_baidu_download_url_falls_back_to_dlink(monkeypatch):
    """拿不到最终地址时退回 dlink，至少让调用方有东西可试。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/filemetas":
            return httpx.Response(200, json={"errno": 0, "info": [{"dlink": "https://d.pcs.baidu.com/file/x"}]})
        return httpx.Response(200)  # 没有 Location

    _patch(monkeypatch, handler)
    assert run(BaiduPanStorage({"cookie": "c"}).download_url("/a.mkv")) == "https://d.pcs.baidu.com/file/x"


def test_baidu_download_url_root_and_missing(monkeypatch):
    _patch(monkeypatch, _api({"/api/filemetas": {"errno": 0, "info": []}}))
    store = BaiduPanStorage({"cookie": "c"})
    assert run(store.download_url("/")) is None
    assert run(store.download_url("/a.mkv")) is None


# ------------------------------------------------ 分享码与转存
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://pan.baidu.com/s/1AbCdEf_x", "AbCdEf_x"),
        ("https://pan.baidu.com/share/init?surl=ZzZz9", "ZzZz9"),
        ("https://pan.baidu.com/s/1abc?pwd=1234", "abc"),
        ("https://pan.quark.cn/s/xxxx", ""),
        ("", ""),
    ],
)
def test_baidu_share_id_parsing(url, expected):
    assert BaiduPanStorage.parse_share_id(url) == expected


def test_baidu_save_share_rejects_foreign_link():
    result = run(BaiduPanStorage({"cookie": "c"}).save_share("https://pan.quark.cn/s/x"))
    assert result.success is False
    assert "百度网盘分享" in result.message


def test_baidu_save_share_reports_missing_params(monkeypatch):
    """分享页拿不到 shareid/uk 时要说清原因，不能假装成功。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/gettemplatevariable":
            return httpx.Response(200, json=TOKEN_OK)
        return httpx.Response(200, text="<html>链接已失效</html>")

    _patch(monkeypatch, handler)
    result = run(BaiduPanStorage({"cookie": "c"}).save_share("https://pan.baidu.com/s/1abc"))
    assert result.success is False
    assert "风控" in result.message or "失效" in result.message


def test_baidu_save_share_success(monkeypatch):
    """完整转存链路：token → 分享页取参数 → transfer errno=0。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/gettemplatevariable":
            return httpx.Response(200, json=TOKEN_OK)
        if path == "/s/1abc":
            return httpx.Response(
                200,
                text='var x = {"shareid":12345,"uk":67890,"list":[{"fs_id":"111"},{"fs_id":"222"}]}',
            )
        if path == "/share/transfer":
            return httpx.Response(200, json={"errno": 0})
        return httpx.Response(404, json={"errno": 404})

    _patch(monkeypatch, handler)
    result = run(
        BaiduPanStorage({"cookie": "c", "root_path": "/CineFlow"}).save_share(
            "https://pan.baidu.com/s/1abc"
        )
    )
    assert result.success is True
    assert result.saved_path == "/CineFlow"
    assert result.file_count == 2


def test_baidu_save_share_wrong_password(monkeypatch):
    """提取码错时 /share/verify 会非 0，要在校验阶段就拦下。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/gettemplatevariable":
            return httpx.Response(200, json=TOKEN_OK)
        if request.url.path == "/share/verify":
            return httpx.Response(200, json={"errno": -9})
        return httpx.Response(404, json={"errno": 404})

    _patch(monkeypatch, handler)
    result = run(
        BaiduPanStorage({"cookie": "c"}).save_share(
            "https://pan.baidu.com/s/1abc", password="0000"
        )
    )
    assert result.success is False
    assert "提取码" in result.message


@pytest.mark.parametrize(
    ("errno", "keyword"),
    [(-1, "失效"), (-6, "过期"), (-8, "同名"), (-10, "容量"), (-62, "风控")],
)
def test_baidu_transfer_errors_are_human_readable(errno, keyword):
    """把 errno 直接抛给用户毫无意义，必须翻成人话。"""
    message = BaiduPanStorage._transfer_error(errno, {})
    assert keyword in message
    assert str(errno) not in message or keyword in message


def test_baidu_unknown_transfer_error_keeps_errno():
    """未知 errno 要把原始码带出来，方便排查。"""
    message = BaiduPanStorage._transfer_error(9999, {"show_msg": "奇怪"})
    assert "9999" in message


# ------------------------------------------------ 服务层：分享归属
def test_pick_for_share_routes_115_link_to_115(monkeypatch):
    """hints 的键必须是 provider 名（``pan115``），写 ``115`` 会恒不匹配。"""
    # 诱饵必须排在前面且 supports_save=True：否则键名写错时
    # 兜底的 candidates[0] 仍会返回 pan115，测试就白测了
    fake = [
        BaiduPanStorage({"name": "百度", "cookie": "c"}),
        Pan115Storage({"name": "115 网盘", "cookie": "c"}),
    ]
    monkeypatch.setattr(pan_service, "storages", lambda **kw: fake)
    for url in ("https://115.com/s/abc", "https://115cdn.com/s/abc"):
        picked = pan_service._pick_for_share(url)
        assert picked.name == "pan115", (
            f"115 分享被分给了 {picked.name}：hints 的键必须是 provider 名 pan115"
        )


def test_pick_for_share_prefers_same_family(monkeypatch):
    """同家网盘优先：百度分享要给百度，而不是列表里第一个盘。"""
    fake = [
        Pan115Storage({"name": "115", "cookie": "c"}),
        BaiduPanStorage({"name": "百度", "cookie": "c"}),
    ]
    monkeypatch.setattr(pan_service, "storages", lambda **kw: fake)
    assert pan_service._pick_for_share("https://pan.baidu.com/s/1abc").name == "baidu"
    assert pan_service._pick_for_share("https://115.com/s/abc").name == "pan115"


def test_pick_for_share_skips_storages_without_save(monkeypatch):
    """不支持转存的盘（本地目录）不能被选中。"""
    monkeypatch.setattr(
        pan_service, "storages", lambda **kw: [LocalDirStorage({"name": "本地"})]
    )
    assert pan_service._pick_for_share("https://pan.baidu.com/s/1abc") is None


# ------------------------------------------------ 服务层：返回值解包
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ((False, "目标目录不存在"), (False, "目标目录不存在")),
        ((True, "已移动"), (True, "已移动")),
        (True, (True, "")),
        (False, (False, "")),
        ((False,), (False, "")),
        ((), (False, "")),
    ],
)
def test_unwrap_handles_both_shapes(raw, expected):
    assert pan_service._unwrap(raw) == expected


def test_service_does_not_report_115_failure_as_success(monkeypatch):
    """115 的 rename/move 返回 ``(False, msg)``，元组恒真会把失败报成成功。"""
    store = Pan115Storage({"name": "115", "cookie": ""})  # 空 Cookie => 必失败
    monkeypatch.setattr(pan_service, "get_storage", lambda sid: store)

    result = run(pan_service.rename_file(1, "/a.mkv", "b.mkv", file_id="1"))
    assert result["success"] is False, "115 改名失败被误报成成功"
    assert "已重命名" not in result["message"]

    moved = run(pan_service.move_file(1, "/a.mkv", "/x", file_id="1"))
    assert moved["success"] is False, "115 移动失败被误报成成功"
    assert "已移动" not in moved["message"]


def test_service_surfaces_provider_reason(monkeypatch):
    """Provider 给了具体原因时要透传，比笼统的"失败（检查权限）"有用。"""

    class Fussy(Pan115Storage):
        async def rename(self, path, new_name, *, file_id=None):
            return False, "115 拒绝了改名请求"

    monkeypatch.setattr(
        pan_service, "get_storage", lambda sid: Fussy({"name": "115", "cookie": "c"})
    )
    result = run(pan_service.rename_file(1, "/a.mkv", "b.mkv"))
    assert result["message"] == "115 拒绝了改名请求"
