"""认证与口令处理。"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

from app.core.config import settings

ALGORITHM = "HS256"
_PBKDF2_ROUNDS = 180_000


def hash_password(password: str) -> str:
    """PBKDF2-SHA256 口令散列（不依赖 bcrypt 的原生库）。"""
    salt = hashlib.sha256(
        f"{settings.SECRET_KEY}:{password}".encode()
    ).hexdigest()[:16]
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS
    ).hex()
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest}"


def verify_password(password: str, hashed: str) -> bool:
    """校验口令。"""
    try:
        algorithm, rounds, salt, digest = hashed.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), int(rounds)
    ).hex()
    return hmac.compare_digest(candidate, digest)


def create_access_token(
    subject: str, extra: dict[str, Any] | None = None, expires_minutes: int | None = None
) -> str:
    """签发 JWT。"""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.TOKEN_EXPIRE_MINUTES
    )
    payload: dict[str, Any] = {"sub": subject, "exp": expire}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    """解析 JWT，失败返回 ``None``。"""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
