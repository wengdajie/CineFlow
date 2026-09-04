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

#: 列分享内容时单页条数与最多翻几页。夸克单页上限约 200，
#: 50 页 × 200 = 1 万个文件，足够覆盖任何正常剧集分享。
_SHARE_PAGE_SIZE = 200
_SHARE_MAX_PAGES = 50
#: 递归进子目录的最大深度与最多收集多少个文件（防嵌套过深/成环把巡检卡死）
_SHARE_MAX_DEPTH = 4
_SHARE_MAX_FILES = 3000


@register
class QuarkStorage(BasePanStorage):
    """夸克网盘。"""

    name = "quark"
    display_name = "夸克网盘"

    # 夸克开放了完整的文件管理接口，能力位全开
    supports_rename = True
    supports_move = True
    supports_search = True
    supports_keepalive = True

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

    async def _share_token(self, share_id: str, password: str | None) -> str | None:
        """第 1 步：用分享码（+提取码）换 stoken。"""
        payload = await self._call(
            "/share/sharepage/token",
            method="POST",
            body={"pwd_id": share_id, "passcode": password or ""},
        )
        return ((payload or {}).get("data") or {}).get("stoken")

    async def _share_items(
        self, share_id: str, stoken: str, pdir_fid: str = "0"
    ) -> list[dict[str, Any]]:
        """第 2 步：列出分享内**某一层**的文件（含 fid 与 share_fid_token）。

        ``pdir_fid`` 默认 ``"0"``（分享根）。想看子目录就把目录自己的 fid 传进来
        —— 这是 :meth:`list_share` 能递归的前提。

        **分页必须翻到底**：``_size`` 再大也是一页。整季连载分享常有
        30+ 个文件，写死单页只能看到前一页，后面的集数**永远**不会被
        追更发现（增量判断看不到就等于不存在）。这里循环翻页到取空为止。
        """
        collected: list[dict[str, Any]] = []
        page = 1
        while page <= _SHARE_MAX_PAGES:
            detail = await self._call(
                "/share/sharepage/detail",
                params={
                    "pwd_id": share_id,
                    "stoken": stoken,
                    "pdir_fid": pdir_fid,
                    "_page": page,
                    "_size": _SHARE_PAGE_SIZE,
                },
            )
            batch = ((detail or {}).get("data") or {}).get("list") or []
            if not isinstance(batch, list) or not batch:
                break
            collected.extend(item for item in batch if isinstance(item, dict))
            if len(batch) < _SHARE_PAGE_SIZE:
                break  # 不满一页说明已经是最后一页
            page += 1
        return collected

    async def list_share(
        self, share_url: str, *, password: str | None = None
    ) -> list[PanFile]:
        """列出分享内部的文件清单（供「分享追更」做增量判断）。

        **必须递归进子目录**：实测国内剧集分享极少把文件摊在根上，
        典型结构是 ``/喜剧之王单口季 第三季/EP01.mp4``。只列根目录时
        追更看到的是**一个目录、零个文件**，``match_files()`` 又会跳过
        目录（``item.is_dir`` 直接 continue），于是每轮巡检都得出
        「没有新增文件」—— 一个永远不会更新、也永远不报错的订阅。
        这是「网盘追更不生效」最常见的成因，比 Cookie 失效更隐蔽，
        因为它**不产生任何失败**。

        深度与总数都设了闸门（:data:`_SHARE_MAX_DEPTH` /
        :data:`_SHARE_MAX_FILES`）：分享可以嵌套很深甚至成环，
        没有上限时一次巡检能把自己卡死在别人的网盘结构里。
        截断时如实记日志，而不是假装列全了（ADR-82）。
        """
        share_id = self.parse_share_id(share_url)
        if not share_id or not self.cookie:
            return []
        stoken = await self._share_token(share_id, password)
        if not stoken:
            return []

        files: list[PanFile] = []
        # (fid, 该层在分享内的路径前缀, 深度)
        queue: list[tuple[str, str, int]] = [("0", "", 0)]
        seen_dirs: set[str] = {"0"}
        truncated = False

        while queue:
            fid, prefix, depth = queue.pop(0)
            for item in await self._share_items(share_id, stoken, fid):
                if not item.get("fid"):
                    continue
                name = str(item.get("file_name") or "")
                if not name:
                    continue
                is_dir = bool(item.get("dir") or item.get("file_type") == 0)
                child_fid = str(item["fid"])
                path = f"{prefix}/{name}"
                if is_dir:
                    # 目录本身也放进结果：调用方（整体转存/界面展示）可能要用；
                    # match_files() 会自己跳过目录，不会误转。
                    files.append(
                        PanFile(
                            name=name,
                            path=path,
                            is_dir=True,
                            size=0,
                            file_id=child_fid,
                            extra={
                                "share_fid_token": str(
                                    item.get("share_fid_token") or ""
                                )
                            },
                        )
                    )
                    if depth + 1 <= _SHARE_MAX_DEPTH and child_fid not in seen_dirs:
                        seen_dirs.add(child_fid)
                        queue.append((child_fid, path, depth + 1))
                    continue
                files.append(
                    PanFile(
                        name=name,
                        path=path,
                        is_dir=False,
                        size=int(item.get("size") or 0),
                        file_id=child_fid,
                        # share_fid_token 是转存时必须回传的凭据，随文件一起带出来
                        extra={
                            "share_fid_token": str(item.get("share_fid_token") or ""),
                            # 追更重命名要按「落地后的路径」找文件，而落地路径
                            # 取决于它在分享内的父目录，所以一起带出来。
                            "share_dir": prefix or "/",
                        },
                    )
                )
                if len(files) >= _SHARE_MAX_FILES:
                    truncated = True
                    break
            if truncated:
                break

        if truncated:
            logger.warning(
                "夸克分享 %s 文件数超过 %d，只列出了前一部分（增量判断可能不完整）",
                share_id,
                _SHARE_MAX_FILES,
            )
        return files

    async def save_share_files(
        self,
        share_url: str,
        files: list[PanFile],
        *,
        password: str | None = None,
        target_dir: str | None = None,
    ) -> SaveResult:
        """只转存分享内指定的文件（增量转存）。"""
        if not files:
            return SaveResult(False, "没有需要转存的文件")
        share_id = self.parse_share_id(share_url)
        if not share_id:
            return SaveResult(False, "不是有效的夸克分享链接")
        if not self.cookie:
            return SaveResult(False, "未配置夸克 Cookie")
        stoken = await self._share_token(share_id, password)
        if not stoken:
            return SaveResult(False, "获取分享 token 失败（链接失效或提取码错误）")
        fid_list = [str(item.file_id) for item in files if item.file_id]
        token_list = [str((item.extra or {}).get("share_fid_token") or "") for item in files if item.file_id]
        if not fid_list:
            return SaveResult(False, "选中的文件缺少 fid，无法转存")
        return await self._submit_save(
            share_id, stoken, fid_list, token_list, target_dir
        )

    async def _submit_save(
        self,
        share_id: str,
        stoken: str,
        fid_list: list[str],
        token_list: list[str],
        target_dir: str | None,
    ) -> SaveResult:
        """第 3~4 步：提交转存任务并轮询结果。"""
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
        ok = await self._wait_task(str(task_id))
        if not ok:
            return SaveResult(
                True,
                f"转存已提交但未确认完成（task {task_id}），请稍后在网盘中确认",
                saved_path=target,
                file_count=len(fid_list),
            )
        return SaveResult(
            True,
            f"已转存 {len(fid_list)} 个文件",
            saved_path=target,
            file_count=len(fid_list),
        )

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
        stoken = await self._share_token(share_id, password)
        if not stoken:
            return SaveResult(False, "获取分享 token 失败（链接失效或提取码错误）")

        # 2) 列出分享内容
        items = await self._share_items(share_id, stoken)
        if not items:
            return SaveResult(False, "分享内没有可转存的文件")
        fid_list = [str(i.get("fid")) for i in items if i.get("fid")]
        token_list = [str(i.get("share_fid_token") or "") for i in items if i.get("fid")]

        # 3~4) 提交转存并轮询（与增量转存共用同一段逻辑）
        return await self._submit_save(
            share_id, stoken, fid_list, token_list, target_dir
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

    # ---------------- 文件管理 ----------------
    async def _fid_of(self, path: str, file_id: str | None = None) -> str:
        """把「路径或已知 fid」统一解析成 fid，避免每个方法都重复一遍。"""
        if file_id:
            return file_id
        target = self.normalize_path(path)
        if target == "/":
            return "0"
        parent = self.join_path(*target.split("/")[:-1])
        name = target.split("/")[-1]
        siblings = await self.list_dir(parent)
        match = next((s for s in siblings if s.name == name), None)
        return match.file_id or "" if match else ""

    async def rename(
        self, path: str, new_name: str, *, file_id: str | None = None
    ) -> bool:
        name = str(new_name or "").strip()
        if not name:
            return False
        fid = await self._fid_of(path, file_id)
        if not fid or fid == "0":
            return False
        payload = await self._call(
            "/file/rename",
            method="POST",
            body={"fid": fid, "file_name": name},
        )
        return payload is not None

    async def move(
        self, path: str, target_dir: str, *, file_id: str | None = None
    ) -> bool:
        fid = await self._fid_of(path, file_id)
        to_fid = await self._resolve_fid(target_dir)
        # to_fid 为空说明目标目录不存在；根目录是 "0" 属于合法值，所以只判空串
        if not fid or fid == "0" or not to_fid:
            return False
        payload = await self._call(
            "/file/move",
            method="POST",
            body={
                "action_type": 1,
                "filelist": [fid],
                "to_pdir_fid": to_fid,
                "exclude_fids": [],
            },
        )
        return payload is not None

    async def search(self, keyword: str, *, limit: int = 50) -> list[PanFile]:
        word = str(keyword or "").strip()
        if not word:
            return []
        payload = await self._call(
            "/file/search",
            params={
                "q": word,
                "_page": 1,
                "_size": max(1, min(int(limit or 50), 200)),
                "_fetch_total": 1,
                "_sort": "file_type:desc,updated_at:desc",
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
                    # 搜索结果里夸克会带 path_str（所在目录），拼出完整路径便于直接操作
                    path=self.join_path(str(item.get("path_str") or "/"), name),
                    is_dir=bool(item.get("dir") or item.get("file_type") == 0),
                    size=int(item.get("size") or 0),
                    file_id=str(item.get("fid") or ""),
                    modified_at=str(item.get("updated_at") or "") or None,
                )
            )
        return files

    async def keep_alive(self) -> tuple[bool, str]:
        """夸克保活：查一次容量即可刷新会话活跃度。"""
        if not self.cookie:
            return False, "未配置 Cookie"
        quota = await self.quota()
        if quota.total <= 0:
            return False, "Cookie 已失效，请重新登录夸克并复制 Cookie"
        return True, "Cookie 有效"

    async def health_check(self) -> tuple[bool, str]:
        if not self.cookie:
            return False, "未配置 Cookie（浏览器登录夸克后复制完整 Cookie）"
        quota = await self.quota()
        if quota.total <= 0:
            return False, "Cookie 无效或已过期，请重新获取"
        from app.utils.strings import format_size

        return True, f"连接正常，已用 {format_size(quota.used)} / {format_size(quota.total)}"
