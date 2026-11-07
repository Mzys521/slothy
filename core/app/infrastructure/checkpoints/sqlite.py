"""SQLite checkpoint connection lifecycle.
/ SQLite 快照连接的生命周期。"""

from types import TracebackType

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import CheckpointSettings


class SQLiteCheckpointStore:
    """Own the SQLite connection at application startup and shutdown.

    Args:
        settings: Validated path and lock timeout; environment defaults when omitted.
            / 已校验的路径与锁等待；省略时使用环境默认值。

    Yields the official LangGraph saver for injection; this module never imports nodes,
    state, models or domain services. / 返回官方 Saver 供 Graph 注入；本模块不导入节点、
    State、模型或领域服务。
    """

    def __init__(self, settings: CheckpointSettings | None = None) -> None:
        """Store the resolved settings and pending handles.

        Args:
            settings: Checkpoint settings; loaded from the environment when None.
                / 快照配置；为 None 时读取环境。
        """
        self.settings = settings if settings is not None else CheckpointSettings()
        self.connection: aiosqlite.Connection | None = None
        self.saver: AsyncSqliteSaver | None = None

    async def __aenter__(self) -> AsyncSqliteSaver:
        """Open the database, apply PRAGMAs and initialize saver tables.

        Returns:
            The LangGraph SQLite saver bound to the opened connection.
            / 绑定到已打开连接的 LangGraph SQLite Saver。
        Raises:
            RuntimeError: When the store is already open. / 存储已处于打开状态时抛出。
            Exception: When the connection cannot be established; handles are closed first.
                / 连接建立失败时抛出；抛出前会先关闭句柄。
        """
        if self.connection is not None:
            raise RuntimeError("SQLiteCheckpointStore is already open")
        self.settings.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = aiosqlite.connect(
            str(self.settings.sqlite_path), timeout=self.settings.busy_timeout_ms / 1000
        )
        try:
            await self.connection
            # English: PRAGMA values are validated integers; combine WAL with FULL commits.
            # 中文：PRAGMA 值只来自已校验整数；FULL 提交与 WAL 共同用于持久化。
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
            # English: Close on failure/cancellation to release worker threads and file handles.
            # 中文：启动失败或取消也关闭连接，避免残留工作线程与文件句柄。
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
        """Close the connection without suppressing exceptions.

        Args:
            exc_type: Exception type, or None. / 异常类型或 None。
            exc: Original exception, or None. / 原始异常或 None。
            traceback: Original traceback, or None. / 原始调用栈或 None。
        """
        if self.connection is not None:
            await self.connection.close()
            self.connection = None
            self.saver = None
