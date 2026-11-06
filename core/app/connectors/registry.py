"""Durable opt-in local tools, MCP connections and ZIP skills."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path, PurePosixPath
from uuid import uuid4

from jsonschema import Draft202012Validator
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.providers import protect_secret

MAX_IMPORT = 10 * 1024 * 1024
NAME = r"^[a-zA-Z][a-zA-Z0-9_-]{0,47}$"


class LocalTool(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(pattern=NAME)
    description: str = Field(min_length=1, max_length=2000)
    command: str = Field(min_length=1, max_length=2048)
    args: list[str] = Field(default_factory=list, max_length=64)
    input_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})


class MCPConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    command: str | None = Field(default=None, min_length=1, max_length=2048)
    args: list[str] = Field(default_factory=list, max_length=64)
    env: dict[str, str] = Field(default_factory=dict, max_length=32)
    url: str | None = Field(default=None, max_length=2048)
    headers: dict[str, str] = Field(default_factory=dict, max_length=16)
    type: str | None = None

    @model_validator(mode="after")
    def transport(self):
        from urllib.parse import urlsplit

        if bool(self.command) == bool(self.url):
            raise ValueError("Provide exactly one MCP command or URL.")
        if self.type not in (None, "stdio", "http", "streamable-http", "sse"):
            raise ValueError("Unsupported MCP transport.")
        if self.url:
            url = urlsplit(self.url)
            if (
                url.scheme not in ("https", "http")
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError("Use an HTTP(S) MCP URL without embedded credentials.")
            if self.type == "stdio" or self.args or self.env:
                raise ValueError("HTTP MCP connections cannot contain process settings.")
        elif self.type not in (None, "stdio") or self.headers:
            raise ValueError("Stdio MCP connections cannot contain HTTP settings.")
        return self


def schema_valid(schema: dict):
    if schema.get("type") != "object" or len(json.dumps(schema)) > 24000:
        raise ValueError("Tool input_schema must be a bounded JSON object schema.")

    def refs(value):
        if isinstance(value, dict):
            if "$ref" in value and not str(value["$ref"]).startswith("#"):
                raise ValueError("External JSON Schema references are not supported.")
            for child in value.values():
                refs(child)
        elif isinstance(value, list):
            for child in value:
                refs(child)

    refs(schema)
    Draft202012Validator.check_schema(schema)


def extract_bundle(data: bytes, destination: Path):
    """Validate the whole archive before writing. No links, devices or path traversal."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > 512 or sum(item.file_size for item in entries) > 20 * 1024 * 1024:
                raise ValueError("ZIP exceeds the 512-file / 20 MiB extracted limit.")
            names = set()
            for item in entries:
                path = PurePosixPath(item.filename.replace("\\", "/"))
                mode = item.external_attr >> 16
                if (
                    path.is_absolute()
                    or not path.parts
                    or any(
                        part in (".", "..")
                        or ":" in part
                        or part.rstrip(". ") != part
                        or any(ord(char) < 32 for char in part)
                        or part.split(".")[0].upper()
                        in {
                            "CON",
                            "PRN",
                            "AUX",
                            "NUL",
                            *(f"{prefix}{n}" for prefix in ("COM", "LPT") for n in range(1, 10)),
                        }
                        for part in path.parts
                    )
                    or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
                ):
                    raise ValueError("ZIP contains an unsafe path or special file.")
                key = str(path).casefold()
                if key in names:
                    raise ValueError("ZIP contains duplicate paths.")
                names.add(key)
                target = destination.joinpath(*path.parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ValueError("ZIP path escapes its destination.")
            for item in entries:
                target = destination.joinpath(
                    *PurePosixPath(item.filename.replace("\\", "/")).parts
                )
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(item))
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise ValueError("The ZIP archive could not be read.") from exc


@asynccontextmanager
async def mcp_session(config: dict, directory: str | None = None):
    import httpx
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    async with AsyncExitStack() as stack:
        if config.get("command"):
            transport = stdio_client(
                StdioServerParameters(
                    command=config["command"],
                    args=config.get("args", []),
                    env=config.get("env", {}),
                    cwd=directory,
                ),
                errlog=stack.enter_context(open(os.devnull, "w")),
            )
        elif config.get("type") == "sse":
            transport = sse_client(config["url"], headers=config.get("headers", {}), timeout=15)
        else:
            client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=config.get("headers", {}),
                    timeout=15,
                    follow_redirects=False,
                )
            )
            transport = streamable_http_client(
                config["url"],
                http_client=client,
            )
        async with transport as streams, ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            yield session


