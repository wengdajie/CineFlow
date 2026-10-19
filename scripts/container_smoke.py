"""验证隔离容器的健康、版本、前端和登录；凭据仅从环境变量读取。"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener


def main() -> None:
    base = os.environ["CF_SMOKE_URL"].rstrip("/")
    username = os.environ.get("CF_SMOKE_USERNAME", "smoke_admin")
    password = os.environ["CF_SMOKE_PASSWORD"]
    expected = os.environ["CF_SMOKE_VERSION"]
    opener = build_opener(ProxyHandler({}))

    def read(path: str, *, data: bytes | None = None, token: str = "") -> bytes:
        headers = {"Authorization": "Bearer " + token} if token else {}
        if data is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        with opener.open(Request(base + path, data=data, headers=headers), timeout=5) as response:
            return response.read()

    deadline = time.monotonic() + 90
    while True:
        try:
            health = json.loads(read("/api/health"))
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise RuntimeError("容器未在 90 秒内就绪") from None
            time.sleep(1)
    assert health["version"] == expected, "镜像中的版本与待发布版本不一致"
    assert b"CineFlow" in read("/"), "前端首页缺失"
    assert read("/assets/app.js"), "前端脚本缺失"
    auth = json.loads(
        read(
            "/api/v1/auth/login",
            data=urlencode({"username": username, "password": password}).encode(),
        )
    )
    account = json.loads(read("/api/v1/auth/me", token=auth["access_token"]))
    assert account["username"] == username and account["is_superuser"], "管理员登录失败"
    with zipfile.ZipFile(io.BytesIO(read("/api/v1/pan/login/browser-extension", token=auth["access_token"]))) as archive:
        assert json.loads(archive.read("manifest.json"))["manifest_version"] == 3
        assert "background.js" in archive.namelist()
    print(f"Container smoke passed: version={expected}, health, frontend, login, browser helper")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Container smoke failed: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
