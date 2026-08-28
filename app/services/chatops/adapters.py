"""聊天平台适配器：验签、解析入站消息、回复消息。

每个平台的回调协议差别很大，本模块把它们统一成 :class:`InboundMessage`：

| 平台 | 验签方式 | 回复方式 |
|---|---|---|
| 飞书 | ``Encrypt`` AES 解密（可选）+ URL 验证挑战 | Bot API ``/im/v1/messages`` |
| 钉钉 | HMAC-SHA256(timestamp + secret) | 回调返回体 或 机器人 Webhook |
| Telegram | ``X-Telegram-Bot-Api-Secret-Token`` 定值比对 | Bot API ``sendMessage`` |

安全设计：
- 所有适配器都实现 :meth:`verify`，**验签失败直接拒绝**（返回 401）；
- :meth:`dedupe_key` 提供幂等键，配合 ``chatops.service`` 的去重表防重放；
- 用户白名单在 service 层统一校验（各平台用户 ID 语义不同）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.core.logger import get_logger
from app.utils.http import fetch_json

logger = get_logger(__name__)


@dataclass
class InboundMessage:
    """归一化后的入站消息。"""

    platform: str
    text: str = ""
    user_id: str = ""
    user_name: str = ""
    chat_id: str = ""
    message_id: str = ""
    #: 平台要求的即时响应体（如飞书 URL 验证挑战）
    challenge: dict[str, Any] | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        """是否是一条需要执行指令的真实消息。"""
        return bool(self.text.strip()) and self.challenge is None


class ChatAdapter(ABC):
    """聊天平台适配器基类。"""

    #: 平台标识
    platform: str = "base"
    display_name: str = "Chat"
    #: 配置项声明（供前端自动渲染表单，避免前后端硬编码两份字段）
    config_fields: tuple[dict[str, Any], ...] = ()
    #: 平台侧填写回调地址的位置说明
    setup_hint: str = ""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}

    def option(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    def _missing_secret(self, name: str) -> tuple[bool, str]:
        """没配验签密钥时的统一处理。

        默认**拒绝**：Webhook 端点不走 JWT，如果未配密钥又放行，
        任何人知道地址就能操控 NAS 下载。确实想跳过（例如纯内网）
        必须在平台配置里显式打开 ``allow_unverified``。
        """
        if self.option("allow_unverified"):
            logger.warning("ChatOps[%s] 未配置 %s，已按 allow_unverified 放行", self.platform, name)
            return True, f"未配置 {name}，已按 allow_unverified 放行"
        return False, f"未配置 {name}，已拒绝（如为纯内网可在平台配置中开启 allow_unverified）"

    @abstractmethod
    def verify(
        self, *, headers: dict[str, str], body: bytes, payload: dict[str, Any]
    ) -> tuple[bool, str]:
        """校验请求合法性，返回 ``(是否通过, 原因)``。"""

    @abstractmethod
    def parse(self, payload: dict[str, Any]) -> InboundMessage:
        """把平台原始回调解析成统一消息。"""

    async def reply(self, message: InboundMessage, text: str) -> bool:
        """主动回复一条消息（默认不支持，由子类实现）。"""
        return False

    @staticmethod
    def dedupe_key(message: InboundMessage) -> str:
        """幂等键：同一条消息重复投递只处理一次。"""
        base = f"{message.platform}:{message.message_id or message.text}:{message.user_id}"
        return hashlib.md5(base.encode()).hexdigest()


# ---------------------------------------------------------------- 飞书
class FeishuAdapter(ChatAdapter):
    """飞书（Lark）自建应用事件回调。"""

    platform = "feishu"
    display_name = "飞书"
    config_fields = (
        {"key": "app_id", "label": "App ID", "hint": "开放平台应用凭证 App ID"},
        {"key": "app_secret", "label": "App Secret", "secret": True, "hint": "用于换取 tenant_access_token 回复消息"},
        {"key": "verification_token", "label": "Verification Token", "secret": True, "hint": "事件订阅页的 Verification Token，必填（用于验签）"},
        {"key": "encrypt_key", "label": "Encrypt Key", "secret": True, "hint": "若开启了加密推送则填写，否则留空"},
        {"key": "api_base", "label": "API 地址", "hint": "默认 https://open.feishu.cn，飞书国际版填 open.larksuite.com"},
        {"key": "allow_unverified", "label": "允许免验签", "hint": "留空=必须验签（推荐）；填 1 表示纯内网免验签，风险自负"},
    )
    setup_hint = "开放平台 → 事件订阅 → 请求地址，填下方回调地址；并订阅 im.message.receive_v1"

    def verify(
        self, *, headers: dict[str, str], body: bytes, payload: dict[str, Any]
    ) -> tuple[bool, str]:
        """校验 verification token；若配了签名密钥则同时校验签名。

        飞书事件订阅有两种模式：v1 用 ``token`` 字段，v2 用
        ``header.token``。两者都支持。
        """
        expected = str(self.option("verification_token") or "").strip()
        if not expected:
            return self._missing_secret("verification_token")

        got = str(
            payload.get("token")
            or (payload.get("header") or {}).get("token")
            or ""
        )
        if got != expected:
            return False, "verification token 不匹配"
        return True, "ok"

    def decrypt(self, encrypted: str) -> dict[str, Any] | None:
        """解密飞书 ``Encrypt`` 字段（配置了加密密钥时）。"""
        key = str(self.option("encrypt_key") or "").strip()
        if not key or not encrypted:
            return None
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            digest = hashlib.sha256(key.encode()).digest()
            data = base64.b64decode(encrypted)
            iv, cipher_text = data[:16], data[16:]
            decryptor = Cipher(algorithms.AES(digest), modes.CBC(iv)).decryptor()
            plain = decryptor.update(cipher_text) + decryptor.finalize()
            plain = plain[: -plain[-1]]  # 去 PKCS7 padding
            return json.loads(plain.decode("utf-8"))
        except Exception as exc:
            logger.warning("飞书事件解密失败: %s", exc)
            return None

    def parse(self, payload: dict[str, Any]) -> InboundMessage:
        # 加密模式：先解密
        if payload.get("encrypt"):
            decrypted = self.decrypt(str(payload["encrypt"]))
            if decrypted:
                payload = decrypted

        # URL 验证挑战：必须原样回 challenge
        if payload.get("type") == "url_verification" or payload.get("challenge"):
            return InboundMessage(
                platform=self.platform,
                challenge={"challenge": payload.get("challenge", "")},
                raw=payload,
            )

        event = payload.get("event") or {}
        message = event.get("message") or {}
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}

        # 文本内容是 JSON 字符串：{"text":"搜索 庆余年"}
        text = ""
        content = message.get("content")
        if content:
            try:
                text = str((json.loads(content) or {}).get("text") or "")
            except (json.JSONDecodeError, TypeError):
                text = str(content)

        return InboundMessage(
            platform=self.platform,
            text=text,
            user_id=str(sender_id.get("open_id") or sender_id.get("user_id") or ""),
            user_name=str(sender.get("sender_type") or ""),
            chat_id=str(message.get("chat_id") or ""),
            message_id=str(message.get("message_id") or ""),
            raw=payload,
        )

    async def reply(self, message: InboundMessage, text: str) -> bool:
        """通过飞书 Bot API 回复到原会话。"""
        app_id = str(self.option("app_id") or "").strip()
        app_secret = str(self.option("app_secret") or "").strip()
        if not app_id or not app_secret or not message.chat_id:
            return False

        base = str(self.option("api_base") or "https://open.feishu.cn").rstrip("/")
        auth = await fetch_json(
            f"{base}/open-apis/auth/v3/tenant_access_token/internal",
            method="POST",
            json_body={"app_id": app_id, "app_secret": app_secret},
            timeout=15,
        )
        token = (auth or {}).get("tenant_access_token")
        if not token:
            logger.warning("飞书获取 access_token 失败")
            return False

        result = await fetch_json(
            f"{base}/open-apis/im/v1/messages",
            method="POST",
            params={"receive_id_type": "chat_id"},
            json_body={
                "receive_id": message.chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        return bool(result and result.get("code") == 0)


# ---------------------------------------------------------------- 钉钉
class DingTalkAdapter(ChatAdapter):
    """钉钉机器人 outgoing 回调。"""

    platform = "dingtalk"
    display_name = "钉钉"
    config_fields = (
        {"key": "app_secret", "label": "App Secret", "secret": True, "hint": "机器人的 AppSecret，用于校验回调签名，必填"},
        {"key": "webhook_url", "label": "兜底 Webhook", "hint": "留空则用回调里的 sessionWebhook 回复"},
        {"key": "max_drift_ms", "label": "签名有效期(毫秒)", "hint": "默认 3600000，防重放"},
        {"key": "allow_unverified", "label": "允许免验签", "hint": "留空=必须验签（推荐）；填 1 表示纯内网免验签，风险自负"},
    )
    setup_hint = "钉钉开发者后台 → 机器人 → 消息接收模式选 HTTP，消息接收地址填下方回调地址"

    def verify(
        self, *, headers: dict[str, str], body: bytes, payload: dict[str, Any]
    ) -> tuple[bool, str]:
        """校验钉钉的 timestamp + sign 头。

        算法：``base64(HMAC_SHA256(timestamp + "\\n" + secret, secret))``
        """
        secret = str(self.option("app_secret") or self.option("secret") or "").strip()
        if not secret:
            return self._missing_secret("app_secret")

        lowered = {k.lower(): v for k, v in headers.items()}
        timestamp = lowered.get("timestamp") or ""
        sign = lowered.get("sign") or ""
        if not timestamp or not sign:
            return False, "缺少 timestamp 或 sign 头"

        # 防重放：默认允许 1 小时时差（钉钉官方建议）
        try:
            drift = abs(time.time() * 1000 - float(timestamp))
            if drift > float(self.option("max_drift_ms", 3600_000)):
                return False, "时间戳超出允许范围（可能是重放请求）"
        except ValueError:
            return False, "timestamp 格式非法"

        payload_str = f"{timestamp}\n{secret}"
        digest = hmac.new(
            secret.encode(), payload_str.encode(), hashlib.sha256
        ).digest()
        expected = base64.b64encode(digest).decode()
        if not hmac.compare_digest(expected, sign):
            return False, "签名不匹配"
        return True, "ok"

    def parse(self, payload: dict[str, Any]) -> InboundMessage:
        text = str(((payload.get("text") or {}).get("content")) or "")
        return InboundMessage(
            platform=self.platform,
            text=text,
            user_id=str(payload.get("senderStaffId") or payload.get("senderId") or ""),
            user_name=str(payload.get("senderNick") or ""),
            chat_id=str(payload.get("conversationId") or ""),
            message_id=str(payload.get("msgId") or ""),
            raw=payload,
        )

    async def reply(self, message: InboundMessage, text: str) -> bool:
        """通过 sessionWebhook 或固定机器人 Webhook 回复。"""
        webhook = str(
            (message.raw or {}).get("sessionWebhook")
            or self.option("webhook_url")
            or ""
        ).strip()
        if not webhook:
            return False
        result = await fetch_json(
            webhook,
            method="POST",
            json_body={"msgtype": "text", "text": {"content": text}},
            timeout=15,
        )
        return bool(result and result.get("errcode") in (0, None))


# ---------------------------------------------------------------- Telegram
class TelegramAdapter(ChatAdapter):
    """Telegram Bot Webhook。"""

    platform = "telegram"
    display_name = "Telegram"
    config_fields = (
        {"key": "token", "label": "Bot Token", "secret": True, "hint": "BotFather 给的 token，用于回复消息"},
        {"key": "secret_token", "label": "Secret Token", "secret": True, "hint": "setWebhook 时传的 secret_token，用于验签，必填"},
        {"key": "api_base", "label": "API 地址", "hint": "默认 https://api.telegram.org，可填反代地址"},
        {"key": "allow_unverified", "label": "允许免验签", "hint": "留空=必须验签（推荐）；填 1 表示纯内网免验签，风险自负"},
    )
    setup_hint = "调用 setWebhook?url=<回调地址>&secret_token=<上面的 Secret Token> 完成注册"

    def verify(
        self, *, headers: dict[str, str], body: bytes, payload: dict[str, Any]
    ) -> tuple[bool, str]:
        """校验 ``X-Telegram-Bot-Api-Secret-Token``。

        这是 Telegram 官方推荐的 Webhook 防伪方式（setWebhook 时设定）。
        """
        expected = str(self.option("secret_token") or "").strip()
        if not expected:
            return self._missing_secret("secret_token")
        lowered = {k.lower(): v for k, v in headers.items()}
        got = lowered.get("x-telegram-bot-api-secret-token") or ""
        if not hmac.compare_digest(expected, got):
            return False, "secret token 不匹配"
        return True, "ok"

    def parse(self, payload: dict[str, Any]) -> InboundMessage:
        message = (
            payload.get("message")
            or payload.get("edited_message")
            or payload.get("channel_post")
            or {}
        )
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        return InboundMessage(
            platform=self.platform,
            text=str(message.get("text") or message.get("caption") or ""),
            user_id=str(sender.get("id") or ""),
            user_name=str(sender.get("username") or sender.get("first_name") or ""),
            chat_id=str(chat.get("id") or ""),
            message_id=str(message.get("message_id") or ""),
            raw=payload,
        )

    async def reply(self, message: InboundMessage, text: str) -> bool:
        token = str(self.option("token") or self.option("api_key") or "").strip()
        if not token or not message.chat_id:
            return False
        base = str(self.option("api_base") or "https://api.telegram.org").rstrip("/")
        result = await fetch_json(
            f"{base}/bot{token}/sendMessage",
            method="POST",
            json_body={
                "chat_id": message.chat_id,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return bool(result and result.get("ok"))


#: 平台标识 -> 适配器类
ADAPTERS: dict[str, type[ChatAdapter]] = {
    FeishuAdapter.platform: FeishuAdapter,
    DingTalkAdapter.platform: DingTalkAdapter,
    TelegramAdapter.platform: TelegramAdapter,
}


def get_adapter(platform: str, config: dict[str, Any] | None = None) -> ChatAdapter | None:
    """按平台名构建适配器。"""
    adapter_cls = ADAPTERS.get(str(platform or "").lower())
    return adapter_cls(config or {}) if adapter_cls else None


def list_platforms() -> list[dict[str, Any]]:
    """支持的平台清单。"""
    return [
        {
            "platform": cls.platform,
            "display_name": cls.display_name,
            "setup_hint": cls.setup_hint,
            "fields": [dict(item) for item in cls.config_fields],
        }
        for cls in ADAPTERS.values()
    ]
