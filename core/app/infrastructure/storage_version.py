"""One-time, explicitly authorized V3 business-store reset before opening connections.
/ 在开库前执行用户已授权的一次性 V3 业务库重置。"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

STORAGE_VERSION = 3


@contextmanager
def _exclusive(path: Path):
    """OS advisory lock survives crashes, without a stale lockfile timeout.
    / 崩溃自动释放的系统锁。"""
    with path.open("a+b") as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def prepare_storage(checkpoint: Path) -> dict:
    """Reset only the three named databases once; preserve configs and real files.
    / 只重置指定三库一次；保留配置和实际文件。"""
    checkpoint = checkpoint.absolute()
    root = checkpoint.parent.resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = checkpoint.with_suffix(".storage.json")
    with _exclusive(checkpoint.with_suffix(".storage.lock")):
        if marker.exists():
            metadata = json.loads(marker.read_text(encoding="utf-8"))
            if metadata["version"] > STORAGE_VERSION:
                raise RuntimeError("Unsupported storage version; refusing to reset newer data")
            if metadata["version"] == STORAGE_VERSION:
                return metadata
        targets = [
            checkpoint,
            checkpoint.with_suffix(".sessions.sqlite"),
            checkpoint.with_suffix(".workspace.sqlite"),
        ]
        checked = [Path(str(item) + suffix) for item in targets for suffix in ("", "-wal", "-shm")]
        # Resolve every individual target before deleting any; never glob or recurse.
        # 删除前逐一检查路径；不通配、不递归、不跟随库文件链接到数据目录之外。
        if any(item.is_symlink() or item.resolve().parent != root for item in checked):
            raise RuntimeError("Unsafe database reset path")
        reset = any(item.exists() for item in checked)
        for item in checked:
            item.unlink(missing_ok=True)
        metadata = {"version": STORAGE_VERSION, "epoch": uuid4().hex, "reset": reset}
        temporary = marker.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(metadata, stream)
            stream.flush()
            os.fsync(stream.fileno())
        # Marker precedes opening V3 databases. A crash before it cannot delete new turns.
        # 标记先于 V3 开库；标记前崩溃时尚不存在新轮次，重试不会删除已接收的新数据。
        os.replace(temporary, marker)
        return metadata
