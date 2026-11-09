"""Exact, lifetime-bounded session locks. / 精确匹配、随使用者释放的会话锁。"""

import asyncio
from weakref import WeakValueDictionary


class SessionLocks:
    """Keep distinct sessions independent without retaining idle locks forever.
    / 不同会话互不等待，同时不永久保存空闲锁。"""

    def __init__(self):
        self._locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

    def for_session(self, key: str) -> asyncio.Lock:
        """Return the same lock while a holder or waiter still owns it.
        / 持有者或等待者存在时返回同一把锁；单事件循环内此方法不让出执行权。"""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock
