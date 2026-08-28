"""ChatOps 测试：指令解析、三平台验签、会话上下文、Webhook 全链路（全程离线）。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from typing import Any

import pytest

from app.services.chatops import adapters as chat_adapters
from app.services.chatops import commands as command_parser
from app.services.chatops import service as chat_service
from app.services.chatops.adapters import (
    ChatAdapter,
    DingTalkAdapter,
    FeishuAdapter,
    InboundMessage,
    TelegramAdapter,
    get_adapter,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def clean_sessions():
    """每个用例都从干净的幂等/会话缓存开始，避免互相污染。"""
    chat_service.clear_sessions()
    yield
    chat_service.clear_sessions()


def msg(text: str, **kwargs: Any) -> InboundMessage:
    """构造一条测试用入站消息（message_id 随机化避免幂等误伤）。"""
    defaults = {
        "platform": "console",
        "user_id": "u1",
        "chat_id": "c1",
        "message_id": f"m-{text}-{time.time_ns()}",
    }
    defaults.update(kwargs)
    return InboundMessage(text=text, **defaults)


# ---------------------------------------------------------------- 指令解析
@pytest.mark.parametrize(
    "text,name",
    [
        ("搜索 庆余年", "search"),
        ("搜 沙丘", "search"),
        ("查找 三体", "search"),
        ("/search Dune", "search"),
        ("s Dune", "search"),
        ("下载 2", "download"),
        ("dl 3", "download"),
        ("订阅 凡人修仙传", "subscribe"),
        ("追剧 苍兰诀", "subscribe"),
        ("订阅列表", "subscribes"),
        ("subs", "subscribes"),
        ("状态", "status"),
        ("进度", "status"),
        ("转存", "transfer"),
        ("热榜", "trending"),
        ("帮助", "help"),
        ("？", "help"),
    ],
)
def test_parse_aliases(text, name):
    assert command_parser.parse(text).name == name


def test_parse_empty_is_not_ok():
    assert command_parser.parse("").ok is False
    assert command_parser.parse("   ").ok is False
    assert command_parser.parse("@机器人").ok is False


def test_parse_strips_mention_and_slash():
    command = command_parser.parse("@CineFlowBot /搜索 庆余年")
    assert command.name == "search"
    assert command.argument == "庆余年"


def test_parse_colon_syntax():
    assert command_parser.parse("搜索:庆余年").argument == "庆余年"
    assert command_parser.parse("搜索：庆余年").argument == "庆余年"


def test_parse_season_chinese_and_sxx():
    a = command_parser.parse("搜索 凡人修仙传 第二季")
    assert (a.argument, a.season) == ("凡人修仙传", 2)
    b = command_parser.parse("订阅 庆余年 第2季")
    assert (b.argument, b.season) == ("庆余年", 2)
    c = command_parser.parse("搜索 Dune S02")
    assert c.season == 2
    d = command_parser.parse("搜索 苍兰诀 第十季")
    assert d.season == 10


def test_parse_episode():
    command = command_parser.parse("搜索 庆余年 第二季 第5集")
    assert command.season == 2 and command.episode == 5
    assert command.argument == "庆余年"
    assert command_parser.parse("搜索 Dune S01E09").episode == 9


def test_parse_bare_number_is_download():
    """纯数字兜底成「下载 N」，方便用户直接回数字选条目。"""
    command = command_parser.parse("2")
    assert command.name == "download" and command.index == 2


def test_parse_bare_title_is_search():
    """没有指令词就当搜索，降低使用门槛。"""
    command = command_parser.parse("庆余年 第二季")
    assert command.name == "search" and command.argument == "庆余年 第二季"


def test_parse_download_link_goes_to_argument():
    command = command_parser.parse("下载 magnet:?xt=urn:btih:abc")
    assert command.name == "download"
    assert command.argument.startswith("magnet:")
    assert command.index is None


def test_help_text_covers_all_commands():
    """帮助文本必须覆盖所有规范指令，避免出现"能用但没人知道"的指令。"""
    text = command_parser.HELP_TEXT
    for word in ("搜索", "下载", "订阅", "订阅列表", "状态", "转存", "热榜", "帮助"):
        assert word in text


# ---------------------------------------------------------------- 飞书验签
def test_feishu_verify_v1_and_v2_token():
    adapter = FeishuAdapter({"verification_token": "VT"})
    ok, _ = adapter.verify(headers={}, body=b"", payload={"token": "VT"})
    assert ok is True
    ok, _ = adapter.verify(headers={}, body=b"", payload={"header": {"token": "VT"}})
    assert ok is True
    ok, reason = adapter.verify(headers={}, body=b"", payload={"token": "WRONG"})
    assert ok is False and "token" in reason


def test_feishu_verify_rejects_when_unconfigured():
    """未配密钥默认拒绝——webhook 不走 JWT，放行等于裸奔。"""
    ok, reason = FeishuAdapter({}).verify(headers={}, body=b"", payload={})
    assert ok is False
    assert "allow_unverified" in reason


def test_feishu_allow_unverified_escape_hatch():
    ok, _ = FeishuAdapter({"allow_unverified": 1}).verify(headers={}, body=b"", payload={})
    assert ok is True


def test_feishu_url_verification_challenge():
    adapter = FeishuAdapter({"verification_token": "VT"})
    message = adapter.parse({"type": "url_verification", "challenge": "abc", "token": "VT"})
    assert message.challenge == {"challenge": "abc"}
    assert message.actionable is False


def test_feishu_parse_message_content_json():
    adapter = FeishuAdapter({"verification_token": "VT"})
    payload = {
        "header": {"token": "VT"},
        "event": {
            "message": {
                "content": json.dumps({"text": "搜索 庆余年"}),
                "chat_id": "oc_1",
                "message_id": "om_1",
            },
            "sender": {"sender_id": {"open_id": "ou_1"}},
        },
    }
    message = adapter.parse(payload)
    assert message.text == "搜索 庆余年"
    assert message.user_id == "ou_1"
    assert message.chat_id == "oc_1"
    assert message.actionable is True


def test_feishu_decrypt_roundtrip():
    """用同样的算法加密再让适配器解密，确认加密模式可用。"""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = "my-encrypt-key"
    plain = json.dumps({"type": "url_verification", "challenge": "xyz"}).encode()
    padding = 16 - len(plain) % 16
    padded = plain + bytes([padding]) * padding
    iv = b"0123456789abcdef"
    digest = hashlib.sha256(key.encode()).digest()
    encryptor = Cipher(algorithms.AES(digest), modes.CBC(iv)).encryptor()
    cipher_text = encryptor.update(padded) + encryptor.finalize()
    encoded = base64.b64encode(iv + cipher_text).decode()

    adapter = FeishuAdapter({"encrypt_key": key, "verification_token": "VT"})
    assert adapter.decrypt(encoded) == {"type": "url_verification", "challenge": "xyz"}
    message = adapter.parse({"encrypt": encoded})
    assert message.challenge == {"challenge": "xyz"}


def test_feishu_decrypt_bad_input_returns_none():
    adapter = FeishuAdapter({"encrypt_key": "k"})
    assert adapter.decrypt("not-base64!!") is None
    assert FeishuAdapter({}).decrypt("whatever") is None


# ---------------------------------------------------------------- 钉钉验签
def _dingtalk_sign(secret: str, timestamp: str) -> str:
    digest = hmac.new(
        secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode()


def test_dingtalk_verify_valid_signature():
    secret = "SEC123"
    timestamp = str(int(time.time() * 1000))
    adapter = DingTalkAdapter({"app_secret": secret})
    ok, _ = adapter.verify(
        headers={"timestamp": timestamp, "sign": _dingtalk_sign(secret, timestamp)},
        body=b"{}",
        payload={},
    )
    assert ok is True


def test_dingtalk_verify_bad_signature():
    timestamp = str(int(time.time() * 1000))
    adapter = DingTalkAdapter({"app_secret": "SEC123"})
    ok, reason = adapter.verify(
        headers={"timestamp": timestamp, "sign": "wrong"}, body=b"{}", payload={}
    )
    assert ok is False and "签名" in reason


def test_dingtalk_verify_missing_headers():
    ok, reason = DingTalkAdapter({"app_secret": "S"}).verify(headers={}, body=b"", payload={})
    assert ok is False and "timestamp" in reason


def test_dingtalk_verify_replay_protection():
    """老时间戳的请求要被当成重放拒绝。"""
    secret = "SEC123"
    old = str(int((time.time() - 7200) * 1000))
    adapter = DingTalkAdapter({"app_secret": secret})
    ok, reason = adapter.verify(
        headers={"timestamp": old, "sign": _dingtalk_sign(secret, old)},
        body=b"{}",
        payload={},
    )
    assert ok is False and "重放" in reason


def test_dingtalk_verify_bad_timestamp_format():
    adapter = DingTalkAdapter({"app_secret": "S"})
    ok, reason = adapter.verify(
        headers={"timestamp": "not-a-number", "sign": "x"}, body=b"", payload={}
    )
    assert ok is False and "timestamp" in reason


def test_dingtalk_parse():
    message = DingTalkAdapter({}).parse(
        {
            "text": {"content": " 搜索 沙丘 "},
            "senderStaffId": "staff-1",
            "senderNick": "张三",
            "conversationId": "cid",
            "msgId": "mid",
            "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=x",
        }
    )
    assert message.text.strip() == "搜索 沙丘"
    assert message.user_id == "staff-1" and message.user_name == "张三"
    assert message.raw["sessionWebhook"].startswith("https://")


# ---------------------------------------------------------------- Telegram
def test_telegram_verify_secret_token():
    adapter = TelegramAdapter({"secret_token": "ST"})
    ok, _ = adapter.verify(
        headers={"X-Telegram-Bot-Api-Secret-Token": "ST"}, body=b"", payload={}
    )
    assert ok is True
    ok, reason = adapter.verify(
        headers={"x-telegram-bot-api-secret-token": "BAD"}, body=b"", payload={}
    )
    assert ok is False and "secret token" in reason
    ok, _ = adapter.verify(headers={}, body=b"", payload={})
    assert ok is False


def test_telegram_parse_variants():
    adapter = TelegramAdapter({})
    for key in ("message", "edited_message", "channel_post"):
        message = adapter.parse(
            {key: {"text": "状态", "chat": {"id": 42}, "from": {"id": 7, "username": "bob"}, "message_id": 9}}
        )
        assert message.text == "状态"
        assert message.chat_id == "42" and message.user_id == "7"
        assert message.user_name == "bob"


# ---------------------------------------------------------------- 适配器注册
def test_get_adapter_and_list_platforms():
    assert isinstance(get_adapter("feishu"), FeishuAdapter)
    assert isinstance(get_adapter("DingTalk"), DingTalkAdapter)
    assert isinstance(get_adapter("telegram"), TelegramAdapter)
    assert get_adapter("wechat") is None

    platforms = chat_adapters.list_platforms()
    assert {item["platform"] for item in platforms} == {"feishu", "dingtalk", "telegram"}
    for item in platforms:
        assert item["setup_hint"], item["platform"]
        assert item["fields"], item["platform"]
        # 每个平台都要有验签相关字段，否则前端无法引导用户配置
        assert any(field.get("secret") for field in item["fields"])


def test_dedupe_key_stable_and_distinct():
    a = msg("状态", message_id="fixed")
    b = InboundMessage(platform="console", text="状态", user_id="u1", chat_id="c1", message_id="fixed")
    assert ChatAdapter.dedupe_key(a) == ChatAdapter.dedupe_key(b)
    c = msg("状态", message_id="other")
    assert ChatAdapter.dedupe_key(a) != ChatAdapter.dedupe_key(c)


# ---------------------------------------------------------------- 执行引擎
def test_handle_message_help(client):
    result = run(chat_service.handle_message(msg("帮助")))
    assert result["handled"] is True
    assert "指令帮助" in result["reply"]


def test_handle_message_status(client):
    result = run(chat_service.handle_message(msg("状态")))
    assert result["command"] == "status"
    assert "累计已完成" in result["reply"]


def test_handle_message_subscribes(client):
    result = run(chat_service.handle_message(msg("订阅列表")))
    assert result["command"] == "subscribes"
    # 有订阅时回「追剧中 N 部」，没有时回引导语，两者都算正常
    assert "追剧中" in result["reply"] or "还没有追剧中的订阅" in result["reply"]


def test_handle_message_trending(client):
    result = run(chat_service.handle_message(msg("热榜")))
    assert result["command"] == "trending"
    assert result["reply"]


def test_handle_message_empty_is_ignored(client):
    result = run(chat_service.handle_message(msg("")))
    assert result["handled"] is False
    assert result["reason"] == "非指令消息"


def test_handle_message_idempotent(client):
    """平台重试同一条消息只执行一次。"""
    message = msg("状态", message_id="dup-1")
    first = run(chat_service.handle_message(message))
    second = run(chat_service.handle_message(message))
    assert first["handled"] is True
    assert second["handled"] is False
    assert "重复" in second["reason"]


def test_handle_message_disabled(client, monkeypatch):
    monkeypatch.setattr(chat_service, "get_config", lambda: {"enabled": False})
    result = run(chat_service.handle_message(msg("状态")))
    assert result["handled"] is False and "停用" in result["reason"]


def test_handle_message_whitelist_blocks(client, monkeypatch):
    base = chat_service.default_config()
    monkeypatch.setattr(
        chat_service, "get_config", lambda: {**base, "allow_users": ["allowed-user"]}
    )
    blocked = run(chat_service.handle_message(msg("状态", user_id="stranger")))
    assert blocked["handled"] is True
    assert "白名单" in blocked["reply"]

    allowed = run(chat_service.handle_message(msg("状态", user_id="allowed-user")))
    assert "累计已完成" in allowed["reply"]


def test_search_then_download_uses_session(client, monkeypatch):
    """会话上下文：搜索后回「下载 2」应命中第 2 条。"""
    from app.services import download as download_service
    from app.services import search as search_service

    fake_results = [
        {"title": "剧集 S01E01 1080p", "link": "magnet:?xt=1", "kind": "magnet", "site": "站A", "size": 100},
        {"title": "剧集 S01E02 2160p", "link": "magnet:?xt=2", "kind": "magnet", "site": "站B", "size": 200},
    ]

    async def fake_search(keyword, **kwargs):
        return list(fake_results)

    added: list[dict[str, Any]] = []

    class FakeTask:
        id = 123
        status = "downloading"
        kind = "magnet"

    async def fake_add(resource, **kwargs):
        added.append(resource)
        return FakeTask()

    monkeypatch.setattr(search_service, "search", fake_search)
    monkeypatch.setattr(download_service, "add_download", fake_add)

    search_reply = run(chat_service.handle_message(msg("搜索 剧集", chat_id="s1")))
    assert "找到 2 条" in search_reply["reply"]

    download_reply = run(chat_service.handle_message(msg("下载 2", chat_id="s1")))
    assert download_reply["success"] is True
    assert added and added[0]["link"] == "magnet:?xt=2"
    assert "#123" in download_reply["reply"]


def test_download_without_session(client):
    result = run(chat_service.handle_message(msg("下载 1", chat_id="empty-session")))
    assert result["success"] is False
    assert "搜索" in result["reply"]


def test_download_index_out_of_range(client, monkeypatch):
    from app.services import search as search_service

    async def fake_search(keyword, **kwargs):
        return [{"title": "只有一条", "link": "magnet:?xt=1", "kind": "magnet"}]

    monkeypatch.setattr(search_service, "search", fake_search)
    run(chat_service.handle_message(msg("搜索 x", chat_id="s2")))
    result = run(chat_service.handle_message(msg("下载 9", chat_id="s2")))
    assert result["success"] is False and "范围" in result["reply"]


def test_session_expires(client, monkeypatch):
    """会话上下文过期后不能再引用旧结果。"""
    from app.core.config import settings
    from app.services import search as search_service

    async def fake_search(keyword, **kwargs):
        return [{"title": "过期测试", "link": "magnet:?xt=1", "kind": "magnet"}]

    monkeypatch.setattr(search_service, "search", fake_search)
    monkeypatch.setattr(settings, "CHATOPS_SESSION_TTL", 60)
    run(chat_service.handle_message(msg("搜索 y", chat_id="s3")))

    key = "console:s3"
    chat_service._SESSIONS[key]["at"] -= 3600
    result = run(chat_service.handle_message(msg("下载 1", chat_id="s3")))
    assert result["success"] is False and "过期" in result["reply"]


def test_search_auto_download(client, monkeypatch):
    """开了 auto_download 就直接下最优，不再等用户回序号。"""
    from app.services import download as download_service
    from app.services import search as search_service

    async def fake_search(keyword, **kwargs):
        return [{"title": "最优资源 2160p", "link": "magnet:?xt=best", "kind": "magnet", "site": "站A"}]

    added: list[dict[str, Any]] = []

    class FakeTask:
        id = 9
        status = "downloading"
        kind = "magnet"

    async def fake_add(resource, **kwargs):
        added.append(resource)
        return FakeTask()

    monkeypatch.setattr(search_service, "search", fake_search)
    monkeypatch.setattr(download_service, "add_download", fake_add)
    base = chat_service.default_config()
    monkeypatch.setattr(chat_service, "get_config", lambda: {**base, "auto_download": True})

    result = run(chat_service.handle_message(msg("搜索 最优", chat_id="auto")))
    assert added and added[0]["link"] == "magnet:?xt=best"
    assert "自动下载" in result["reply"]


def test_download_direct_link(client, monkeypatch):
    from app.services import download as download_service

    captured: list[dict[str, Any]] = []

    class FakeTask:
        id = 55
        status = "downloading"
        kind = "magnet"

    async def fake_add(resource, **kwargs):
        captured.append(resource)
        return FakeTask()

    monkeypatch.setattr(download_service, "add_download", fake_add)
    result = run(chat_service.handle_message(msg("下载 magnet:?xt=urn:btih:zzz")))
    assert result["success"] is True and "#55" in result["reply"]
    assert captured[0]["kind"] == "magnet"


def test_subscribe_command_creates_and_dedupes(client, monkeypatch):
    from app.services import subscribe as subscribe_service

    class FakeRecord:
        id = 88

    created: list[dict[str, Any]] = []

    async def fake_create(payload, **kwargs):
        created.append(payload)
        return FakeRecord()

    monkeypatch.setattr(subscribe_service, "create_subscribe", fake_create)
    result = run(chat_service.handle_message(msg("订阅 ChatOps测试剧 第二季")))
    assert result["success"] is True
    assert created[0]["title"] == "ChatOps测试剧"
    assert created[0]["season"] == 2
    assert "#88" in result["reply"]


def test_subscribe_without_title(client):
    result = run(chat_service.handle_message(msg("订阅")))
    assert result["success"] is False
    assert "要订阅什么" in result["reply"]


def test_transfer_command(client, monkeypatch):
    from app.services import pan_storage as pan_service

    async def fake_transfer(**kwargs):
        return {"pending": 3, "saved": 2, "failed": 1, "details": []}

    monkeypatch.setattr(pan_service, "transfer_pending", fake_transfer)
    result = run(chat_service.handle_message(msg("转存")))
    assert "成功 2" in result["reply"] and "失败 1" in result["reply"]


def test_transfer_command_nothing_pending(client, monkeypatch):
    from app.services import pan_storage as pan_service

    async def fake_transfer(**kwargs):
        return {"pending": 0, "saved": 0, "failed": 0}

    monkeypatch.setattr(pan_service, "transfer_pending", fake_transfer)
    result = run(chat_service.handle_message(msg("转存")))
    assert "没有待转存" in result["reply"]


def test_audit_log_written(client):
    """每条指令都要留痕，便于回溯"谁让 NAS 下了什么"。"""
    run(chat_service.handle_message(msg("状态", user_id="auditor", platform="dingtalk")))
    items = chat_service.list_audit(limit=20)
    hit = next((item for item in items if item["actor_id"] == "auditor"), None)
    assert hit is not None
    assert hit["source"] == "chatops.dingtalk"
    assert hit["action"] == "status"
    assert hit["success"] is True

    filtered = chat_service.list_audit(limit=20, source="chatops")
    assert filtered
    assert all(item["source"].startswith("chatops") for item in filtered)


# ---------------------------------------------------------------- Webhook 全链路
def test_process_webhook_unknown_platform():
    result = run(
        chat_service.process_webhook("wechat", headers={}, body=b"{}", payload={})
    )
    assert result["status"] == 404


def test_process_webhook_rejects_bad_signature(client, monkeypatch):
    base = chat_service.default_config()
    monkeypatch.setattr(
        chat_service,
        "get_config",
        lambda: {**base, "platforms": {"telegram": {"secret_token": "ST"}}},
    )
    result = run(
        chat_service.process_webhook(
            "telegram",
            headers={"x-telegram-bot-api-secret-token": "WRONG"},
            body=b"{}",
            payload={},
        )
    )
    assert result["status"] == 401
    assert "验签失败" in result["response"]["message"]


def test_process_webhook_feishu_challenge(client, monkeypatch):
    base = chat_service.default_config()
    monkeypatch.setattr(
        chat_service,
        "get_config",
        lambda: {**base, "platforms": {"feishu": {"verification_token": "VT"}}},
    )
    payload = {"type": "url_verification", "challenge": "cha-1", "token": "VT"}
    result = run(
        chat_service.process_webhook("feishu", headers={}, body=b"{}", payload=payload)
    )
    assert result["status"] == 200
    assert result["response"] == {"challenge": "cha-1"}


def test_process_webhook_executes_command(client, monkeypatch):
    """钉钉全链路：验签 → 解析 → 执行 → 回复内容塞进响应体。"""
    secret = "SEC-E2E"
    base = chat_service.default_config()
    monkeypatch.setattr(
        chat_service,
        "get_config",
        lambda: {**base, "platforms": {"dingtalk": {"app_secret": secret}}},
    )
    timestamp = str(int(time.time() * 1000))
    payload = {
        "text": {"content": "状态"},
        "senderStaffId": "e2e-user",
        "senderNick": "E2E",
        "conversationId": "conv",
        "msgId": f"msg-{time.time_ns()}",
    }
    result = run(
        chat_service.process_webhook(
            "dingtalk",
            headers={"timestamp": timestamp, "sign": _dingtalk_sign(secret, timestamp)},
            body=json.dumps(payload).encode(),
            payload=payload,
        )
    )
    assert result["status"] == 200
    assert result["response"]["handled"] is True
    # 没有 sessionWebhook 时把回复直接放进响应体，钉钉会显示出来
    assert result["response"]["msgtype"] == "text"
    assert "累计已完成" in result["response"]["text"]["content"]


# ---------------------------------------------------------------- 配置与 API
def test_save_and_get_config(client):
    chat_service.save_config({"result_limit": 7, "platforms": {"telegram": {"token": "T"}}})
    config = chat_service.get_config()
    assert config["result_limit"] == 7
    assert chat_service.platform_config("telegram")["token"] == "T"
    assert chat_service.platform_config("nope") == {}
    # 复原，避免影响其他用例
    chat_service.save_config({"result_limit": 5, "platforms": {}})


def test_build_adapter_uses_saved_config(client):
    chat_service.save_config({"platforms": {"feishu": {"verification_token": "VT-X"}}})
    adapter = chat_service.build_adapter("feishu")
    assert isinstance(adapter, FeishuAdapter)
    ok, _ = adapter.verify(headers={}, body=b"", payload={"token": "VT-X"})
    assert ok is True
    assert chat_service.build_adapter("nope") is None
    chat_service.save_config({"platforms": {}})


def test_chatops_api_platforms_and_commands(client, auth_headers):
    response = client.get("/api/v1/chatops/platforms", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 3
    for item in data["items"]:
        assert item["webhook_path"].startswith("/api/v1/chatops/webhook/")

    response = client.get("/api/v1/chatops/commands", headers=auth_headers)
    assert response.status_code == 200
    names = {item["name"] for item in response.json()["commands"]}
    assert {"search", "download", "subscribe", "status", "transfer", "trending"} <= names


def test_chatops_api_config_masks_secrets(client, auth_headers):
    client.put(
        "/api/v1/chatops/config",
        headers=auth_headers,
        json={"platforms": {"telegram": {"token": "SUPER-SECRET", "api_base": "http://tg"}}},
    )
    data = client.get("/api/v1/chatops/config", headers=auth_headers).json()["data"]
    assert data["platforms"]["telegram"]["token"] == "******"
    assert data["platforms"]["telegram"]["api_base"] == "http://tg"

    # 提交 ****** 表示保持原值
    client.put(
        "/api/v1/chatops/config",
        headers=auth_headers,
        json={"platforms": {"telegram": {"token": "******", "api_base": "http://tg2"}}},
    )
    assert chat_service.platform_config("telegram")["token"] == "SUPER-SECRET"
    assert chat_service.platform_config("telegram")["api_base"] == "http://tg2"
    chat_service.save_config({"platforms": {}})


def test_chatops_api_test_and_parse(client, auth_headers):
    response = client.post(
        "/api/v1/chatops/test", headers=auth_headers, json={"text": "状态"}
    )
    assert response.status_code == 200
    assert "累计已完成" in response.json()["reply"]

    response = client.post(
        "/api/v1/chatops/parse", headers=auth_headers, json={"text": "搜索 沙丘 第二季"}
    )
    assert response.json()["data"] == {
        "name": "search",
        "argument": "沙丘",
        "index": None,
        "season": 2,
        "episode": None,
    }

    response = client.post(
        "/api/v1/chatops/parse", headers=auth_headers, json={"text": "@bot"}
    )
    assert response.status_code == 400


def test_chatops_api_audit(client, auth_headers):
    response = client.get("/api/v1/chatops/audit?limit=5", headers=auth_headers)
    assert response.status_code == 200
    assert "items" in response.json()


def test_chatops_webhook_needs_no_jwt_but_verifies(client):
    """Webhook 端点不要求登录（平台无法带 JWT），但必须验签。"""
    response = client.post("/api/v1/chatops/webhook/telegram", json={"message": {"text": "状态"}})
    assert response.status_code == 401

    response = client.post("/api/v1/chatops/webhook/wechat", json={})
    assert response.status_code == 404


def test_chatops_config_requires_superuser(client):
    assert client.get("/api/v1/chatops/config").status_code == 401
    assert client.put("/api/v1/chatops/config", json={"enabled": True}).status_code == 401
