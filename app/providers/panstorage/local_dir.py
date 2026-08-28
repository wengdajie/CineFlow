"""本地目录「伪网盘」。

用途有两个：

1. **零配置试用**：没有任何网盘账号也能把网盘管理页跑起来，
   把 NAS 上的某个目录当成网盘浏览（例如已挂载的 rclone / CloudDrive 目录）。
2. **可测**：不联网即可验证服务层与 API 的正确性。

它不支持从分享链接转存（``supports_save = False``），
转存请求会返回明确提示而不是假装成功。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.logger import get_logger
from app.providers.panstorage.base import BasePanStorage, PanFile, PanQuota, SaveResult
from app.providers.registry import register

logger = get_logger(__name__)


@register
class LocalDirStorage(BasePanStorage):
    """把本地/挂载目录当作网盘浏览。"""

    name = "local_dir"
    display_name = "本地目录（含 rclone/CloudDrive 挂载）"
    supports_save = False

    @property
    def base_dir(self) -> Path | None:
        """映射的本地根目录，未配置时返回 ``None``。

        注意不能返回 ``Path("")``——它等价于 ``Path(".")``（进程当前目录），
        会让"未配置"被误判成"已配置且存在"，进而把 CineFlow 自己的目录当网盘。
        """
        raw = str(self.option("base_dir") or self.config.get("url") or "").strip()
        if raw.startswith("file:///"):
            raw = raw[len("file:///"):]
        return Path(raw) if raw else None

    def _resolve(self, path: str) -> Path:
        """把网盘路径映射为本地路径，并**禁止逃出根目录**。"""
        if not self.base_dir:
            raise ValueError("未配置 base_dir")
        root = self.base_dir.resolve()
        target = (root / self.normalize_path(path).lstrip("/")).resolve()
        if root not in target.parents and target != root:
            raise ValueError("路径越界")
        return target

    async def list_dir(self, path: str = "/") -> list[PanFile]:
        if not self.base_dir:
            return []
        try:
            target = self._resolve(path)
        except ValueError:
            logger.warning("本地网盘路径越界：%s", path)
            return []
        if not target.is_dir():
            return []

        base = self.normalize_path(path)
        files: list[PanFile] = []
        for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
            try:
                stat = entry.stat()
            except OSError:
                continue
            files.append(
                PanFile(
                    name=entry.name,
                    path=self.join_path(base, entry.name),
                    is_dir=entry.is_dir(),
                    size=0 if entry.is_dir() else stat.st_size,
                    modified_at=None,
                )
            )
        return files

    async def save_share(
        self,
        share_url: str,
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        return SaveResult(
            False,
            "本地目录不支持从分享链接转存，请改用 AList 或夸克网盘",
        )

    async def quota(self) -> PanQuota:
        if not self.base_dir or not self.base_dir.exists():
            return PanQuota()
        usage = shutil.disk_usage(self.base_dir)
        return PanQuota(total=usage.total, used=usage.used)

    async def mkdir(self, path: str) -> bool:
        try:
            self._resolve(path).mkdir(parents=True, exist_ok=True)
            return True
        except (OSError, ValueError) as exc:
            logger.warning("创建目录失败 %s: %s", path, exc)
            return False

    async def delete(self, path: str, *, file_id: str | None = None) -> bool:
        try:
            target = self._resolve(path)
        except ValueError:
            return False
        if not target.exists():
            return False
        try:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            return True
        except OSError as exc:
            logger.warning("删除失败 %s: %s", path, exc)
            return False

    async def health_check(self) -> tuple[bool, str]:
        if not self.base_dir:
            return False, "未配置 base_dir（要映射的本地目录）"
        if not self.base_dir.exists():
            return False, f"目录不存在：{self.base_dir}"
        count = sum(1 for _ in self.base_dir.iterdir())
        return True, f"目录可访问，根目录 {count} 个条目"
