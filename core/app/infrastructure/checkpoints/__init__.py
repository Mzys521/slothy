"""Checkpoint storage resources, independent of business memory.
/ 快照存储资源，不包含业务记忆。"""

from app.infrastructure.checkpoints.sqlite import SQLiteCheckpointStore

__all__ = ["SQLiteCheckpointStore"]
