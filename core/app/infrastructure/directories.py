"""Canonical per-workspace filesystem roots; no process-global chdir."""

from hashlib import sha256
from pathlib import Path


class WorkspaceDirectories:
    def __init__(self, managed: Path, personal: Path):
        self.managed = managed.resolve()
        self.personal = personal.resolve()

    def resolve(self, record: dict) -> Path:
        if record.get("directory"):
            return Path(record["directory"]).resolve()
        if record["id"] == "personal":
            return self.personal
        return self.managed / sha256(record["id"].encode()).hexdigest()[:32]

    def validate(self, directory: str) -> Path:
        path = Path(directory).expanduser()
        if not path.is_absolute() or "\0" in directory:
            raise ValueError("请选择绝对路径 / Choose an absolute directory path.")
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise ValueError("工作目录不存在或不是目录 / The working directory is unavailable.")
        return path

    @staticmethod
    def overlaps(first: Path, second: Path) -> bool:
        return first.is_relative_to(second) or second.is_relative_to(first)

    def prepare(self, record: dict) -> Path:
        path = self.resolve(record)
        if record.get("directory"):
            path = self.validate(record["directory"])
            if path != Path(record["directory"]):
                raise ValueError(
                    "工作目录指向已更改，请重新选择工作区 / The directory target has changed."
                )
            return path
        path.mkdir(parents=True, exist_ok=True)
        return path
