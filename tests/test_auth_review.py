"""认证依赖迁移与本机签名密钥回归。"""

import hashlib
import json
from pathlib import Path

import jwt
import pytest

from app.core import security
from app.core.config import Settings, settings


def test_legacy_python_jose_token_is_still_accepted(monkeypatch):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "legacy-jose-jwt.json").read_text(encoding="utf-8")
    )
    monkeypatch.setattr(settings, "SECRET_KEY", fixture["key"])
    assert security.decode_access_token(fixture["token"]) == fixture["payload"]


@pytest.mark.parametrize("algorithm", ["none", "HS512"])
def test_unapproved_token_algorithms_are_rejected(monkeypatch, algorithm):
    monkeypatch.setattr(settings, "SECRET_KEY", "review-only-signing-key-at-least-64-bytes-" * 2)
    token = jwt.encode(
        {"sub": "review", "exp": 4102444800},
        "" if algorithm == "none" else settings.SECRET_KEY,
        algorithm=algorithm,
    )
    assert security.decode_access_token(token) is None


def test_token_requires_expiry():
    token = jwt.encode({"sub": "review"}, settings.SECRET_KEY, algorithm="HS256")
    assert security.decode_access_token(token) is None


def test_legacy_password_hash_is_still_accepted():
    password = "review legacy password"
    salt = hashlib.sha256(f"old-public-key:{password}".encode()).hexdigest()[:16]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 180000).hex()
    assert security.verify_password(password, f"pbkdf2_sha256$180000${salt}${digest}")


def _settings(directory, key):
    return Settings(
        DATA_DIR=directory,
        SECRET_KEY=key,
        DOWNLOAD_DIR=directory / "downloads",
        LIBRARY_DIR=directory / "library",
        STRM_DIR=directory / "strm",
        PLUGIN_DIR=directory / "plugins",
    )


@pytest.mark.parametrize(
    "placeholder",
    [
        "",
        "cineflow-dev-secret-change-me",
        "please-change-this-to-a-random-string",
        "CHANGE_ME_RANDOM_STRING",
    ],
)
def test_public_default_key_is_replaced_and_persisted(tmp_path, placeholder):
    first = _settings(tmp_path, placeholder)
    second = _settings(tmp_path, placeholder)
    assert first.SECRET_KEY == second.SECRET_KEY
    assert first.SECRET_KEY != "cineflow-dev-secret-change-me"
    assert len(first.SECRET_KEY.encode()) >= 32
    assert (tmp_path / "jwt-secret.txt").read_text(encoding="utf-8") == first.SECRET_KEY


def test_custom_signing_key_is_preserved(tmp_path):
    configured = "review-custom-signing-key-at-least-32-bytes"
    assert configured == _settings(tmp_path, configured).SECRET_KEY
    assert not (tmp_path / "jwt-secret.txt").exists()


def test_corrupt_local_key_is_rejected(tmp_path):
    (tmp_path / "jwt-secret.txt").write_text("short", encoding="utf-8")
    with pytest.raises(ValueError, match="密钥文件无效"):
        _settings(tmp_path, "cineflow-dev-secret-change-me")


def test_password_change_uses_json_body(client):
    from app.db.models import User
    from app.db.session import session_scope

    with session_scope() as session:
        session.add(
            User(
                username="review-password-user",
                password_hash=security.hash_password("previous-review-password"),
                role="viewer",
                is_active=True,
                is_superuser=False,
            )
        )
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "review-password-user", "password": "previous-review-password"},
    )
    token = response.json()["access_token"]
    changed = client.post(
        "/api/v1/auth/password",
        headers={"Authorization": "Bearer " + token},
        json={"old_password": "previous-review-password", "new_password": "new-review-password"},
    )
    assert changed.status_code == 200
    assert (
        client.post(
            "/api/v1/auth/login",
            data={"username": "review-password-user", "password": "new-review-password"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/auth/password",
            headers={"Authorization": "Bearer " + token},
            params={"old_password": "new-review-password", "new_password": "query-must-not-work"},
        ).status_code
        == 422
    )


def test_sensitive_request_urls_are_redacted_in_all_log_outputs():
    import logging

    from app.core.logger import LOG_BUFFER, BufferHandler, SecretFormatter

    message = "POST /api/v1/auth/password?old_password=review-old&new_password=review-new HTTP/1.1 https://user:review-basic@example.org/?api_key=review-key&category=tv"
    record = logging.LogRecord("review", logging.INFO, __file__, 1, "%s", (message,), None)
    rendered = SecretFormatter("%(message)s").format(record)
    previous = list(LOG_BUFFER)
    try:
        BufferHandler().emit(record)
        buffered = LOG_BUFFER[-1]["message"]
        for text in (rendered, buffered):
            assert "review-old" not in text
            assert "review-new" not in text
            assert "review-basic" not in text
            assert "review-key" not in text
            assert "category=tv" in text
    finally:
        LOG_BUFFER.clear()
        LOG_BUFFER.extend(previous)
