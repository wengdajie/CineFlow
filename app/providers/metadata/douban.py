"""豆瓣元数据（封面/年份/集数）。

**为什么需要豆瓣**：TMDB 需要用户自备 API Key，且国产剧集的中文标题命中率
一般；豆瓣的 ``subject_suggest`` 是**公开接口、无需 Key**，对中文片名的命中
率极高，非常适合做封面兜底。

**降级策略**：豆瓣有反爬限流（连续请求会短暂 403/空响应）。因此这里做了
三层保护——内存缓存、失败退避（限流期间直接跳过不再打请求）、以及所有异常
都返回空结果。任何情况下都不会让搜索/热榜接口 500。

**防盗链**：豆瓣图片带 Referer 校验，前端必须用 ``referrerpolicy="no-referrer"``
才能正常显示（``web/assets`` 里的 ``posterBox()`` 已处理）。
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import quote

from app.core.logger import get_logger
from app.utils.http import fetch_json

logger = get_logger(__name__)

SUGGEST_URL = "https://movie.douban.com/j/subject_suggest"

#: 缓存 6 小时：封面几乎不变，没必要反复请求（也是对豆瓣的基本礼貌）
_CACHE_TTL = 6 * 3600
#: 被限流后静默这么久，避免雪上加霜
_BACKOFF_SECONDS = 300

_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
#: 触发限流的时间戳；为 0 表示正常
_rate_limited_until: float = 0.0


def _cache_get(key: str) -> list[dict[str, Any]] | None:
    item = _CACHE.get(key)
    if not item:
        return None
    expire_at, value = item
    if expire_at < time.time():
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: list[dict[str, Any]]) -> None:
    _CACHE[key] = (time.time() + _CACHE_TTL, value)


def is_rate_limited() -> bool:
    """当前是否处于限流退避期。"""
    return time.time() < _rate_limited_until


def _mark_rate_limited() -> None:
    global _rate_limited_until
    _rate_limited_until = time.time() + _BACKOFF_SECONDS
    logger.warning("豆瓣接口疑似限流，静默 %s 秒后重试", _BACKOFF_SECONDS)


def reset_state() -> None:
    """清空缓存与退避状态（测试用）。"""
    global _rate_limited_until
    _CACHE.clear()
    _rate_limited_until = 0.0


def _headers() -> dict[str, str]:
    # 豆瓣对无 Referer / 非浏览器 UA 的请求更容易限流
    return {
        "Referer": "https://movie.douban.com/",
        "Accept": "application/json, text/plain, */*",
    }


def _normalize(item: dict[str, Any]) -> dict[str, Any] | None:
    """把豆瓣 suggest 条目转成项目内部统一的元数据字典。"""
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    # episode 只有剧集才有值，用它区分电影/电视剧比 type 字段更可靠
    episode_raw = str(item.get("episode") or "").strip()
    episodes = int(episode_raw) if episode_raw.isdigit() else None
    year_raw = str(item.get("year") or "").strip()
    return {
        "title": title,
        "sub_title": str(item.get("sub_title") or "").strip() or None,
        "year": int(year_raw) if year_raw.isdigit() else None,
        "episodes": episodes,
        "media_type": "tv" if episodes else "movie",
        "poster": str(item.get("img") or "").strip() or None,
        "douban_id": str(item.get("id") or "").strip() or None,
        "douban_url": str(item.get("url") or "").strip() or None,
        "source": "douban",
    }


async def suggest(keyword: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """按关键词搜索豆瓣条目，返回归一化后的元数据列表。

    失败（网络异常/限流/无结果）统一返回空列表，调用方据此回退到下一层封面源。
    """
    word = str(keyword or "").strip()
    if not word:
        return []

    cache_key = word.lower()
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached[:limit]

    if is_rate_limited():
        return []

    payload = await fetch_json(
        f"{SUGGEST_URL}?q={quote(word)}",
        headers=_headers(),
        timeout=8,
    )
    if payload is None:
        # fetch_json 失败返回 None：可能是限流也可能是网络问题，一律退避
        _mark_rate_limited()
        return []
    if not isinstance(payload, list):
        return []

    items = [n for n in (_normalize(i) for i in payload) if n]
    _cache_set(cache_key, items)
    return items[:limit]


#: 认可一条匹配所需的最低分。低于此分说明「只是名字里有几个字一样」，
#: 用它的封面很可能是张错图。**宁可没封面，也不要错封面**——
#: 错图会让用户以为订阅错了片，比留个占位色块糟糕得多。
#:
#: 取 60 的依据：精确同名=100 分必过；模糊包含=50 分（如「三国」命中
#: 「三国演义」）单靠自己不够，必须再有年份或类型佐证才够 60。
MIN_MATCH_SCORE = 60

#: 年份差得多时的扣分。同名不同年基本就是**翻拍/续作**（《无间道》2002 电影
#: vs 2019 剧版），封面完全不同，因此要显著扣分而不是"不加分"就算了。
#:
#: 取 60 是**算过的**：精确同名(100) + 有图(5) - 60 = 45 < 60 门槛，
#: 于是"同名但年份差很远"单靠名字**过不了关**，必须另有佐证。
#: 若只扣 40，该组合仍得 65 分能过门槛，等于没拦住——这是调这个值时踩过的点。
#:
#: 注意这笔账成立的前提是 ``sub_title == title`` 时**不加**那 20 分佐证分
#: （见 :func:`_score`）。中文条目的 sub_title 常与 title 完全相同，
#: 若照加就会变成 125-60=65 分，又把门槛顶过去了。
YEAR_MISMATCH_PENALTY = 60

#: 年份差 2~3 年时的轻度扣分。这个区间既可能是翻拍，也可能是
#: "上映年 vs 引进年 vs 豆瓣录入年"的口径差异，所以只轻扣、不一刀切。
YEAR_NEAR_MISS_PENALTY = 15


#: 季号/续作后缀。用于区分两种「候选名以查询名开头」的情况：
#: 「庆余年」→「庆余年 第一季」是同一部作品的分季（应当匹配），
#: 而「三国」→「三国演义」是**另一部作品**（不应当匹配）。
#: 覆盖实测见到的全部写法：第一季 / 第2部 / 年番4 / 流浪地球2 / 无间道Ⅲ。
_SEASON_SUFFIX_RE = re.compile(
    r"^[\s:：\-—·]*(?:"
    r"第[\s]*[0-9一二三四五六七八九十百]+[\s]*(?:季|部|篇|期)"
    r"|年番[\s]*[0-9]*"
    r"|[0-9]+"
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+"
    r"|(?:season|part|s)[\s]*[0-9]+"
    r")[\s]*$",
    re.IGNORECASE,
)


def _is_season_suffix(remainder: str) -> bool:
    """候选名比查询名多出来的这一截，是否只是个季号标记。

    空字符串不算（那是完全同名，走精确分支）。
    """
    text = str(remainder or "").strip()
    return bool(text) and bool(_SEASON_SUFFIX_RE.match(text))


def _score(item: dict[str, Any], title: str, year: int | None) -> int:
    """给候选条目打匹配分，挑最贴合的那个。

    与 v1.11.0 的区别（M41）：年份**对不上要扣分**，不再只是"不加分"。
    原逻辑下，"同名但年份差很远"仍能拿满 100 分，于是 2002 年的电影
    《无间道》会被配上翻拍剧的封面——这正是路线图里「豆瓣匹配准确率兜底」
    要解决的问题。
    """
    score = 0
    name = str(item.get("title") or "")
    sub = str(item.get("sub_title") or "")
    if name == title:
        score += 100
    elif title and name.startswith(title) and _is_season_suffix(name[len(title) :]):
        # 「分季/续作」关系：查询名是候选名的前缀，且多出来的部分是季号标记
        # （实测常见：庆余年→庆余年 第一季、凡人修仙传→凡人修仙传 年番4、
        # 流浪地球→流浪地球2）。这类几乎肯定是同一部作品的某一季，
        # 给的分要足以单独过门槛，否则「按剧名搜」永远配不到封面。
        score += 60
    elif title and (title in name or name in title):
        # 纯字面包含就弱得多：「三国」也包含在「三国演义」里，但那是另一部作品。
        score += 50
    # ⚠️ 只有当 sub_title **不等于** title 时，这 20 分才算「额外佐证」
    # （命中了别名/原名，如「无间道」↔「無間道」、「复仇者联盟」↔「The Avengers」）。
    # 实测中文作品的 sub_title 极常与 title 完全相同（庆余年/流浪地球/三体 皆是），
    # 此时加分等于把**同一个证据数了两遍**，会把同名不同年的分数顶到
    # 100+20+5-60=65 分，正好越过 60 门槛——年份罚分就被架空了。
    if sub and title and sub != name and title in sub:
        score += 20

    candidate_year = item.get("year")
    if year and candidate_year:
        delta = abs(int(candidate_year) - year)
        if delta == 0:
            score += 30
        elif delta <= 1:
            # 上映年份跨年很常见（12 月上映、次年引进），差 1 年也算靠谱
            score += 12
        elif delta <= 3:
            # 可能是翻拍，也可能只是录入口径差异 —— 轻扣，别一刀切
            score -= YEAR_NEAR_MISS_PENALTY
        else:
            score -= YEAR_MISMATCH_PENALTY
    if item.get("poster"):
        score += 5
    return score


async def match(
    title: str,
    *,
    year: int | None = None,
    media_type: str | None = None,
    min_score: int | None = None,
) -> dict[str, Any] | None:
    """为一部作品找到最匹配的豆瓣条目（用于补封面）。

    **二次校验（M41）**：算完分后还要过 :data:`MIN_MATCH_SCORE` 这道门槛，
    分不够就返回 ``None``。原先用 ``max()`` 无条件取最高分，哪怕最高分只是
    "名字里有俩字一样"也会被采用，于是同名作品互相串封面。

    返回的字典里带 ``match_score`` 与 ``match_confidence``（high/medium），
    前端可据此决定要不要提示用户"这个封面可能不准"。
    ``min_score=0`` 可显式关掉门槛（保留旧行为，供只求有图的场景）。
    """
    name = str(title or "").strip()
    if not name:
        return None
    candidates = await suggest(name, limit=10)
    if not candidates:
        return None
    if media_type in ("movie", "tv"):
        # 类型明确时优先同类型，但没有同类型也不至于放弃（好过没封面）
        same = [c for c in candidates if c.get("media_type") == media_type]
        candidates = same or candidates

    scored = [(c, _score(c, name, year)) for c in candidates]
    best, score = max(scored, key=lambda pair: pair[1])
    threshold = MIN_MATCH_SCORE if min_score is None else int(min_score)
    if score < threshold:
        logger.debug(
            "豆瓣匹配分不足，放弃：%s（最佳候选 %s，%d < %d）",
            name,
            best.get("title"),
            score,
            threshold,
        )
        return None
    result = dict(best)
    result["match_score"] = score
    # 100 分起才叫 high：精确同名 + 至少一项佐证（年份/类型/有图）
    result["match_confidence"] = "high" if score >= 100 else "medium"
    return result


async def poster(
    title: str, *, year: int | None = None, media_type: str | None = None
) -> str | None:
    """只取封面地址的便捷方法。"""
    found = await match(title, year=year, media_type=media_type)
    return found.get("poster") if found else None


async def health_check() -> tuple[bool, str]:
    """探活：豆瓣公开接口无需配置，能搜到结果即视为可用。"""
    if is_rate_limited():
        return False, "豆瓣接口限流中，稍后自动恢复"
    items = await suggest("流浪地球", limit=1)
    if items:
        return True, "豆瓣接口正常"
    return False, "豆瓣接口无响应（可能被限流或网络不通）"
