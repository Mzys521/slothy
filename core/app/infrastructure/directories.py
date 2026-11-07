"""Canonical per-workspace filesystem roots; no process-global chdir.
/ 规范化的工作区文件系统根目录；不切换进程级工作目录。"""

from hashlib import sha256
from pathlib import Path


class WorkspaceDirectories:
    """Resolve and validate the directory backing each logical workspace.

    Args:
        managed: Root that holds hash-sharded managed workspace directories.
            / 存放按哈希分片的托管工作区目录的根。
        personal: Directory backing the implicit personal workspace.
            / 隐式 personal 工作区对应的目录。
    """

    def __init__(self, managed: Path, personal: Path):
        """Resolve both configured roots to absolute paths.

        Args:
            managed: Managed root, resolved on construction. / 托管根目录，构造时解析为绝对路径。
            personal: Personal root, resolved on construction. / 个人根目录，构造时解析为绝对路径。
        """
        self.managed = managed.resolve()
        self.personal = personal.resolve()

    def resolve(self, record: dict) -> Path:
        """Return the directory for one workspace record without creating it.

        Args:
            record: Workspace row with an id and an optional explicit directory.
                / 含 id 与可选显式目录的工作区记录。

        Returns:
            The explicit directory, the personal root, or a sha256-sharded managed path.
            / 显式目录、个人根目录或 sha256 分片的托管路径。
        """
        if record.get("directory"):
            return Path(record["directory"]).resolve()
        if record["id"] == "personal":
            return self.personal
        return self.managed / sha256(record["id"].encode()).hexdigest()[:32]

    def validate(self, directory: str) -> Path:
        """Validate a user-supplied absolute directory.

        Args:
            directory: Candidate absolute path chosen by the user. / 用户选择的绝对路径。

        Returns:
            The resolved, existing directory. / 解析后确实存在的目录。
        Raises:
            ValueError: For relative paths, null bytes, missing directories or non-directories.
                / 相对路径、空字符、不存在或不是目录时抛出。
        """
        path = Path(directory).expanduser()
        if not path.is_absolute() or "\0" in directory:
            raise ValueError("请选择绝对路径 / Choose an absolute directory path.")
        path = path.resolve(strict=True)
        if not path.is_dir():
            raise ValueError("工作目录不存在或不是目录 / The working directory is unavailable.")
        return path

    @staticmethod
    def overlaps(first: Path, second: Path) -> bool:
        """Report whether either directory contains the other.

        Args:
            first: One resolved path. / 已解析路径之一。
            second: The other resolved path. / 另一个已解析路径。

        Returns:
            True when the paths nest in either direction. / 任一方向存在包含关系时为 True。
        """
        return first.is_relative_to(second) or second.is_relative_to(first)

    def prepare(self, record: dict) -> Path:
        """Resolve a workspace directory, creating it when it is managed.

        Args:
            record: Workspace row to materialize. / 要落地的工作区记录。

        Returns:
            An existing directory ready for use. / 可直接使用的已存在目录。
        Raises:
            ValueError: When an explicit directory moved since it was saved.
                / 显式目录在保存后发生移动时抛出。
        """
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
