"""Inspect, adopt and recover the three business databases before opening them.

The startup path never deletes business data by default. A missing or outdated side marker
triggers a read-only schema probe of the existing databases; only a verified current schema is
adopted and re-marked, while older, newer, damaged or undeterminable data raises
``StorageRecoveryRequired`` and is left untouched for an explicit user decision.
/ 开库前检查、接管并恢复三个业务库。启动链路默认不再删除任何业务数据；旁置标记缺失或过期时
只读探测已有库的结构，仅接管已验证的当前结构并补建标记；旧版、未来版、损坏或无法判定的数据
抛出 ``StorageRecoveryRequired``，保留原文件等待用户显式选择。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

STORAGE_VERSION = 3
StorageStatus = Literal["empty", "current", "legacy", "future", "corrupt", "unknown"]
SUFFIXES = ("", "-wal", "-shm")
# Required tables and columns prove which schema the stored data already follows. Additive
# migrations keep every listed column in the current layout, so a verified match means the
# existing rows can be opened without rewriting them.
# 必需的表与列用于判定数据已遵循哪一版结构；增量迁移保留下列全部列，因此匹配即表示现有行可
# 不重写地直接打开。
REQUIRED_SCHEMA: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "checkpoints": (
        ("checkpoints", ("thread_id", "checkpoint_id", "checkpoint")),
        ("writes", ("thread_id", "checkpoint_id", "task_id")),
    ),
    "sessions": (
        (
            "conversation_turns",
            (
                "user_id",
                "thread_id",
                "run_id",
                "user_input",
                "response",
                "attachment_ids",
                "explicit_references",
                "memory_trace",
                "learning_generation",
            ),
        ),
        ("conversation_threads", ("user_id", "thread_id", "workspace_id", "pinned")),
        ("conversation_files", ("id", "user_id", "thread_id", "content")),
        ("conversation_workspaces", ("user_id", "id", "name", "directory")),
        ("memory_configuration", ("user_id", "revision", "configuration_json")),
    ),
    "workspace": (
        (
            "workspace_memories",
            ("id", "user_id", "memory_key", "dimension", "value_json", "protected"),
        ),
        ("memory_claims", ("id", "user_id", "semantic_json", "pending_reason")),
        ("memory_evidence", ("claim_id", "thread_id", "run_id", "qualified")),
        ("workspace_profiles", ("user_id", "version")),
        ("memory_meta", ("user_id", "memory_index_version")),
    ),
}
# One core table per database distinguishes recognizable legacy data, which the existing
# additive migrations can upgrade in place, from data written by an unrelated program.
# 每个库一个核心表：可识别的旧版数据可经现有增量迁移原地升级，无关程序写入的数据则无法判定。
CORE_TABLES = {
    "checkpoints": "checkpoints",
    "sessions": "conversation_turns",
    "workspace": "workspace_memories",
}


@dataclass(frozen=True)
class StorageState:
    """Verified shape of the existing business databases. / 已有业务库的已核验状态。

    Attributes:
        status: One of empty, current, legacy, future, corrupt or unknown. / 状态取值。
        version: Highest detected schema version; 0 when nothing was versioned yet. / 最高结构版本。
        detail: Diagnostics without file contents. / 不含文件内容的诊断说明。
        databases: Per-database probe results. / 每个数据库的探测结果。
    """

    status: StorageStatus
    version: int = 0
    detail: str = ""
    databases: tuple[dict, ...] = ()

    @property
    def adoptable(self) -> bool:
        """Whether the existing data matches the running schema. / 现有数据是否匹配当前结构。"""
        return self.status == "current"

    @property
    def recoverable(self) -> bool:
        """Whether data exists that only the user may decide about. / 是否仅能由用户决定。"""
        return self.status in {"legacy", "future", "corrupt", "unknown"}

    def as_dict(self) -> dict:
        """Return a JSON-safe projection for diagnostics. / 返回可安全用于诊断的映射。"""
        return {
            "status": self.status,
            "version": self.version,
            "detail": self.detail,
            "adoptable": self.adoptable,
            "databases": [dict(item) for item in self.databases],
        }


class StorageRecoveryRequired(RuntimeError):
    """Existing business data cannot be opened without an explicit user decision.

    / 已有业务数据需要用户显式决定后才能继续启动。
    """

    def __init__(self, state: StorageState) -> None:
        """Store the inspected state with the error. / 把已核验状态附加到异常上。

        Args:
            state: Verified shape of the existing data. / 已有数据的已核验状态。
        """
        self.state = state
        super().__init__(f"Storage recovery required: {state.status}. {state.detail}".strip())


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


def storage_paths(checkpoint: Path) -> list[Path]:
    """Return the three named databases for one data directory. / 返回数据目录的三个业务库路径。"""
    checkpoint = checkpoint.absolute()
    return [
        checkpoint,
        checkpoint.with_suffix(".sessions.sqlite"),
        checkpoint.with_suffix(".workspace.sqlite"),
    ]


def _kind(path: Path, checkpoint: Path) -> str:
    """Map one database path onto its schema expectation. / 把库路径映射到结构预期。"""
    name = path.name
    if name == checkpoint.name:
        return "checkpoints"
    if name.endswith(".sessions.sqlite"):
        return "sessions"
    return "workspace"


def _with_suffixes(path: Path) -> list[Path]:
    """Return the database and its SQLite side files. / 返回数据库文件及其 SQLite 边车文件。"""
    return [Path(str(path) + suffix) for suffix in SUFFIXES]


def _copy_for_probe(path: Path, directory: Path) -> Path:
    """Copy a database with its WAL so probing never touches the original.
    / 连同 WAL 一起复制数据库，探测过程绝不改动原文件。"""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / path.name
    for source in _with_suffixes(path):
        if source.exists():
            shutil.copy2(source, directory / source.name)
    return target


def _probe(path: Path, kind: str) -> dict:
    """Read one copied database and classify its schema. / 读取一个副本并判定其结构。

    Args:
        path: Copied database path inside a temporary directory. / 临时目录中的数据库副本路径。
        kind: One of checkpoints, sessions or workspace. / checkpoints、sessions 或 workspace。

    Returns:
        Probe record with version, status, table count and detail.
        / 含版本、状态、表数量与说明的记录。
    """
    requirements = REQUIRED_SCHEMA[kind]
    record = {"version": 0, "status": "unknown", "tables": 0, "detail": ""}
    try:
        connection = sqlite3.connect(path, timeout=5)
    except sqlite3.Error as error:
        return {**record, "status": "corrupt", "detail": f"cannot open: {error}"}
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0] or 0)
        record["version"] = version
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        record["tables"] = len(names)
        if not names:
            record["status"] = "empty"
            record["detail"] = "no tables"
            return record
        if version > STORAGE_VERSION:
            return {**record, "status": "future", "detail": f"schema version {version}"}
        missing = []
        for table, columns in requirements:
            if table not in names:
                missing.append(table)
                continue
            present = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            missing.extend(f"{table}.{column}" for column in columns if column not in present)
        if version < STORAGE_VERSION and version > 0:
            return {**record, "status": "legacy", "detail": f"schema version {version}"}
        if missing:
            # A recognizable core table proves this is our earlier layout, which the current
            # additive migrations can upgrade without dropping rows.
            # 存在核心表说明是本应用早期布局，可由当前增量迁移升级且不丢弃行。
            core = CORE_TABLES[kind]
            if core in names:
                return {
                    **record,
                    "status": "legacy",
                    "detail": "upgradable legacy layout",
                }
            return {
                **record,
                "status": "unknown",
                "detail": "missing " + ", ".join(sorted(missing)[:6]),
            }
        return {**record, "status": "current", "detail": f"schema version {version or 0}"}
    except sqlite3.Error as error:
        return {**record, "status": "corrupt", "detail": f"unreadable: {error}"}
    finally:
        connection.close()


def inspect_storage(checkpoint: Path) -> StorageState:
    """Probe the three business databases without modifying them. / 只读探测三个业务库。

    Copies each database with its WAL into a temporary directory before opening it, so an
    interrupted or encrypted file never changes, locks or corrupts the original. The side
    marker is only an auxiliary signal and is handled by :func:`prepare_storage`.

    / 开库前把每个库连同 WAL 复制到临时目录，损坏或加密的文件不会被修改、加锁或损坏；旁置标记
    只作为辅助信号，由 :func:`prepare_storage` 处理。

    Args:
        checkpoint: Base checkpoint database path. / 基础检查点数据库路径。

    Returns:
        Verified state of every existing database. / 每个已有数据库的已核验状态。
    """
    checkpoint = checkpoint.absolute()
    targets = storage_paths(checkpoint)
    present = [path for path in targets if path.exists()]
    if not present:
        return StorageState("empty", 0, "No business database exists yet.")
    databases: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="slothy-storage-probe-") as temporary:
        for path in targets:
            exists = path.exists()
            record = {
                "name": path.name,
                "exists": exists,
                "bytes": path.stat().st_size if exists else 0,
                "version": 0,
                "status": "absent",
                "tables": 0,
                "detail": "",
            }
            if exists:
                probe = _probe(
                    _copy_for_probe(path, Path(temporary) / path.name),
                    _kind(path, checkpoint),
                )
                record.update(probe)
            databases.append(record)
    statuses = [item["status"] for item in databases]
    version = max((item["version"] for item in databases), default=0)
    if all(status in {"absent", "empty"} for status in statuses):
        status: StorageStatus = "empty"
        detail = "Existing database files contain no schema yet."
    elif "corrupt" in statuses:
        status, detail = "corrupt", "A business database cannot be read."
    elif "future" in statuses:
        status, detail = "future", "The business data uses a newer schema than this build."
    elif "legacy" in statuses:
        status, detail = "legacy", "The business data uses an older schema."
    elif "unknown" in statuses:
        status, detail = "unknown", "The business data schema cannot be verified."
    elif "current" in statuses and any(status in {"absent", "empty"} for status in statuses):
        status, detail = "unknown", "Only part of the business data exists."
    else:
        status, detail = "current", "The business data matches the running schema."
    return StorageState(status, version, detail, tuple(databases))


def _read_marker(marker: Path) -> dict | None:
    """Read the side marker; unusable markers are treated as missing. / 读取旁置标记。

    Args:
        marker: Marker path next to the checkpoint database. / 检查点库旁的标记路径。

    Returns:
        Parsed marker metadata, or None when absent or unreadable.
        / 已解析元数据；缺失或不可读时 None。
    """
    if not marker.exists():
        return None
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    version = metadata.get("version")
    if not isinstance(version, int):
        return None
    return metadata


def _write_marker(marker: Path, *, epoch: str | None = None, **extra) -> dict:
    """Atomically write the side marker before V3 databases are opened.
    / 在打开 V3 数据库前原子写入旁置标记。

    Args:
        marker: Marker path to replace. / 待替换的标记路径。
        epoch: Data epoch to keep, or None for a new one. / 保留的数据纪元；None 表示新建。
        extra: Additional diagnostic fields recorded in the marker. / 额外记录的诊断字段。

    Returns:
        The stored marker metadata. / 已存储的标记元数据。
    """
    metadata = {
        "version": STORAGE_VERSION,
        "epoch": epoch or uuid4().hex,
        "reset": False,
        "recorded_at": datetime.now(UTC).isoformat(),
        **extra,
    }
    temporary = marker.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(metadata, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, marker)
    return metadata


def _require_safe(paths: list[Path], root: Path) -> None:
    """Reject linked or escaping database paths before writing or deleting.
    / 写入或删除前拒绝链接或越界路径。

    Args:
        paths: Candidate database files. / 候选数据库文件。
        root: Data directory the files must stay inside. / 数据目录根。

    Raises:
        StorageRecoveryRequired: When a path is a link or leaves the data directory. /
            路径为链接或越出数据目录时抛出。
    """
    for path in paths:
        if path.is_symlink() or path.resolve().parent != root:
            state = StorageState("unknown", 0, "Unsafe storage path; refusing to modify it.")
            raise StorageRecoveryRequired(state)


def _stamp_versions(checkpoint: Path) -> None:
    """Record the current schema version inside adopted databases.
    / 在接管的库内记录当前结构版本。"""
    for path in storage_paths(checkpoint):
        if not path.exists():
            continue
        try:
            connection = sqlite3.connect(path, timeout=5)
            try:
                connection.execute(f"PRAGMA user_version = {STORAGE_VERSION}")
                connection.commit()
            finally:
                connection.close()
        except sqlite3.Error:
            # A read-only or damaged file still opens through the normal startup path; the
            # missing version stamp is not a reason to refuse already verified data.
            # 只读或受损文件仍按正常启动路径打开；缺少版本标记不应拒绝已验证可用的数据。
            continue


def mark_storage_current(checkpoint: Path, **extra) -> dict:
    """Stamp the current schema version and write the side marker.
    / 记录当前结构版本并写入旁置标记。

    Args:
        checkpoint: Base checkpoint database path. / 基础检查点数据库路径。
        extra: Additional diagnostic fields recorded in the marker. / 额外记录的诊断字段。

    Returns:
        Stored marker metadata. / 已存储的标记元数据。

    Raises:
        StorageRecoveryRequired: When a database path is a link or leaves the data directory. /
            数据库路径为链接或越出数据目录时抛出。
    """
    checkpoint = checkpoint.absolute()
    root = checkpoint.parent.resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = checkpoint.with_suffix(".storage.json")
    with _exclusive(checkpoint.with_suffix(".storage.lock")):
        files = [file for path in storage_paths(checkpoint) for file in _with_suffixes(path)]
        _require_safe(files, root)
        _stamp_versions(checkpoint)
        return _write_marker(marker, **extra)


def prepare_storage(checkpoint: Path) -> dict:
    """Adopt verified current data and mark it, never deleting existing data by default.
    / 接管已核验的当前数据并补建标记；默认绝不删除已有数据。

    Args:
        checkpoint: Base checkpoint database path. / 基础检查点数据库路径。

    Returns:
        Stored marker metadata with version and data epoch. / 含版本与数据纪元的标记元数据。

    Raises:
        StorageRecoveryRequired: When existing data needs an explicit user decision. /
            已有数据需要用户显式决定时抛出。
    """
    checkpoint = checkpoint.absolute()
    root = checkpoint.parent.resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = checkpoint.with_suffix(".storage.json")
    with _exclusive(checkpoint.with_suffix(".storage.lock")):
        files = [file for path in storage_paths(checkpoint) for file in _with_suffixes(path)]
        _require_safe(files, root)
        metadata = _read_marker(marker)
        if metadata is not None:
            if metadata["version"] > STORAGE_VERSION:
                raise StorageRecoveryRequired(
                    StorageState(
                        "future",
                        metadata["version"],
                        "The storage marker was written by a newer build.",
                    )
                )
            if metadata["version"] == STORAGE_VERSION:
                return metadata
            state = inspect_storage(checkpoint)
            raise StorageRecoveryRequired(
                StorageState(
                    "legacy",
                    metadata["version"],
                    f"Storage marker version {metadata['version']} "
                    f"is older than {STORAGE_VERSION}.",
                    state.databases,
                )
            )
        state = inspect_storage(checkpoint)
        if state.status == "empty":
            return _write_marker(marker, reset=False, adopted=False)
        if state.adoptable:
            _stamp_versions(checkpoint)
            return _write_marker(marker, reset=False, adopted=True, adopted_version=state.version)
        raise StorageRecoveryRequired(state)


def adopt_storage(checkpoint: Path) -> dict:
    """Accept verified existing data after a user choice. / 用户确认后接管已验证的已有数据。

    Args:
        checkpoint: Base checkpoint database path. / 基础检查点数据库路径。

    Returns:
        Stored marker metadata. / 已存储的标记元数据。

    Raises:
        StorageRecoveryRequired: When no compatible data can be adopted. / 没有可接管数据时抛出。
    """
    checkpoint = checkpoint.absolute()
    marker = checkpoint.with_suffix(".storage.json")
    root = checkpoint.parent.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with _exclusive(checkpoint.with_suffix(".storage.lock")):
        files = [file for path in storage_paths(checkpoint) for file in _with_suffixes(path)]
        _require_safe(files, root)
        metadata = _read_marker(marker)
        if metadata is not None and metadata["version"] == STORAGE_VERSION:
            return metadata
        state = inspect_storage(checkpoint)
        if not state.adoptable:
            raise StorageRecoveryRequired(state)
    return mark_storage_current(checkpoint, adopted=True, adopted_version=state.version)


def _same_bytes(left: Path, right: Path) -> bool:
    """Compare two files byte for byte. / 逐字节比较两个文件。

    Args:
        left: Original file. / 原文件。
        right: Copied file. / 副本文件。

    Returns:
        True when both files have identical contents. / 内容完全相同时返回 True。
    """
    try:
        with left.open("rb") as first, right.open("rb") as second:
            while True:
                original, copied = first.read(1 << 20), second.read(1 << 20)
                if original != copied:
                    return False
                if not original:
                    return True
    except OSError:
        return False


def backup_databases(checkpoint: Path) -> Path:
    """Copy the three databases with their side files into a verified backup directory.
    / 把三个库及其边车文件复制到经过校验的备份目录。

    Args:
        checkpoint: Base checkpoint database path. / 基础检查点数据库路径。

    Returns:
        The created backup directory. / 新建的备份目录。

    Raises:
        StorageRecoveryRequired: When a copy cannot be verified. / 副本无法校验时抛出。
    """
    checkpoint = checkpoint.absolute()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = checkpoint.parent / f"{checkpoint.stem}.backup-{stamp}-{uuid4().hex[:8]}"
    backup.mkdir(parents=True, exist_ok=False)
    shares = {}
    for path in storage_paths(checkpoint):
        for source in _with_suffixes(path):
            if source.exists():
                target = backup / source.name
                shutil.copy2(source, target)
                shares[source.name] = (source, target)
    manifest = {
        "version": STORAGE_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "files": list(shares),
    }
    (backup / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if shares:
        state = inspect_storage(checkpoint)
        observed = {record["name"]: record["status"] for record in state.databases}
        kinds = {path.name: _kind(path, checkpoint) for path in storage_paths(checkpoint)}
        with tempfile.TemporaryDirectory(prefix="slothy-backup-probe-") as temporary:
            for name, (source, target) in shares.items():
                if _same_bytes(source, target) is False:
                    raise StorageRecoveryRequired(
                        StorageState("corrupt", 0, "The storage backup is not identical.")
                    )
                if not name.endswith(".sqlite") or observed.get(name) == "corrupt":
                    continue
                probe = _probe(
                    _copy_for_probe(target, Path(temporary) / target.name),
                    kinds[name],
                )
                if probe["status"] == "corrupt":
                    raise StorageRecoveryRequired(
                        StorageState("corrupt", 0, "The storage backup could not be verified.")
                    )
    return backup


def reset_storage(checkpoint: Path) -> dict:
    """Back up, verify and then initialize fresh databases after an explicit user choice.
    / 用户明确选择后先备份、校验，再初始化全新库。

    Args:
        checkpoint: Base checkpoint database path. / 基础检查点数据库路径。

    Returns:
        Stored marker metadata including the backup directory. / 含备份目录的标记元数据。

    Raises:
        StorageRecoveryRequired: When the backup cannot be verified; nothing is deleted. /
            备份无法校验时抛出，且不删除任何文件。
    """
    checkpoint = checkpoint.absolute()
    root = checkpoint.parent.resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = checkpoint.with_suffix(".storage.json")
    with _exclusive(checkpoint.with_suffix(".storage.lock")):
        files = [file for path in storage_paths(checkpoint) for file in _with_suffixes(path)]
        _require_safe(files, root)
        state = inspect_storage(checkpoint)
        previous = _read_marker(marker)
        backup = backup_databases(checkpoint) if state.status != "empty" else None
        for file in files:
            file.unlink(missing_ok=True)
        return _write_marker(
            marker,
            reset=state.status != "empty",
            backup=str(backup) if backup else None,
            replaced_version=state.version or (previous or {}).get("version", 0),
        )


def migrate_storage(checkpoint: Path) -> dict:
    """Explicit legacy migration entry point: verified backup, then a fresh store.
    / 显式的旧版迁移入口：先做可验证备份，再初始化新库。

    Args:
        checkpoint: Base checkpoint database path. / 基础检查点数据库路径。

    Returns:
        Stored marker metadata including the backup directory. / 含备份目录的标记元数据。
    """
    return reset_storage(checkpoint)
