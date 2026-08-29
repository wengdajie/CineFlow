"""运行期配置层：让「设置」页真正能改，并且改完立即生效、重启仍在。

背景：v1.4.0 之前设置页是**只读**的（ADR-05），理由是"做成可编辑就会出现
界面改了、重启后丢了的假功能"。本模块把那个前提消掉：

1. 用户改动写进 ``settings`` 表（``settings_store``），重启后仍存在；
2. 启动时 ``apply_overrides()`` 把覆盖值写回 ``settings`` 单例，
   于是**所有读 ``settings.X`` 的既有代码零改动就能拿到新值**；
3. 只有**明确登记在 ``EDITABLE`` 里的键**可改。目录、端口、密钥这类
   改了必须重启进程才有意义的项**不放进白名单**，界面上标记为「需重启」。

第 3 点是刻意的：能改的必须真能生效，不能生效的就别给入口（ADR-18）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger
from app.services import settings_store

logger = get_logger(__name__)

#: 运行期配置在 settings 表里的存储键
KEY_RUNTIME = "runtime_config"


@dataclass(frozen=True)
class FieldSpec:
    """一个可在线修改的配置项。"""

    key: str
    #: bool / int / float / str / list / choice
    type: str
    label: str
    #: 供前端渲染的可选值（type=choice 时必填）
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    #: 改动后需要重建调度（周期类配置）
    reschedule: bool = False
    hint: str = ""


def _spec_list() -> list[FieldSpec]:
    """可在线编辑的配置清单。

    只列**运行期读取即生效**的项：服务重启才可能变的（目录、端口、密钥、
    数据库）一律不在此列，避免"能改但没用"。
    """
    return [
        # ---- 整理入库 ----
        FieldSpec("TRANSFER_MODE", "choice", "转移模式",
                  choices=("link", "copy", "move", "softlink", "strm"),
                  hint="link 最快且不占空间，跨盘失败会自动退化为 copy"),
        FieldSpec("MOVIE_TEMPLATE", "str", "电影命名模板"),
        FieldSpec("TV_TEMPLATE", "str", "剧集命名模板"),
        FieldSpec("MIN_FILE_SIZE_MB", "int", "最小文件大小(MB)", minimum=0, maximum=100000),
        # ---- 搜索与订阅策略 ----
        FieldSpec("SEARCH_TIMEOUT", "int", "搜索超时(秒)", minimum=3, maximum=300),
        FieldSpec("SEARCH_MAX_RESULTS", "int", "结果上限", minimum=10, maximum=5000),
        FieldSpec("SEARCH_MAX_PER_SITE", "int", "单站安全阀", minimum=0, maximum=5000,
                  hint="单站最多贡献多少条，0=不限；仅防异常站点返回上万条，日常不必调"),
        FieldSpec("SEARCH_CONCURRENCY", "int", "并发数", minimum=1, maximum=64),
        FieldSpec("AUTO_DOWNLOAD_BEST", "bool", "自动下载最优"),
        FieldSpec("PREFER_RESOLUTIONS", "list", "画质偏好顺序",
                  hint="逗号分隔，越靠前优先级越高"),
        FieldSpec("EXCLUDE_KEYWORDS", "list", "关键词黑名单"),
        FieldSpec("INCLUDE_KEYWORDS", "list", "关键词白名单"),
        FieldSpec("MIN_SEEDERS", "int", "最少做种数", minimum=0, maximum=100000),
        FieldSpec("RADAR_LIMIT_PER_SITE", "int", "雷达每站取量", minimum=10, maximum=1000),
        # ---- 调度（改完需要重建触发器）----
        FieldSpec("SUBSCRIBE_INTERVAL_MINUTES", "int", "订阅巡检间隔(分)",
                  minimum=1, maximum=10080, reschedule=True),
        FieldSpec("RADAR_INTERVAL_MINUTES", "int", "雷达间隔(分)",
                  minimum=0, maximum=10080, reschedule=True),
        FieldSpec("DOWNLOAD_CHECK_INTERVAL_MINUTES", "int", "下载同步间隔(分)",
                  minimum=1, maximum=1440, reschedule=True),
        FieldSpec("LIBRARY_SCAN_CRON", "str", "媒体库扫描 cron",
                  reschedule=True, hint="标准 5 段表达式"),
        # ---- 网盘 ----
        FieldSpec("PAN_AUTO_SAVE", "bool", "网盘自动转存"),
        FieldSpec("PAN_TRANSFER_INTERVAL_MINUTES", "int", "转存重试间隔(分)",
                  minimum=0, maximum=1440, reschedule=True),
        FieldSpec("PAN_TRANSFER_BATCH", "int", "单次转存条数", minimum=1, maximum=500),
        FieldSpec("PAN_SUBSCRIBE_INTERVAL_MINUTES", "int", "分享追更间隔(分)",
                  minimum=0, maximum=10080, reschedule=True),
        FieldSpec("PAN_SUBSCRIBE_MAX_FAILURES", "int", "分享失效阈值", minimum=1, maximum=100),
        # ---- 刮削与分类 ----
        FieldSpec("SCRAPE_ENABLED", "bool", "入库自动刮削"),
        FieldSpec("SCRAPE_IMAGES", "bool", "下载海报图片"),
        FieldSpec("SCRAPE_OVERWRITE", "bool", "覆盖已有 NFO"),
        FieldSpec("SCRAPE_CRON", "str", "补刮 cron", reschedule=True),
        FieldSpec("SCRAPE_BATCH", "int", "单次补刮上限", minimum=1, maximum=5000),
        FieldSpec("CATEGORY_ENABLED", "bool", "分类归档",
                  hint="开启后会在媒体库下多一级分类目录，老库请谨慎"),
        # ---- STRM ----
        FieldSpec("STRM_LINK_MODE", "choice", "STRM 链接模式",
                  choices=("proxy", "direct"),
                  hint="proxy 链接永不过期；direct 为网盘临时直链"),
        FieldSpec("STRM_BASE_URL", "str", "STRM 播放地址前缀",
                  hint="必须是媒体服务器能访问到的地址"),
        FieldSpec("STRM_SYNC_INTERVAL_MINUTES", "int", "STRM 同步间隔(分)",
                  minimum=0, maximum=10080, reschedule=True),
        FieldSpec("STRM_CLEAN_INVALID", "bool", "清理失效 STRM"),
        FieldSpec("STRM_SYNC_METADATA", "bool", "同步随行文件"),
        # ---- 洗版 ----
        FieldSpec("UPGRADE_ENABLED", "bool", "启用洗版",
                  hint="⚠️ 会删除已入库文件", reschedule=True),
        FieldSpec("UPGRADE_SCORE_DELTA", "float", "洗版评分差", minimum=0, maximum=500),
        FieldSpec("UPGRADE_MAX_TIMES", "int", "洗版次数上限", minimum=1, maximum=20),
        # ---- 站点健康 ----
        FieldSpec("SITE_HEALTH_ENABLED", "bool", "站点健康巡检", reschedule=True),
        FieldSpec("SITE_HEALTH_INTERVAL_MINUTES", "int", "健康巡检间隔(分)",
                  minimum=0, maximum=10080, reschedule=True),
        FieldSpec("SITE_HEALTH_FAIL_THRESHOLD", "int", "掉线告警阈值", minimum=1, maximum=50),
        FieldSpec("SITE_AUTO_DISABLE", "bool", "连续失败自动停用站点"),
        # ---- 下载器调度 ----
        FieldSpec("DOWNLOADER_STRATEGY", "choice", "下载器选择策略",
                  choices=("priority", "least_tasks", "round_robin"),
                  hint="多下载器时如何分配任务"),
        FieldSpec("DOWNLOADER_FAILOVER", "bool", "失败自动换下载器"),
        # ---- 榜单订阅 ----
        FieldSpec("RANKING_INTERVAL_MINUTES", "int", "榜单订阅巡检(分)",
                  minimum=0, maximum=10080, reschedule=True),
        FieldSpec("RANKING_MAX_PER_RUN", "int", "单次最多建订阅", minimum=1, maximum=100),
        # ---- 通知与元数据 ----
        FieldSpec("METADATA_CACHE_TTL", "int", "元数据缓存(秒)", minimum=60, maximum=864000),
        FieldSpec("TMDB_LANGUAGE", "str", "TMDB 语言"),
        # ---- ChatOps ----
        FieldSpec("CHATOPS_ENABLED", "bool", "机器人总开关"),
        FieldSpec("CHATOPS_AUTO_DOWNLOAD", "bool", "指令自动下载"),
        FieldSpec("CHATOPS_RESULT_LIMIT", "int", "回复条数", minimum=1, maximum=50),
        FieldSpec("CHATOPS_SESSION_TTL", "int", "会话有效期(秒)", minimum=60, maximum=86400),
        FieldSpec("CHATOPS_ALLOW_USERS", "list", "指令白名单"),
    ]


#: key -> FieldSpec
EDITABLE: dict[str, FieldSpec] = {spec.key: spec for spec in _spec_list()}


def is_editable(key: str) -> bool:
    return key in EDITABLE


def spec_for(key: str) -> FieldSpec | None:
    return EDITABLE.get(key)


def overrides() -> dict[str, Any]:
    """当前已持久化的覆盖值。"""
    data = settings_store.get_setting(KEY_RUNTIME, {}) or {}
    if not isinstance(data, dict):
        return {}
    # 白名单之外的历史残留直接忽略（例如降级后又升级）
    return {k: v for k, v in data.items() if k in EDITABLE}


def coerce(key: str, raw: Any) -> Any:
    """把前端传来的原始值转成配置项要求的类型。

    校验失败抛 ``ValueError``，消息直接面向用户。
    """
    spec = EDITABLE.get(key)
    if spec is None:
        raise ValueError(f"配置项 {key} 不支持在线修改")

    if spec.type == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("1", "true", "yes", "on", "开启"):
            return True
        if text in ("0", "false", "no", "off", "关闭"):
            return False
        raise ValueError(f"{spec.label} 需要布尔值")

    if spec.type in ("int", "float"):
        try:
            value = float(str(raw).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{spec.label} 需要数字") from exc
        if spec.type == "int":
            if value != int(value):
                raise ValueError(f"{spec.label} 需要整数")
            value = int(value)
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"{spec.label} 不能小于 {spec.minimum:g}")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"{spec.label} 不能大于 {spec.maximum:g}")
        return value

    if spec.type == "list":
        if isinstance(raw, list):
            items = [str(item).strip() for item in raw]
        else:
            items = [item.strip() for item in str(raw).replace("、", ",").split(",")]
        return [item for item in items if item]

    if spec.type == "choice":
        text = str(raw).strip()
        if text not in spec.choices:
            raise ValueError(f"{spec.label} 只能是 {'/'.join(spec.choices)}")
        return text

    text = str(raw).strip()
    if text and (key.endswith("_CRON") or key == "LIBRARY_SCAN_CRON"):
        # cron 交给调度模块的解析器校验，避免两份规则漂移
        from app.services.scheduler import validate_cron

        validate_cron(text)
    return text


def _assign(key: str, value: Any) -> None:
    """把值写回 settings 单例。

    ``Settings`` 是 pydantic 模型，用 ``object.__setattr__`` 绕过校验直接赋值
    （值已在 ``coerce`` 里校验过），这样所有读 ``settings.X`` 的代码无需改动。
    """
    object.__setattr__(settings, key, value)


def apply_overrides() -> dict[str, Any]:
    """启动时调用：把持久化的覆盖值套到 settings 单例上。"""
    applied: dict[str, Any] = {}
    for key, raw in overrides().items():
        try:
            value = coerce(key, raw)
        except ValueError as exc:  # 老库里的非法值不应阻塞启动
            logger.warning("运行期配置 %s 无效，已忽略：%s", key, exc)
            continue
        _assign(key, value)
        applied[key] = value
    if applied:
        logger.info("已应用 %d 项运行期配置覆盖：%s", len(applied), sorted(applied))
    return applied


def update(values: dict[str, Any]) -> dict[str, Any]:
    """校验 → 持久化 → 立即生效。

    **先全部校验再落库**：一次提交里有一项非法就整体拒绝，
    避免出现"改了 5 项、第 3 项报错、前 2 项已经生效"的半吊子状态。
    """
    if not values:
        raise ValueError("没有需要保存的配置")

    coerced: dict[str, Any] = {}
    for key, raw in values.items():
        coerced[key] = coerce(key, raw)

    stored = overrides()
    stored.update(coerced)
    settings_store.set_setting(KEY_RUNTIME, stored)
    for key, value in coerced.items():
        _assign(key, value)

    if any(EDITABLE[key].reschedule for key in coerced):
        _reschedule()
    logger.info("运行期配置已更新：%s", sorted(coerced))
    return coerced


def reset(keys: list[str] | None = None) -> list[str]:
    """清除覆盖，回到 .env / config.yaml 的静态值。

    静态默认值来自进程启动时的快照，所以这里从一份「干净的 Settings」里取原值，
    而不是猜。
    """
    stored = overrides()
    targets = [key for key in (keys or list(stored)) if key in stored]
    if not targets:
        return []

    from app.core.config import Settings, _yaml_source

    pristine = Settings(**_yaml_source())
    for key in targets:
        stored.pop(key, None)
        _assign(key, getattr(pristine, key))
    settings_store.set_setting(KEY_RUNTIME, stored)
    if any(EDITABLE[key].reschedule for key in targets):
        _reschedule()
    logger.info("运行期配置已重置：%s", targets)
    return targets


def _reschedule() -> None:
    """周期类配置改动后重建内置任务的触发器。"""
    from app.services.scheduler import scheduler_service

    try:
        scheduler_service.refresh_builtin_jobs()
    except Exception as exc:  # pragma: no cover - 调度未启动时忽略
        logger.warning("重建定时任务失败: %s", exc)


def describe(key: str) -> dict[str, Any]:
    """给前端的字段元信息（不含值）。"""
    spec = EDITABLE.get(key)
    if spec is None:
        return {"editable": False}
    return {
        "editable": True,
        "type": spec.type,
        "label": spec.label,
        "choices": list(spec.choices),
        "minimum": spec.minimum,
        "maximum": spec.maximum,
        "reschedule": spec.reschedule,
        "hint": spec.hint,
    }
