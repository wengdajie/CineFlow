"""失败后静默 N 秒的共享退避状态机。

替代各 Provider 模块复制粘贴的「global 时间戳 + 三个函数」样板
（douban / douban_chart / bangumi / bili_chart / yt_chart 原本各抄一份，
连 ``global`` 声明这种易漏写法都原样复制）。
"""

from __future__ import annotations

import time


class Backoff:
    """「失败后静默 N 秒」的状态机。"""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._until = 0.0

    def limited(self) -> bool:
        """当前是否处于静默期。"""
        return time.time() < self._until

    def mark(self) -> None:
        """记录一次失败，进入静默期。"""
        self._until = time.time() + self.seconds

    def reset(self) -> None:
        """清空状态（测试用）。"""
        self._until = 0.0
