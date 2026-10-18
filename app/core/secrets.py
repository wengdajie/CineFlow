"""站点扩展配置中的凭据展示与编辑回填。"""

import re
from copy import deepcopy
from typing import Any
from urllib.parse import unquote_plus, urlsplit, urlunsplit

SECRET_MASK = "******"


def _secret_key(key: str) -> bool:
    key = key.lower().replace("-", "_")
    compact = key.replace("_", "")
    return any(
        word in compact
        for word in ("password", "secret", "token", "cookie", "authorization", "apikey")
    ) or key in {"bduss", "stoken", "sid"}


def _map_url(value: Any, previous: Any = "", *, restore: bool = False) -> Any:
    if restore and value == SECRET_MASK:
        return previous
    if not isinstance(value, str) or not value.lower().startswith(("http://", "https://")):
        return value
    try:
        parsed = urlsplit(value)
        old = urlsplit(previous if isinstance(previous, str) else "")
    except ValueError:
        return value if restore else SECRET_MASK
    netloc = parsed.netloc
    if "@" in netloc:
        credentials, host = netloc.rsplit("@", 1)
        if not restore:
            if ":" in credentials:
                credentials = credentials.split(":", 1)[0] + ":" + SECRET_MASK
            else:
                credentials = SECRET_MASK
        elif unquote_plus(credentials.split(":", 1)[-1]) == SECRET_MASK:
            old_credentials = old.netloc.rpartition("@")[0]
            if ":" in credentials:
                credentials = credentials.split(":", 1)[0] + ":" + old_credentials.split(":", 1)[-1]
            else:
                credentials = old_credentials
        netloc = credentials + "@" + host
    old_query = {
        unquote_plus(part.partition("=")[0]): part.partition("=")[2]
        for part in old.query.split("&")
    }
    query = []
    for part in parsed.query.split("&"):
        key, separator, item = part.partition("=")
        decoded = unquote_plus(key)
        if (
            separator
            and item
            and (_secret_key(decoded) or decoded.lower() in {"key", "auth", "sign", "signature"})
        ):
            if not restore:
                item = SECRET_MASK
            elif unquote_plus(item) == SECRET_MASK:
                item = old_query.get(decoded, "")
        query.append(key + separator + item)
    mapped_query = "&".join(query)
    if netloc == parsed.netloc and mapped_query == parsed.query:
        return value
    return urlunsplit((parsed.scheme, netloc, parsed.path, mapped_query, parsed.fragment))


def redact_url(value: str) -> str:
    return _map_url(value)


def redact_message(value: str) -> str:
    """遮盖日志中完整 URL 及相对请求地址内的敏感查询参数。"""
    value = re.sub(r"https?://[^\s<>\"']+", lambda match: redact_url(match[0]), value)

    def replace(match: re.Match) -> str:
        key = unquote_plus(match[1])
        if _secret_key(key) or key.lower() in {"key", "auth", "sign", "signature"}:
            return match[1] + "=" + SECRET_MASK
        return match[0]

    return re.sub(r"([\w%.-]+)=([^&\s\"'<>]*)", replace, value)


def restore_url(value: str, previous: str) -> str:
    return _map_url(value, previous, restore=True)


def redact_options(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: SECRET_MASK
            if _secret_key(str(key)) and isinstance(item, (str, dict, list)) and item
            else redact_options(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_options(item) for item in value]
    return _map_url(value)


def restore_options(value: Any, previous: Any) -> Any:
    """掩码代表不修改；显式空串仍可用于清除凭据。"""
    if isinstance(value, dict):
        previous = previous if isinstance(previous, dict) else {}
        return {
            key: deepcopy(previous.get(key))
            if _secret_key(str(key)) and item == SECRET_MASK
            else restore_options(item, previous.get(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        previous = previous if isinstance(previous, list) else []
        return [
            restore_options(item, previous[index] if index < len(previous) else None)
            for index, item in enumerate(value)
        ]
    return _map_url(value, previous, restore=True)