async def discover(config: dict) -> list[dict]:
    async with asyncio.timeout(30), mcp_session(config) as session:
        return await list_mcp_tools(session)


async def list_mcp_tools(session) -> list[dict]:
    tools, cursor, names = [], None, set()
    for _ in range(8):
        result = await session.list_tools(cursor=cursor)
        for tool in result.tools:
            if tool.name in names:
                raise ValueError("MCP returned duplicate tool names.")
            names.add(tool.name)
            schema_valid(tool.inputSchema)
            tools.append(
                {
                    "name": tool.name,
                    "description": (tool.description or tool.name)[:2000],
                    "input_schema": tool.inputSchema,
                }
            )
        if len(tools) > 64:
            raise ValueError("An MCP connection can expose at most 64 tools.")
        cursor = result.nextCursor
        if not cursor:
            return tools
    raise ValueError("MCP tool pagination exceeded its limit.")


class ConnectorRegistry:
    def __init__(self, directory: Path, builtin_tools: list[BaseTool]):
        self.directory = directory
        self.path = directory / "connectors.json"
        self.lock = asyncio.Lock()
        self.builtin = {tool.name: tool for tool in builtin_tools}
        self.records = {}
        self.disabled = set()
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if saved.get("version") != 1:
                raise ValueError("Unsupported connector store version.")
            self.records = saved["items"]
            self.disabled = set(saved.get("disabled_builtin", []))

    def write(self, records: dict, disabled: set | None = None):
        disabled = self.disabled if disabled is None else disabled
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "items": records, "disabled_builtin": sorted(disabled)}),
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        self.records, self.disabled = records, disabled

    def config(self, record):
        return json.loads(protect_secret(record["config"], decrypt=True))

    def public(self, key: str, record: dict):
        return {
            "id": key,
            "kind": record["kind"],
            "name": record["name"],
            "description": record["description"],
            "enabled": record["enabled"],
            "builtin": False,
            "tools": [self.tool_name(key, spec) for spec in record.get("tools", [])],
            "transport": record.get("transport"),
            "file_count": record.get("file_count", 0),
        }

    def list(self):
        return [
            {
                "id": "builtin-" + name,
                "kind": "local",
                "name": name,
                "description": tool.description,
                "enabled": name not in self.disabled,
                "builtin": True,
                "tools": [name],
                "transport": None,
                "file_count": 0,
            }
            for name, tool in self.builtin.items()
        ] + [self.public(key, record) for key, record in self.records.items()]

    async def import_file(self, kind: str, filename: str, data: bytes):
        if len(self.records) >= 64:
            raise ValueError("Remove a connector before importing more (maximum 64).")
        self.directory.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(prefix="import-", dir=self.directory))
        moved = None
        try:
            bundle = filename.lower().endswith(".zip")
            if bundle:
                extract_bundle(data, staged)
            if kind == "skill":
                if not bundle:
                    raise ValueError("Import skills as a ZIP containing one SKILL.md.")
                files = list(staged.rglob("SKILL.md"))
                if len(files) != 1:
                    raise ValueError("The ZIP must contain exactly one SKILL.md.")
                text = files[0].read_text(encoding="utf-8-sig")
                if not text.strip() or len(text) > 32000:
                    raise ValueError("SKILL.md must contain 1–32,000 characters.")
                name = re.search(r"(?m)^name:\s*['\"]?([^\n'\"]+)", text)
                name = (
                    name[1].strip()
                    if name
                    else (
                        Path(filename).stem if files[0].parent == staged else files[0].parent.name
                    )
                )
                config = {"entry": str(files[0].relative_to(staged)), "text": text}
                records = [(name[:120], "", config, [])]
            else:
                if bundle:
                    manifests = list(staged.rglob("tool.json"))
                    if kind != "local" or len(manifests) != 1:
                        raise ValueError("A local tool ZIP must contain one tool.json.")
                    document = json.loads(manifests[0].read_text(encoding="utf-8-sig"))
                    document["bundle_root"] = str(manifests[0].parent.relative_to(staged))
                else:
                    document = json.loads(data.decode("utf-8-sig"))
                if kind == "local":
                    root = document.pop("bundle_root", ".") if bundle else "."
                    config = LocalTool.model_validate(document).model_dump()
                    schema_valid(config["input_schema"])
                    records = [
                        (
                            config["name"],
                            config["description"],
                            {**config, "bundle_root": root},
                            [{key: config[key] for key in ("name", "description", "input_schema")}],
                        )
                    ]
                elif kind == "mcp":
                    entries = document.get("mcpServers", {})
                    if not isinstance(entries, dict) or not 1 <= len(entries) <= 16:
                        raise ValueError("Import JSON with 1–16 named mcpServers entries.")
                    records = [
                        (name, "", MCPConnection.model_validate(value).model_dump(), [])
                        for name, value in entries.items()
                        if isinstance(name, str) and re.fullmatch(NAME, name)
                    ]
                    if len(records) != len(entries):
                        raise ValueError("MCP names must be 1–48 letters, digits, _ or -.")
                else:
                    raise ValueError("Unsupported connector type.")
            if len(self.records) + len(records) > 64:
                raise ValueError("At most 64 imported connectors are supported.")
            fingerprint = hashlib.sha256(data).hexdigest()
            if any(item.get("fingerprint") == fingerprint for item in self.records.values()):
                raise FileExistsError("This file has already been imported.")
            updated, imported = dict(self.records), []
            bundle_id = uuid4().hex
            if bundle:
                moved = self.directory / bundle_id
                staged.rename(moved)
            for name, description, config, tools in records:
                key = uuid4().hex
                record = {
                    "kind": kind,
                    "name": name,
                    "description": description,
                    "enabled": False,
                    "config": protect_secret(json.dumps(config)),
                    "tools": tools,
                    "bundle": bundle_id if bundle else None,
                    "fingerprint": fingerprint,
                    "file_count": sum(1 for file in moved.rglob("*") if file.is_file())
                    if moved
                    else 0,
                    "transport": (
                        "stdio"
                        if config.get("command")
                        else config.get("type") or "streamable-http"
                    )
                    if kind == "mcp"
                    else None,
                }
                updated[key] = record
                imported.append(self.public(key, record))
            self.write(updated)
            moved = None  # Committed bundle is now registry-owned.
            return imported
        finally:
            for path in (staged, moved):
                if (
                    path
                    and path.exists()
                    and path.resolve().is_relative_to(self.directory.resolve())
                ):
                    shutil.rmtree(path)

    async def toggle(self, key: str, enabled: bool):
        if key.startswith("builtin-") and key[8:] in self.builtin:
            name = key[8:]
            self.write(self.records, self.disabled - {name} if enabled else self.disabled | {name})
            return next(item for item in self.list() if item["id"] == key)
        previous = self.records[key]
        record = {**previous, "enabled": enabled}
        if enabled and previous["kind"] == "mcp":
            record["tools"] = await discover(self.config(previous))
        if enabled and previous["kind"] == "skill":
            references = [item for item in self.skill_references() if item["id"] != key]
            if (
                len(references) >= 8
                or sum(len(item["instructions"]) for item in references)
                + len(self.config(record)["text"])
                > 64000
            ):
                raise ValueError("Enabled skills exceed the 8-skill / 64,000-character budget.")
        if (
            enabled
            and sum(
                len(item["tools"])
                for ident, item in self.records.items()
                if ident != key and item["enabled"]
            )
            + len(record["tools"])
            > 64
        ):
            raise ValueError("At most 64 imported tools can be enabled at once.")
        self.write({**self.records, key: record})
        return self.public(key, record)

    def delete(self, key):
        record = self.records[key]
        self.write({k: v for k, v in self.records.items() if k != key})
        if record.get("bundle"):
            path = self.directory / record["bundle"]
            if path.resolve().is_relative_to(self.directory.resolve()) and path.is_dir():
                shutil.rmtree(path)

    def skill_references(self):
        return [
            {"id": key, "name": item["name"], "instructions": self.config(item)["text"]}
            for key, item in self.records.items()
            if item["enabled"] and item["kind"] == "skill"
        ]

    @staticmethod
    def tool_name(key, spec):
        signature = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]
        readable = re.sub(r"[^a-zA-Z0-9_-]", "_", spec["name"])[:24] or "tool"
        return f"ext_{readable}_{key[:8]}_{signature}"

    def guard(self, key, fingerprint):
        item = self.records.get(key)
        if not item or not item["enabled"] or item["fingerprint"] != fingerprint:
            raise ToolException("This connector was disabled or changed. Start a new request.")
        return item

    def tools(self, directory: str | None):
        tools = []
        for key, record in self.records.items():
            if not record["enabled"] or record["kind"] == "skill":
                continue
            for spec in record["tools"]:

                def build_invoke(_key, _record, _spec):
                    async def invoke(**arguments):
                        return await self.invoke(_key, _record, _spec, arguments, directory)

                    return invoke

                tools.append(
                    StructuredTool(
                        name=self.tool_name(key, spec),
                        description=f"{record['name']} / {spec['name']}: {spec['description']}",
                        args_schema=spec["input_schema"],
                        coroutine=build_invoke(key, record, spec),
                        handle_tool_error=True,
                    )
                )
        if self.skill_references():

            async def read_skill_file(skill_id: str, path: str, offset: int = 0):
                return self.read_skill_file(skill_id, path, offset)

            tools.append(
                StructuredTool.from_function(
                    coroutine=read_skill_file,
                    name="read_skill_file",
                    handle_tool_error=True,
                    description="Read a reference file from an enabled skill package by skill_id "
                    "and relative path. Returns 8,000 characters; paginate using next_offset.",
                )
            )
        return tools

    async def invoke(self, _key, _record, _spec, arguments, directory):
        self.guard(_key, _record["fingerprint"])
        try:
            Draft202012Validator(_spec["input_schema"]).validate(arguments)
            if _record["kind"] == "mcp":
                async with (
                    asyncio.timeout(60),
                    mcp_session(self.config(_record), directory) as session,
                ):
                    # Refuse stale schemas; approval applied to the displayed call.
                    listed = await list_mcp_tools(session)
                    current = next((tool for tool in listed if tool["name"] == _spec["name"]), None)
                    if current != _spec:
                        raise ToolException("MCP tools changed. Re-enable the connection.")
                    current_record = self.guard(_key, _record["fingerprint"])
                    if current_record["tools"] != _record["tools"]:
                        raise ToolException("MCP tools changed. Start a new request.")
                    result = await session.call_tool(_spec["name"], arguments)
                    if result.isError:
                        raise ToolException(
                            "MCP reported a tool error: "
                            + str(result.model_dump(mode="json"))[:4000]
                        )
                    return json.dumps(result.model_dump(mode="json"), ensure_ascii=False)[:16000]
            return await self.run_local(_key, _record, arguments, directory)
        except ToolException:
            raise
        except Exception as exc:
            raise ToolException(
                "Connector execution failed ("
                + type(exc).__name__
                + "). Check the connector configuration."
            ) from exc

    def read_skill_file(self, skill_id: str, path: str, offset: int):
        record = self.records.get(skill_id)
        if not record or record["kind"] != "skill" or not record["enabled"]:
            raise ToolException("This skill is not enabled.")
        root = (self.directory / record["bundle"] / self.config(record)["entry"]).parent.resolve()
        relative = PurePosixPath(path.replace("\\", "/"))
        target = root.joinpath(*relative.parts).resolve()
        if relative.is_absolute() or not target.is_relative_to(root) or offset < 0:
            raise ToolException("Use a relative path inside the enabled skill.")
        if not target.is_file() or target.stat().st_size > 1_000_000:
            raise ToolException("Reference file missing or larger than 1 MiB.")
        try:
            text = target.read_text(encoding="utf-8-sig")
        except (UnicodeError, OSError) as exc:
            raise ToolException("This reference is not a readable UTF-8 text file.") from exc
        return {
            "path": path,
            "text": text[offset : offset + 8000],
            "next_offset": offset + 8000 if len(text) > offset + 8000 else None,
        }

    async def run_local(self, key, record, arguments, directory):
        config = self.config(record)
        root = (
            self.directory / record["bundle"] / config["bundle_root"] if record["bundle"] else None
        )
        values = [config["command"], *config["args"]]
        if any("${plugin}" in value for value in values) and root is None:
            raise ToolException("${plugin} requires an imported tool ZIP.")
        values = [value.replace("${plugin}", str(root)) for value in values]
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "PATH",
                "SYSTEMROOT",
                "TEMP",
                "TMP",
                "USERPROFILE",
                "APPDATA",
                "LOCALAPPDATA",
                "PROGRAMFILES",
                "PATHEXT",
            }
        }
        env["SLOTHY_WORKSPACE"] = directory or str(self.directory)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        self.guard(key, record["fingerprint"])
        process = await asyncio.create_subprocess_exec(
            *values,
            cwd=directory or self.directory,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        try:

            async def bounded(stream):
                output = bytearray()
                while chunk := await stream.read(8192):
                    output.extend(chunk)
                    if len(output) > 128000:
                        raise ToolException("Local tool output exceeded 128 KiB.")
                return output

            async with asyncio.timeout(30):
                process.stdin.write(json.dumps(arguments).encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()
                output, _stderr = await asyncio.gather(
                    bounded(process.stdout), bounded(process.stderr)
                )
                await process.wait()
            if process.returncode:
                raise ToolException(f"Local tool exited with status {process.returncode}.")
            return json.loads(output)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
