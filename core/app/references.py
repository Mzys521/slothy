"""Bounded references using existing readers. / 复用既有读取器的有界引用。"""

import asyncio
import json
import mimetypes
from itertools import islice
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.tools.contracts import ToolOperationError
from app.tools.workspace import WorkspaceFiles


class ReferenceInput(BaseModel):
    """Clients select identities, never contents or permissions. / 客户端只选择身份。"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["folder", "file", "session", "plugin"]
    id: str | None = Field(default=None, min_length=1, max_length=4096)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    source: Literal["workspace", "attachment"] | None = None

    @model_validator(mode="after")
    def identity(self):
        if self.kind == "folder" and not (self.path or self.id):
            raise ValueError("Choose a folder")
        if self.kind != "folder" and not self.id:
            raise ValueError("Choose an existing reference")
        if self.kind == "file" and self.source is None:
            raise ValueError("Choose a file source")
        if self.kind != "file" and self.source is not None:
            raise ValueError("Only files have a source")
        if self.kind != "folder" and self.path is not None:
            raise ValueError("Only folders accept a path")
        if self.kind == "folder" or self.source == "workspace":
            value = self.path or self.id
            if (
                Path(value).is_absolute()
                or PureWindowsPath(value).is_absolute()
                or ".." in value.replace("\\", "/").split("/")
                or "\0" in value
            ):
                raise ValueError("Choose a relative workspace path")
            normalized = PurePosixPath(value.replace("\\", "/")).as_posix()
            if self.kind == "folder":
                self.path = normalized
            else:
                self.id = normalized
        return self

    @property
    def key(self):
        return f"{self.kind}:{self.source or ''}:{self.path or self.id}"


class ReferenceResolver:
    """Revalidate before every model call. / 每次模型调用前重新校验引用。"""

    def __init__(self, conversations, connectors, settings_for, budget=12000, summary_for=None):
        self.store = conversations
        self.connectors = connectors
        self.settings_for = settings_for
        self.budget = budget
        self.summary_for = summary_for

    async def history_metadata(self, user, thread, items):
        """Refresh chip status without loading reference contents. / 仅刷新引用状态，不读取内容。"""
        result = []
        plugins = {item["id"]: item for item in self.connectors.list() if not item["builtin"]}
        for saved in items:
            item = dict(saved)
            try:
                if item["kind"] == "plugin":
                    plugin = plugins.get(item["id"])
                    if plugin is None:
                        raise LookupError("Missing plugin")
                    item["status"] = "ok" if plugin["enabled"] else "disabled"
                elif item["kind"] == "session":
                    if await self.store.get_thread(user, item["id"]) is None:
                        raise LookupError("Missing session")
                elif item.get("source") == "attachment":
                    if not await self.store.has_attachment(user, thread, item["id"]):
                        raise LookupError("Missing attachment")
                else:
                    files = WorkspaceFiles(await self.settings_for(user, thread))
                    target = files.resolve(item.get("path") or item["id"])
                    if not target.exists() or target.is_dir() != (item["kind"] == "folder"):
                        raise LookupError("Missing path")
            except (ValueError, LookupError, ToolOperationError, OSError):
                item["status"] = "missing"
            result.append(item)
        return result

    async def search(self, user, thread, kind, query="", path=".", limit=8, workspace="personal"):
        if kind == "session":
            return await self.store.search_threads(user, query, limit)
        if kind == "plugin":
            return [
                {
                    key: item[key]
                    for key in ("id", "name", "kind", "enabled", "description", "tools")
                }
                for item in self.connectors.list()
                if not item["builtin"]
                and query.casefold() in (item["name"] + " " + item["description"]).casefold()
            ][:limit]
        settings = await self.settings_for(user, thread, workspace)
        files = WorkspaceFiles(settings)
        target = files.resolve(path)
        if not target.is_dir():
            raise LookupError("Choose an existing workspace folder")
        items = []
        if kind == "file" and thread:
            for item in await self.store.list_attachments(user, 200, 0, thread):
                if query.casefold() in item["filename"].casefold():
                    items.append(
                        dict(
                            id=item["id"],
                            name=item["filename"],
                            bytes=item["byte_size"],
                            source="attachment",
                        )
                    )
        queue = [(target, 0)]
        scanned = 0
        while queue and len(items) < limit and scanned < 2000:
            directory, depth = queue.pop(0)
            for child in islice(directory.iterdir(), 200):
                scanned += 1
                if scanned % 32 == 0:
                    await asyncio.sleep(0)
                try:
                    relative = child.relative_to(files.root).as_posix()
                    safe = files.resolve(relative)
                    directory_item = safe.is_dir()
                    if query and directory_item and depth < 6:
                        queue.append((safe, depth + 1))
                    if (kind == "folder") != directory_item or not safe.exists():
                        continue
                    if query.casefold() not in relative.casefold():
                        continue
                    stat = safe.stat()
                    items.append(
                        dict(
                            id=relative,
                            path=relative,
                            name=safe.name,
                            bytes=stat.st_size if not directory_item else 0,
                            modified=stat.st_mtime,
                            source="workspace",
                        )
                    )
                    if len(items) >= limit:
                        break
                except (ToolOperationError, OSError):
                    continue
        return items[:limit]

    async def resolve(self, user, thread, inputs):
        selected = [ReferenceInput.model_validate(item) for item in inputs]
        if len(selected) > 8 or len({item.key for item in selected}) != len(selected):
            raise ValueError("Select at most eight distinct references")
        if not await self.store.get_thread(user, thread):
            raise LookupError("Reference conversation is unavailable")
        metadata, blocks = [], []
        remaining = self.budget
        for item in selected:
            source_truncated = False
            identity = item.path or item.id
            label = identity
            status = "ok"
            if item.kind in {"file", "folder"}:
                if item.source == "attachment":
                    (attachment,) = await self.store.read_attachments(user, thread, [item.id])
                    label = attachment["filename"]
                    data = {
                        "name": label,
                        "bytes": attachment["byte_size"],
                        "text": attachment["content"][:8000],
                    }
                else:
                    files = WorkspaceFiles(await self.settings_for(user, thread))
                    target = files.resolve(identity)
                    if not target.exists() or target.is_dir() != (item.kind == "folder"):
                        raise LookupError("Reference path is unavailable")
                    label = target.name or "."
                    if item.kind == "folder":
                        result = await files.read("list", path=identity, depth=1)
                        data = result["data"]
                    else:
                        data = {"path": identity, "bytes": target.stat().st_size}
                        try:
                            result = await files.read("read", path=identity, limit=8000)
                            mime = mimetypes.guess_type(identity)[0] or "text/plain"
                            textual = mime.startswith("text/") or mime in {
                                "application/json",
                                "application/xml",
                            }
                            if textual and "\0" not in result["data"]["text"]:
                                data.update(result["data"])
                                data["next_offset"] = result.get("next_offset")
                                source_truncated = result.get("next_offset") is not None
                            else:
                                data["note"] = "Binary file: metadata only."
                        except ToolOperationError as error:
                            if error.result.error.code not in {"INVALID_ARGS", "OUTPUT_TOO_LARGE"}:
                                raise
                            data["note"] = "Binary or large file: metadata only."
            elif item.kind == "session":
                record = await self.store.get_thread(user, item.id)
                if record is None:
                    raise LookupError("Referenced session is unavailable")
                label = record["title"] or item.id
                summary = await self.summary_for(user, item.id) if self.summary_for else None
                turns = await self.store.list_turns(
                    user_id=user, thread_id=item.id, limit=2 if summary else 6, newest_first=True
                )
                data = {
                    "title": label,
                    "updated_at": record["updated_at"],
                }
                if summary:
                    data["summary"] = summary[:1600]
                    source_truncated = len(summary) > 1600
                excerpt = max(80, (3000 - len(data.get("summary", ""))) // max(1, len(turns) * 2))
                data["turns_newest_first"] = [
                    {"user": turn.user_input[:excerpt], "assistant": turn.response[:excerpt]}
                    for _, turn in turns
                ]
                source_truncated |= any(
                    len(turn.user_input) > excerpt or len(turn.response) > excerpt
                    for _, turn in turns
                )
            else:
                record = next(
                    (
                        entry
                        for entry in self.connectors.list()
                        if entry["id"] == item.id and not entry["builtin"]
                    ),
                    None,
                )
                if record is None:
                    raise LookupError("Referenced plugin is unavailable")
                label = record["name"]
                status = "ok" if record["enabled"] else "disabled"
                data = {key: record[key] for key in ("name", "kind", "description", "enabled")}
                data["tools"] = record["tools"][:20] if record["enabled"] else []
                data["note"] = "Reference only; tool permissions and approval are unchanged."
            meta = {
                **item.model_dump(exclude_none=True),
                "id": identity,
                "label": label[:200],
                "status": status,
                "reference_id": item.key,
            }
            metadata.append(meta)
            cap = {"file": 8000, "folder": 4000, "session": 4000, "plugin": 2000}[item.kind]
            block = json.dumps({**meta, "data": data}, ensure_ascii=False, separators=(",", ":"))
            size = min(cap, max(0, remaining - 1))
            truncated = len(block) > size
            if truncated:
                block = block[: max(0, size - 14)] + " [truncated]" if size >= 14 else ""
            if block:
                blocks.append(block)
                remaining -= len(block) + 1
            meta["truncated"] = truncated or source_truncated
            meta["included"] = bool(block)
        return metadata, "\n".join(blocks)
