"""Bounded UTF-8 workspace file operations with no shell or external search binary. /
有界的 UTF-8 工作区文件操作，不使用 Shell 或外部搜索程序。"""

import asyncio
import os
import shutil
import tempfile
from itertools import islice
from pathlib import Path

from app.config import ToolSettings
from app.tools.context import execution_context
from app.tools.contracts import ToolOperationError, ToolResult, artifact_for


class WorkspaceFiles:
    """
    Workspace-confined directory, read and write operations. /
    限定在工作区内的目录、读取与写入操作。

    Every path is resolved and re-checked for symlinks, private configuration names
    and application journals before use. /
    使用前解析并复查路径的符号链接、私密配置名与应用日志目录。
    """

    def __init__(self, settings: ToolSettings):
        """
        Bind file operations to the tool workspace roots. / 将文件操作绑定到工具工作区根目录。

        Args:
            settings: Workspace root, artifact root and read/search caps. /
            工作区根目录、产物根目录与读取/搜索上限。
        """
        self.settings = settings
        self.root = settings.workspace_path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts = (settings.artifacts_path or self.root).resolve()

    def resolve(self, path: str) -> Path:
        """
        Resolve one relative path inside the workspace and reject escapes. /
        解析工作区内相对路径并拒绝越界。

        Args:
            path: Relative path to resolve; absolute and linked paths are rejected. /
                待解析的相对路径；绝对路径与链接路径会被拒绝。

        Returns:
            Resolved absolute path that stays inside the workspace. /
            位于工作区内的已解析绝对路径。

        Raises:
            ToolOperationError: If the path escapes, links or names private configuration. /
                路径越界、链接到别处或指向私密配置时抛出。
        """
        candidate = self.root / path
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise ToolOperationError(
                "OUT_OF_WORKSPACE",
                "Path is outside the workspace.",
                hint="Use a relative workspace path.",
            )
        parts = resolved.relative_to(self.root).parts
        lexical = Path(os.path.abspath(candidate))
        if not lexical.is_relative_to(self.root):
            raise ToolOperationError("OUT_OF_WORKSPACE", "Path is outside the workspace.")
        for part in (*lexical.relative_to(self.root).parts, *parts):
            name = part.casefold()
            if name in {".git", ".aws", ".codex", ".agents", ".slothy"} or (
                name.startswith(".env") and name != ".env.example"
            ):
                raise ToolOperationError("DENIED", "Private configuration paths are unavailable.")
        # English: Journals are application state, including the default artifact-root layout.
        # 中文：日志属于应用状态，包括默认产物根目录下的布局。
        if resolved.is_relative_to(self.artifacts / "runs"):
            raise ToolOperationError("DENIED", "Tool journals are managed by the application.")
        current = self.root
        for part in lexical.relative_to(self.root).parts:
            current /= part
            if current.is_symlink() or current.resolve() != current.absolute():
                raise ToolOperationError("OUT_OF_WORKSPACE", "Linked paths are unavailable.")
        return resolved

    async def read(self, action: str, path=".", depth=1, offset=0, limit=8000, pattern=None):
        """
        List, page-read or literal-search workspace text files. /
        列出、分页读取或按字面搜索工作区文本文件。

        Args:
            action: list, read or search. / list、read 或 search。
            path: Relative file or directory, default the workspace root. /
            相对文件或目录，默认工作区根目录。
            depth: Directory listing depth from 1 to 4. / 目录列出的深度 1～4。
            offset: Character or match offset for paged reads and searches. /
            分页读取与搜索的字符或匹配偏移。
            limit: Maximum characters for read or matches for search. / read 的字符上限或
            search 的匹配上限。
            pattern: Literal fixed string required by search; never a regex. /
                search 所需字面固定字符串；不是正则。

        Returns:
            Serialized ToolResult with text, entries or matches and truncation flags. /
                含文本、条目或匹配及截断标志的序列化 ToolResult。

        Raises:
            ToolOperationError: If the path, action, limits, encoding or size is invalid. /
                路径、操作、限制、编码或大小无效时抛出。
        """
        target = self.resolve(path)
        if action == "read":
            if not target.is_file():
                raise ToolOperationError("INVALID_ARGS", "Choose an existing text file.")
            if target.stat().st_size > self.settings.max_workspace_read_bytes:
                raise ToolOperationError("OUTPUT_TOO_LARGE", "File exceeds the read byte limit.")
            try:
                with target.open(encoding="utf-8-sig") as stream:
                    stream.read(offset)
                    text = stream.read(limit + 1)
            except UnicodeError as error:
                raise ToolOperationError("INVALID_ARGS", "Only UTF-8 text can be read.") from error
            return ToolResult(
                ok=True,
                summary="Read a page of workspace text.",
                data={"path": target.relative_to(self.root).as_posix(), "text": text[:limit]},
                next_offset=offset + limit if len(text) > limit else None,
            ).model_dump()
        if not target.exists():
            raise ToolOperationError("INVALID_ARGS", "Path does not exist.")
        if action == "list":
            if not target.is_dir():
                raise ToolOperationError("INVALID_ARGS", "Choose a directory to list.")
            entries, pending = [], [(target, 1)]
            while pending:
                directory, level = pending.pop(0)
                children = list(islice(directory.iterdir(), 201))
                for entry in sorted(children, key=lambda item: item.name.casefold()):
                    try:
                        entry = self.resolve(str(entry))
                    except ToolOperationError:
                        continue
                    if len(entries) >= 200:
                        return ToolResult(
                            ok=True,
                            summary="Listed 200 entries; results truncated.",
                            data={"entries": entries, "truncated": True},
                        ).model_dump()
                    entries.append(
                        {
                            "name": entry.name,
                            "path": entry.relative_to(self.root).as_posix(),
                            "type": "directory" if entry.is_dir() else "file",
                            "bytes": entry.stat().st_size if entry.is_file() else 0,
                        }
                    )
                    if entry.is_dir() and level < depth:
                        pending.append((entry, level + 1))
                await asyncio.sleep(0)
            return ToolResult(
                ok=True,
                summary=f"Listed {len(entries)} entries.",
                data={"entries": entries, "truncated": False},
            ).model_dump()
        if not pattern:
            raise ToolOperationError("INVALID_ARGS", "Provide a nonempty fixed-string pattern.")
        limit = min(limit, 50)
        matches, pending, scanned, byte_count = [], [target], 0, 0
        truncated = False
        while pending:
            item = pending.pop()
            try:
                item = self.resolve(str(item))
            except ToolOperationError:
                continue
            scanned += 1
            if scanned > 2000:
                truncated = True
                break
            if item.is_dir():
                # English: Bound directory enumeration as well as file contents.
                # 中文：除文件内容外也限制目录枚举数量。
                with os.scandir(item) as iterator:
                    for index, entry in enumerate(iterator):
                        if index >= 2000 or len(pending) >= 2000:
                            truncated = True
                            break
                        pending.append(Path(entry.path))
            elif item.is_file():
                size = item.stat().st_size
                if size > self.settings.max_workspace_read_bytes:
                    truncated = True
                    continue
                byte_count += size
                if byte_count > self.settings.max_workspace_search_bytes:
                    truncated = True
                    break
                try:
                    with item.open(encoding="utf-8-sig") as stream:
                        for line, text in enumerate(stream, 1):
                            if pattern in text:
                                matches.append(
                                    {
                                        "path": item.relative_to(self.root).as_posix(),
                                        "line": line,
                                        "text": text.rstrip()[:1000],
                                    }
                                )
                                if len(matches) >= limit:
                                    truncated = True
                                    break
                except UnicodeError:
                    continue
                if len(matches) >= limit:
                    break
            await asyncio.sleep(0)
        return ToolResult(
            ok=True,
            summary=f"Found {len(matches)} fixed-string matches.",
            data={"matches": matches, "truncated": truncated},
        ).model_dump()

    async def delete(self, path: str, recursive: bool = False):
        """
        Delete one workspace file or directory after approval. / 审批后删除工作区文件或目录。

        The path is resolved again at execution time, so an approval cannot be reused for a
        different target. Symlinks, workspace roots, private paths and non-empty directories
        without ``recursive`` are rejected, and a recursive delete scans for links first.

        Args:
            path: Relative target path inside the workspace. / 工作区内相对目标路径。
            recursive: Allow deleting a non-empty directory. / 是否允许递归删除非空目录。

        Returns:
            Serialized ToolResult with the removed path and kind. / 含删除路径与类型的 ToolResult。

        Raises:
            ToolOperationError: If the target is missing, linked, the root, private or not
                empty without recursive approval. / 目标缺失、为链接、根目录、私有路径，或未批准
                递归而目录非空时抛出。
        """
        target = self.resolve(path)
        if target == self.root:
            raise ToolOperationError("DENIED", "The workspace root cannot be deleted.")
        if not target.exists():
            raise ToolOperationError("NOT_FOUND", "The target does not exist.")
        if target.is_symlink():
            raise ToolOperationError("DENIED", "Linked paths cannot be deleted.")
        kind = "directory" if target.is_dir() else "file"
        if kind == "directory":
            children = list(target.iterdir())
            if children and not recursive:
                raise ToolOperationError(
                    "DENIED",
                    "Directory is not empty.",
                    hint="Pass recursive=true only after reviewing the full deletion scope.",
                )
            for child in target.rglob("*"):
                if child.is_symlink():
                    raise ToolOperationError(
                        "DENIED",
                        "Directory contains a linked path; move it out before deleting.",
                    )
            shutil.rmtree(target)
        else:
            target.unlink()
        return ToolResult(
            ok=True,
            summary=f"Deleted one workspace {kind}.",
            data={
                "path": target.relative_to(self.root).as_posix(),
                "kind": kind,
                "recursive": recursive,
            },
        ).model_dump()

    async def write(self, action: str, path: str, content="", overwrite=False):
        """
        Create a directory or write a UTF-8 file after approval. / 审批后创建目录或写入 UTF-8
        文件。

        Args:
            action: mkdir or write_file. / mkdir 或 write_file。
            path: Relative destination path inside the workspace. / 工作区内相对目标路径。
            content: UTF-8 text for write_file; ignored for mkdir. / write_file 的 UTF-8
            文本；mkdir 忽略。
            overwrite: Whether to atomically replace an existing file. /
            是否原子替换已有文件。

        Returns:
            Serialized ToolResult with the written path and byte count. /
            含写入路径与字节数的序列化 ToolResult。

        Raises:
            ToolOperationError: If the path, parent, size or overwrite policy is invalid. /
                路径、父目录、大小或覆盖策略无效时抛出。
        """
        target = self.resolve(path)
        if target == self.root:
            if action != "mkdir":
                raise ToolOperationError("INVALID_ARGS", "Choose a file path.")
        if action == "mkdir":
            target.mkdir(parents=True, exist_ok=True)
            return ToolResult(
                ok=True,
                summary="Workspace directory is ready.",
                data={"path": target.relative_to(self.root).as_posix(), "kind": "directory"},
            ).model_dump()
        data = content.encode("utf-8")
        if len(data) > self.settings.max_artifact_bytes:
            raise ToolOperationError("OUTPUT_TOO_LARGE", "File exceeds the artifact byte limit.")
        if not target.parent.is_dir():
            raise ToolOperationError(
                "INVALID_ARGS",
                "Parent directory does not exist.",
                hint="Create the parent with workspace_write mkdir first.",
            )
        if not overwrite:
            try:
                with target.open("xb") as stream:
                    stream.write(data)
            except FileExistsError as error:
                raise ToolOperationError(
                    "DENIED",
                    "File exists; overwrite was not approved.",
                    hint="Choose another path or explicitly set overwrite.",
                ) from error
        else:
            descriptor, temporary = tempfile.mkstemp(prefix=".slothy-write-", dir=target.parent)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.resolve(path)
                os.replace(temporary, target)
            finally:
                Path(temporary).unlink(missing_ok=True)
        context = execution_context.get()
        return ToolResult(
            ok=True,
            summary="Wrote a workspace file.",
            data={"path": target.relative_to(self.root).as_posix(), "bytes": len(data)},
            artifacts=[artifact_for(target, self.root, context.key if context else "direct")],
        ).model_dump()
