"""pytest 全局装置：每个会话使用独立的临时数据目录与数据库。"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

TEST_ROOT = Path(tempfile.mkdtemp(prefix="cineflow_test_"))

# 必须在导入 app 之前设置，配置为单例
os.environ.update(
    {
        "CF_DATA_DIR": str(TEST_ROOT / "data"),
        "CF_DOWNLOAD_DIR": str(TEST_ROOT / "downloads"),
        "CF_LIBRARY_DIR": str(TEST_ROOT / "library"),
        "CF_STRM_DIR": str(TEST_ROOT / "strm"),
        "CF_PLUGIN_DIR": str(TEST_ROOT / "plugins"),
        "CF_SCHEDULER_ENABLED": "false",
        "CF_SECRET_KEY": "test-secret",
        "CF_SUPERUSER": "admin",
        "CF_SUPERUSER_PASSWORD": "cineflow",
        "CF_MIN_FILE_SIZE_MB": "0",
        "CF_TMDB_API_KEY": "",
    }
)


import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """已完成初始化的测试客户端。"""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def auth_headers(client):
    """管理员认证头。"""
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "admin", "password": "cineflow"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def tmp_media(tmp_path):
    """构造一个假的下载目录（含剧集与字幕）。"""

    def _make(name: str, size_kb: int = 8) -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"0" * size_kb * 1024)
        return path

    return _make
