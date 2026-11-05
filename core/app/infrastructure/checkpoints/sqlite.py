"""SQLite 快照连接的生命周期。 / SQLite checkpoint connection lifecycle."""

from types import TracebackType

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import CheckpointSettings


class SQLiteCheckpointStore:
    """在应用启动/关闭边界管理 SQLite。 / Own SQLite at application startup/shutdown.

    Args:
        settings: 路径与锁等待，默认读环境。 / Path and lock timeout; environment defaults.

    中文：返回官方 Saver 供 Graph 注入；本模块不导入节点、State、模型或领域服务。
    English: Yield the official saver for injection; never import nodes, state, models or domains.
    """

    def __init__(self, settings: CheckpointSettings | None = None) -> None:
        self.settings = settings if settings is not None else CheckpointSettings()
        self.connection: aiosqlite.Connection | None = None
        self.saver: AsyncSqliteSaver | None = None

    async def __aenter__(self) -> AsyncSqliteSaver:
        """打开连接并初始化官方表。 / Open the connection and initialize official tables."""
        if self.connection is not None:
            raise RuntimeError("SQLiteCheckpointStore is already open")
        self.settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = aiosqlite.connect(
            str(self.settings.sqlite_path), timeout=self.settings.busy_timeout_ms / 1000
        )
        try:
            await self.connection
            # 中文：PRAGMA 值只来自已校验整数；FULL 提交与 WAL 共同用于持久化。
            # English: PRAGMA values are validated integers; combine WAL with FULL commits.
            async with self.connection.executescript(
                "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;"
                f"PRAGMA busy_timeout={self.settings.busy_timeout_ms};"
            ):
                pass
            self.saver = AsyncSqliteSaver(
                self.connection,
                serde=JsonPlusSerializer(allowed_msgpack_modules=None, pickle_fallback=False),
            )
            await self.saver.setup()
            return self.saver
        except BaseException:
            # 中文：启动失败或取消也关闭连接，避免残留工作线程与文件句柄。
            # English: Close on failure/cancellation to release worker threads and file handles.
            await self.connection.close()
            self.connection = None
            self.saver = None
            raise

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """退出时关闭资源，不吞掉异常。 / Close resources without suppressing exceptions.

        Args:
            exc_type: 异常类型或 None。 / Exception type, or None.
            exc: 原始异常或 None。 / Original exception, or None.
            traceback: 原始调用栈或 None。 / Original traceback, or None.
        """
        if self.connection is not None:
            await self.connection.close()
            self.connection = None
            self.saver = None
