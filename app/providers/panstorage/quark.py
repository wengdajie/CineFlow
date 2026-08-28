"""夸克网盘直连（Cookie 鉴权）。

夸克是国内影视资源分享最集中的网盘之一，盘搜命中的链接大量是
``https://pan.quark.cn/s/xxxx``。本 Provider 直接对接夸克 Web API，
无需额外部署 AList，代价是**依赖 Cookie**（会过期，需在站点管理里更新）。

夸克转存的四步流程：
1. ``/share/sharepage/token``   用分享码（+提取码）换 stoken
2. ``/share/sharepage/detail``  列出分享内的文件，拿 fid / share_fid_token
3. ``/share/sharepage/save``    提交转存任务
4. ``/task``                    轮询任务直到完成

> 夸克接口非公开，字段可能随官方调整而变化。所有请求失败都会**优雅降级**为
> 明确的错误消息，不会抛异常打断调用方。
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.core.logger import get_logger
from app.providers.panstorage.base import BasePanStorage, PanFile, PanQuota, SaveResult
from app.providers.registry import register
from app.utils.http import fetch_json

logger = get_logger(__name__)

API_BASE = "https://drive-pc.quark.cn/1/clouddrive"
#: 分享链接里的分享码，如 https://pan.quark.cn/s/186546bac72a
SHARE_ID_RE = re.compile(r"pan\.quark\.cn/s/([0-9a-zA-Z]+)")
_COMMON_PARAMS = {"pr": "ucpro", "fr": "pc"}


@register
class QuarkStorage(BasePanStorage):
    """夸克网盘。"""

    name = "quark"
    display_name = "夸克网盘"

    @property
    def cookie(self) -> str:
        # 用 option() 而非 config.get()：Cookie 既可能填在站点字段上，
        # 也可能填在 options 里（站点管理页的示例模板写在 options）
        return str(self.option("cookie") or "").strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Cookie": self.cookie,
            "Content-Type": "application/json",
            "Referer": "https://pan.quark.cn/",
        }

    async def _call(
        self,
        path: str,
        *,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """调用夸克 API，成功返回整个响应体。"""
        if not self.cookie:
            return None
        merged = {**_COMMON_PARAMS, **(params or {})}
        payload = await fetch_json(
            f"{API_BASE}{path}",
            method=method,
            params=merged,
            json_body=body,
            headers=self._headers(),
            timeout=self.config.get("timeout"),
        )
        if not payload:
            return None
        if payload.get("code") not in (0, 200, None):
            logger.warning("夸克 %s 返回 %s: %s", path, payload.get("code"), payload.get("message"))
            return None
        return payload

    # ---------------- 目录 ----------------
    async def _resolve_fid(self, path: str) -> str:
        """把路径逐级解析成夸克的目录 fid（根目录为 ``0``）。"""
        target = self.normalize_path(path)
        fid = "0"
        if target == "/":
            return fid
        for segment in [s for s in target.split("/") if s]:
            children = await self._list_by_fid(fid)
            match = next(
                (c for c in children if c.name == segment and c.is_dir), None
            )
            if not match or not match.file_id:
                return ""
            fid = match.file_id
        return fid

    async def _list_by_fid(self, fid: str) -> list[PanFile]:
        payload = await self._call(
            "/file/sort",
            params={
                "pdir_fid": fid,
                "_page": 1,
                "_size": 200,
                "_fetch_total": 1,
                "_sort": "file_type:asc,updated_at:desc",
            },
        )
        items = ((payload or {}).get("data") or {}).get("list") or []
        files: list[PanFile] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("file_name") or "")
            if not name:
                continue
            files.append(
                PanFile(
                    name=name,
                    path=name,  # 由 list_dir 补全完整路径
                    is_dir=bool(item.get("dir") or item.get("file_type") == 0),
                    size=int(item.get("size") or 0),
                    file_id=str(item.get("fid") or ""),
                    modified_at=str(item.get("updated_at") or "") or None,
                )
            )
        return files

    async def list_dir(self, path: str = "/") -> list[PanFile]:
        target = self.normalize_path(path)
        fid = await self._resolve_fid(target)
        if not fid:
            return []
        files = await self._list_by_fid(fid)
        for item in files:
            item.path = self.join_path(target, item.name)
        files.sort(key=lambda f: (not f.is_dir, f.name.lower()))
        return files

    # ---------------- 转存 ----------------
    @staticmethod
    def parse_share_id(share_url: str) -> str:
        """从分享链接里提取分享码。"""
        match = SHARE_ID_RE.search(str(share_url or ""))
        return match.group(1) if match else ""

    async def save_share(
        self,
        share_url: str,
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        share_id = self.parse_share_id(share_url)
        if not share_id:
            return SaveResult(False, "不是有效的夸克分享链接")
        if not self.cookie:
            return SaveResult(False, "未配置夸克 Cookie")

        # 1) 换 stoken
        token_payload = await self._call(
            "/share/sharepage/token",
            method="POST",
            body={"pwd_id": share_id, "passcode": password or ""},
        )
        stoken = ((token_payload or {}).get("data") or {}).get("stoken")
        if not stoken:
            return SaveResult(False, "获取分享 token 失败（链接失效或提取码错误）")

        # 2) 列出分享内容
        detail = await self._call(
            "/share/sharepage/detail",
            params={
                "pwd_id": share_id,
                "stoken": stoken,
                "pdir_fid": "0",
                "_page": 1,
                "_size": 200,
            },
        )
        items = ((detail or {}).get("data") or {}).get("list") or []
        if not items:
            return SaveResult(False, "分享内没有可转存的文件")
        fid_list = [str(i.get("fid")) for i in items if i.get("fid")]
        token_list = [str(i.get("share_fid_token") or "") for i in items if i.get("fid")]

        # 3) 提交转存
        target = self.normalize_path(target_dir or self.root_path)
        to_pdir_fid = await self._resolve_fid(target) or "0"
        save_payload = await self._call(
            "/share/sharepage/save",
            method="POST",
            params={"app": "clouddrive"},
            body={
                "fid_list": fid_list,
                "fid_token_list": token_list,
                "to_pdir_fid": to_pdir_fid,
                "pwd_id": share_id,
                "stoken": stoken,
                "pdir_fid": "0",
                "scene": "link",
            },
        )
        task_id = ((save_payload or {}).get("data") or {}).get("task_id")
        if not task_id:
            return SaveResult(False, "提交转存失败（可能容量不足或文件已存在）")

        # 4) 轮询任务
        ok = await self._wait_task(str(task_id))
        if not ok:
            return SaveResult(
                True,
                f"转存已提交但未确认完成（task {task_id}），请稍后在网盘中确认",
                saved_path=target,
                file_count=len(fid_list),
            )
        return SaveResult(
            True, f"已转存 {len(fid_list)} 个文件", saved_path=target, file_count=len(fid_list)
        )

    async def _wait_task(self, task_id: str, *, retries: int = 10) -> bool:
        """轮询转存任务状态，成功返回 True。"""
        for index in range(retries):
            payload = await self._call(
                "/task", params={"task_id": task_id, "retry_index": index}
            )
            status = ((payload or {}).get("data") or {}).get("status")
            if status == 2:  # 2 = 成功
                return True
            if status == 3:  # 3 = 失败
                return False
            await asyncio.sleep(1)
        return False

    # ---------------- 其他 ----------------
    async def quota(self) -> PanQuota:
        payload = await self._call("/member", params={"fetch_subscribe": "false"})
        data = (payload or {}).get("data") or {}
        return PanQuota(
            total=int(data.get("total_capacity") or 0),
            used=int(data.get("use_capacity") or 0),
        )

    async def mkdir(self, path: str) -> bool:
        target = self.normalize_path(path)
        parent = self.join_path(*target.split("/")[:-1]) if target != "/" else "/"
        name = target.split("/")[-1]
        if not name:
            return False
        parent_fid = await self._resolve_fid(parent)
        if not parent_fid:
            return False
        payload = await self._call(
            "/file",
            method="POST",
            body={"pdir_fid": parent_fid, "file_name": name, "dir_path": "", "dir_init_lock": False},
        )
        return payload is not None

    async def delete(self, path: str, *, file_id: str | None = None) -> bool:
        fid = file_id
        if not fid:
            target = self.normalize_path(path)
            parent = self.join_path(*target.split("/")[:-1]) if target != "/" else "/"
            name = target.split("/")[-1]
            siblings = await self.list_dir(parent)
            match = next((s for s in siblings if s.name == name), None)
            fid = match.file_id if match else None
        if not fid:
            return False
        payload = await self._call(
            "/file/delete",
            method="POST",
            body={"action_type": 2, "filelist": [fid], "exclude_fids": []},
        )
        return payload is not None

    async def download_url(self, path: str, *, file_id: str | None = None) -> str | None:
        fid = file_id
        if not fid:
            target = self.normalize_path(path)
            parent = self.join_path(*target.split("/")[:-1]) if target != "/" else "/"
            name = target.split("/")[-1]
            siblings = await self.list_dir(parent)
            match = next((s for s in siblings if s.name == name and not s.is_dir), None)
            fid = match.file_id if match else None
        if not fid:
            return None
        payload = await self._call(
            "/file/download",
            method="POST",
            body={"fids": [fid]},
        )
        items = ((payload or {}).get("data") or [])
        if isinstance(items, list) and items:
            return str(items[0].get("download_url") or "") or None
        return None

    async def health_check(self) -> tuple[bool, str]:
        if not self.cookie:
            return False, "未配置 Cookie（浏览器登录夸克后复制完整 Cookie）"
        quota = await self.quota()
        if quota.total <= 0:
            return False, "Cookie 无效或已过期，请重新获取"
        from app.utils.strings import format_size

        return True, f"连接正常，已用 {format_size(quota.used)} / {format_size(quota.total)}"
