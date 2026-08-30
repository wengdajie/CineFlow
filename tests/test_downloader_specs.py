"""下载器配置字段清单的反向校验。

**这个测试存在的理由**：``downloader_specs`` 是"界面上能配哪些参数"的唯一来源，
而真正读取这些参数的是 ``app/providers/downloader/*.py``。两边一旦漂移，
用户就会遇到最难排查的一类 bug——**界面上填了、保存成功了、但根本不生效**。

所以这里不测"函数返回了什么"，而是去**源码里确认每个登记的字段真的被读取**。
新增下载器字段时如果忘了在 Provider 里读，这个测试会失败。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.providers.registry import get_provider_class, load_builtin_providers
from app.services import downloader_specs

DOWNLOADER_DIR = Path(__file__).resolve().parents[1] / "app" / "providers" / "downloader"

#: BaseProvider 已经把这两个作为公共属性处理，不在具体下载器源码里出现
_BASE_ATTRS = {"priority", "timeout"}


@pytest.fixture(scope="module", autouse=True)
def _providers() -> None:
    load_builtin_providers()


def _source_for(provider: str) -> str:
    """某下载器的源码 + 基类源码（option 可能在基类里被读）。"""
    name = "qbittorrent" if provider == "qbittorrent" else provider
    text = (DOWNLOADER_DIR / f"{name}.py").read_text(encoding="utf-8")
    return text + (DOWNLOADER_DIR / "base.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("provider", sorted(downloader_specs.PROVIDER_FIELDS))
def test_every_field_is_actually_read(provider: str) -> None:
    """登记的每个字段都必须真的被 Provider 读取，不允许假配置项。"""
    source = _source_for(provider)
    unread: list[str] = []
    for field in downloader_specs.fields_for(provider):
        key = field["key"]
        if key in _BASE_ATTRS:
            continue
        if field["target"] == "option":
            found = re.search(r'self\.option\(\s*"' + re.escape(key) + r'"', source)
        else:
            found = re.search(
                r'self\.config\.get\(\s*"' + re.escape(key) + r'"', source
            ) or re.search(r"self\." + re.escape(key) + r"\b", source)
        if not found:
            unread.append(f"{key}({field['target']})")
    assert not unread, (
        f"{provider} 登记了这些字段但源码里没读取，属于假配置项：{unread}"
    )


@pytest.mark.parametrize("provider", sorted(downloader_specs.PROVIDER_FIELDS))
def test_provider_is_registered_downloader(provider: str) -> None:
    """登记的 provider 名字必须真实存在且确实是下载器。"""
    cls = get_provider_class(provider)
    assert cls is not None, f"{provider} 未注册"
    assert cls.kind == "downloader", f"{provider} 不是下载器"


def test_ytdlp_has_no_remote_connection_fields() -> None:
    """yt-dlp 是本地进程，不该出现地址/用户名/密码这类远程连接字段。

    这条单独钉住是因为它最容易被"顺手加回来"——公共字段是默认加给所有
    下载器的，一旦有人删掉 EXCLUDED_COMMON 就会悄悄冒出来。
    """
    keys = {item["key"] for item in downloader_specs.fields_for("ytdlp")}
    assert not keys & {"url", "username", "password"}
    # 但它必须有 cookie_file——那才是 yt-dlp 真正的"登录"方式
    assert "cookie_file" in keys


def test_aria2_uses_api_key_not_username() -> None:
    """aria2 用 RPC Secret 认证，没有用户名。"""
    fields = {item["key"]: item for item in downloader_specs.fields_for("aria2")}
    assert "username" not in fields
    assert fields["api_key"]["target"] == "column"


def test_schema_covers_all_providers() -> None:
    """schema() 要把每个登记的下载器都吐出来，且带可读说明。"""
    items = downloader_specs.schema()
    assert {item["provider"] for item in items} == set(downloader_specs.PROVIDER_FIELDS)
    for item in items:
        assert item["display_name"], f"{item['provider']} 缺显示名"
        assert item["note"], f"{item['provider']} 缺用途说明"
        assert item["fields"], f"{item['provider']} 没有任何字段"


def test_field_types_are_supported() -> None:
    """字段类型必须是前端认识的那几种，且 choice 必须给 choices。"""
    allowed = {"str", "password", "int", "float", "bool", "list", "choice"}
    for provider in downloader_specs.PROVIDER_FIELDS:
        for field in downloader_specs.fields_for(provider):
            assert field["type"] in allowed, f"{provider}.{field['key']} 类型未支持"
            assert field["target"] in ("column", "option")
            assert field.get("label"), f"{provider}.{field['key']} 缺 label"
            if field["type"] == "choice":
                assert field.get("choices"), f"{provider}.{field['key']} 缺 choices"
