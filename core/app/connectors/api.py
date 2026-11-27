"""Connector HTTP contract; routes hold no execution or registry logic. /
连接器 HTTP 契约；路由不包含执行或目录逻辑。"""

from contextlib import contextmanager
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from app.connectors.registry import (
    MAX_IMPORT,
    NAME,
    ConnectorChangedError,
    MCPConnectionError,
    MCPFormConnection,
    discover,
)
from app.connectors.skills import SkillInput

router = APIRouter()


class ConnectorPublic(BaseModel):
    """
    Public projection of one imported or built-in connector. /
    单个导入或内置连接器的公开投影。
    """

    id: str
    kind: Literal["local", "mcp", "skill"]
    name: str
    description: str
    enabled: bool
    builtin: bool
    tools: list[str]
    transport: str | None
    file_count: int
    risk: str | None = None
    platforms: list[str] = Field(default_factory=list)
    available: bool = False
    capabilities: list[dict] = Field(default_factory=list)
    revision: str | None = None
    reference_count: int | None = None
    connection_status: (
        Literal["disabled", "unchecked", "connecting", "connected", "failed"] | None
    ) = None


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict


class SkillDetail(BaseModel):
    markdown: str
    instructions: str
    references: list[str]


class ConnectorDetail(BaseModel):
    item: ConnectorPublic
    tools: list[ToolDefinition]
    mcp: MCPFormConnection | None = None
    skill: SkillDetail | None = None


class ConnectorList(BaseModel):
    """All connectors visible to the desktop client. / 桌面客户端可见的全部连接器。"""

    items: list[ConnectorPublic]


class ConnectorUpdate(BaseModel):
    """Enable or disable one connector. / 启用或停用单个连接器。"""

    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    revision: str | None = Field(default=None, max_length=64)


class MCPTest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool = False
    revision: str | None = Field(default=None, max_length=64)


class MCPDraftTest(MCPTest):
    connector_id: str | None = Field(default=None, max_length=64)
    config: MCPFormConnection


class MCPInput(MCPTest):
    name: Annotated[str, Field(pattern=NAME)]
    description: str = Field(default="", max_length=2000)
    enabled: StrictBool = False
    config: MCPFormConnection


class MCPResult(BaseModel):
    ok: bool
    response_status: Literal["tools_listed", "failed"]
    tool_count: int
    tools: list[ToolDefinition]
    code: str | None = None
    message: str | None = None
    http_status: int | None = None
    connector: ConnectorPublic | None = None


class ConnectorError(BaseModel):
    """Error body returned by the connector routes. / 连接器路由返回的错误响应体。"""

    code: str
    message: str
    request_id: str


ERRORS = {status: {"model": ConnectorError} for status in (404, 409, 413, 422, 500, 502)}


def problem(status, code, message):
    """
    Build an HTTP exception with a stable code and message. / 构造带稳定错误码与消息的 HTTP
    异常。

    Args:
        status: HTTP status code to return. / 返回的 HTTP 状态码。
        code: Stable machine-readable error code. / 稳定的机器可读错误码。
        message: Human-readable failure message. / 人类可读的失败消息。

    Returns:
        HTTPException carrying the code and message detail. / 携带错误码与消息详情的
        HTTPException。
    """
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@contextmanager
def connector_errors():
    """Return bounded validation and connection errors without echoing the submitted data."""
    try:
        yield
    except KeyError:
        raise problem(404, "CONNECTOR_NOT_FOUND", "Connector not found.") from None
    except ConnectorChangedError as exc:
        raise problem(409, "CONNECTOR_CHANGED", str(exc)) from None
    except FileExistsError as exc:
        raise problem(409, "CONNECTOR_EXISTS", str(exc)) from None
    except MCPConnectionError as exc:
        status = f" HTTP {exc.http_status}." if exc.http_status is not None else ""
        raise problem(502, exc.code, str(exc) + status) from None
    except (ValidationError, SchemaError, UnicodeError, RecursionError):
        raise problem(422, "CONNECTOR_INVALID", "Check the connector format and values.") from None
    except ValueError as exc:
        raise problem(422, "CONNECTOR_INVALID", str(exc)[:500]) from None
    except OSError:
        raise problem(
            500, "CONNECTOR_STORAGE_FAILED", "Could not access connector storage."
        ) from None


def require_confirmation(body: MCPTest):
    if not body.confirmed:
        raise problem(409, "CONFIRMATION_REQUIRED", "Confirm connecting to the MCP server first.")


async def limited_body(request: Request, limit: int, message: str) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > limit:
            raise problem(413, "CONNECTOR_TOO_LARGE", message)
        data.extend(chunk)
    return bytes(data)


def probe_result(tools: list[dict], connector: dict | None = None) -> MCPResult:
    return MCPResult(
        ok=True,
        response_status="tools_listed",
        tools=tools,
        tool_count=len(tools),
        connector=connector,
    )


def failed_probe(exc: MCPConnectionError) -> MCPResult:
    return MCPResult(
        ok=False,
        response_status="failed",
        tools=[],
        tool_count=0,
        code=exc.code,
        message=str(exc),
        http_status=exc.http_status,
    )


@router.get("/connectors", response_model=ConnectorList)
async def list_connectors(request: Request):
    """List built-in and imported connectors. / 列出内置与导入连接器。

    Args:
        request: HTTP request exposing the connector registry. / 暴露连接器目录的 HTTP 请求。

    Returns:
        Connector list envelope for the client. / 供客户端使用的连接器列表信封。
    """
    return {"items": request.app.state.connectors.list()}


@router.post(
    "/connectors/import",
    status_code=201,
    response_model=ConnectorList,
    responses=ERRORS,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def import_connector(
    request: Request,
    kind: Literal["local", "mcp", "skill"],
    filename: str = Query(min_length=1, max_length=180),
):
    """
    Import a local tool, MCP connection or ZIP skill. / 导入本地工具、MCP 连接或 ZIP 技能。

    Args:
        request: HTTP request whose body carries the upload bytes. / 请求体携带上传字节的 HTTP
        请求。
        kind: Import kind: local, mcp or skill. / 导入类型：local、mcp 或 skill。
        filename: Client filename used for ZIP and format detection. / 用于 ZIP
        与格式检测的客户端文件名。

    Returns:
        Connector list envelope including newly imported entries. /
        含新导入项的连接器列表信封。

    Raises:
        HTTPException: If the filename, size, format, duplicate or limit is invalid. /
            文件名、大小、格式、重复或数量限制无效时抛出。
    """
    if any(char in "/\\" or ord(char) < 32 for char in filename):
        raise problem(422, "INVALID_FILENAME", "Provide a filename without a path.")
    data = await limited_body(request, MAX_IMPORT, "Imports must be no larger than 10 MiB.")
    registry = request.app.state.connectors
    async with registry.lock:
        try:
            items = await registry.import_file(kind, filename, data)
        except FileExistsError as exc:
            raise problem(409, "CONNECTOR_EXISTS", str(exc)) from exc
        except (ValueError, UnicodeError, SchemaError, RecursionError) as exc:
            # English: Pydantic errors can contain configuration secrets; do not echo input.
            # 中文：Pydantic 错误可能包含配置密钥，不要回显输入。
            message = (
                "Invalid connector format. Check the import format."
                if isinstance(exc, (ValidationError, SchemaError, RecursionError))
                else str(exc)[:500]
            )
            raise problem(422, "CONNECTOR_INVALID", message) from exc
    return {"items": items}


@router.get("/connectors/{connector_id}", response_model=ConnectorDetail, responses=ERRORS)
async def connector_detail(request: Request, response: Response, connector_id: str):
    """Read tool definitions or editable configuration with secret values withheld."""
    response.headers["Cache-Control"] = "no-store"
    with connector_errors():
        return request.app.state.connectors.details(connector_id)


@router.post("/connectors/mcp/test", response_model=MCPResult, responses=ERRORS)
async def test_mcp_draft(request: Request, body: MCPDraftTest):
    """Handshake and list tools from draft settings without saving or enabling them."""
    require_confirmation(body)
    registry = request.app.state.connectors
    with connector_errors():
        previous = (
            registry.config(registry.get(body.connector_id, "mcp", body.revision))
            if body.connector_id
            else {}
        )
        config = body.config.resolve(previous)
        try:
            return probe_result(await discover(config))
        except MCPConnectionError as exc:
            return failed_probe(exc)


@router.post("/connectors/{connector_id}/test", response_model=MCPResult, responses=ERRORS)
async def test_saved_mcp(request: Request, connector_id: str, body: MCPTest):
    """Probe a saved connection without changing its registered tools or enablement."""
    require_confirmation(body)
    registry = request.app.state.connectors
    async with registry.lock:
        with connector_errors():
            config = registry.config(registry.get(connector_id, "mcp", body.revision))
            try:
                return probe_result(await registry.probe(connector_id, config))
            except MCPConnectionError as exc:
                return failed_probe(exc)


@router.post("/connectors/{connector_id}/refresh", response_model=MCPResult, responses=ERRORS)
async def refresh_mcp(request: Request, connector_id: str, body: MCPTest):
    """Discover and register the latest tool definitions for a saved MCP configuration."""
    require_confirmation(body)
    registry = request.app.state.connectors
    async with registry.lock:
        with connector_errors():
            try:
                tools, connector = await registry.refresh_mcp(connector_id, body.revision)
            except MCPConnectionError as exc:
                return failed_probe(exc)
            request.app.state.reload_models()
            return probe_result(tools, connector)


async def save_mcp(request: Request, body: MCPInput, connector_id: str | None = None):
    if body.enabled:
        require_confirmation(body)
    registry = request.app.state.connectors
    async with registry.lock:
        with connector_errors():
            item = await registry.save_mcp(
                connector_id,
                name=body.name,
                description=body.description.strip(),
                enabled=body.enabled,
                config=body.config,
                revision=body.revision,
            )
            request.app.state.reload_models()
            return item


@router.post("/connectors/mcp", status_code=201, response_model=ConnectorPublic, responses=ERRORS)
async def create_mcp(request: Request, body: MCPInput):
    return await save_mcp(request, body)


@router.put("/connectors/{connector_id}/mcp", response_model=ConnectorPublic, responses=ERRORS)
async def edit_mcp(request: Request, connector_id: str, body: MCPInput):
    return await save_mcp(request, body, connector_id)


SKILL_BODY = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["name", "instructions"],
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "instructions": {"type": "string"},
                        "enabled": {"type": "boolean"},
                        "revision": {"type": "string"},
                        "files": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["name", "data"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "data": {"type": "string", "format": "byte"},
                                },
                            },
                        },
                        "remove_files": {"type": "array", "items": {"type": "string"}},
                    },
                },
            }
        },
    }
}


async def save_skill(request: Request, connector_id: str | None = None):
    data = await limited_body(request, 14 * 1024 * 1024, "The skill upload is too large.")
    registry = request.app.state.connectors
    with connector_errors():
        body = SkillInput.model_validate_json(data)
        async with registry.lock:
            item = registry.save_skill(connector_id, body)
            request.app.state.reload_models()
            return item


@router.post(
    "/connectors/skills",
    status_code=201,
    response_model=ConnectorPublic,
    responses=ERRORS,
    openapi_extra=SKILL_BODY,
)
async def create_skill(request: Request):
    return await save_skill(request)


@router.put(
    "/connectors/{connector_id}/skill",
    response_model=ConnectorPublic,
    responses=ERRORS,
    openapi_extra=SKILL_BODY,
)
async def edit_skill(request: Request, connector_id: str):
    return await save_skill(request, connector_id)


@router.patch("/connectors/{connector_id}", response_model=ConnectorPublic, responses=ERRORS)
async def update_connector(request: Request, connector_id: str, body: ConnectorUpdate):
    """
    Enable or disable one connector and reload tool availability. /
    启用或停用连接器并重新加载工具可用性。

    Args:
        request: HTTP request exposing the connector registry. / 暴露连接器目录的 HTTP 请求。
        connector_id: Connector id such as builtin-name or an imported key. /
            builtin-name 或导入键等连接器 id。
        body: Requested enablement flag. / 请求的启用标志。

    Returns:
        Updated public connector projection. / 更新后的连接器公开投影。

    Raises:
        HTTPException: If the connector is missing, over a limit or cannot connect. /
            连接器缺失、超出限制或无法连接时抛出。
    """
    registry = request.app.state.connectors
    async with registry.lock:
        try:
            item = await registry.toggle(connector_id, body.enabled, body.revision)
        except KeyError as exc:
            raise problem(404, "CONNECTOR_NOT_FOUND", "Connector not found.") from exc
        except ValueError as exc:
            raise problem(422, "CONNECTOR_LIMIT", str(exc)) from exc
        except ConnectorChangedError as exc:
            raise problem(409, "CONNECTOR_CHANGED", str(exc)) from None
        except Exception as exc:
            raise problem(
                502,
                "CONNECTOR_CONNECTION_FAILED",
                "Could not connect to MCP. Check its command, URL and credentials; "
                "the previous enabled state was preserved.",
            ) from exc
        request.app.state.reload_models()
    return item


@router.delete("/connectors/{connector_id}", status_code=204, responses=ERRORS)
async def delete_connector(request: Request, connector_id: str):
    """Delete one imported connector and its bundle files. / 删除单个导入连接器及其包文件。

    Args:
        request: HTTP request exposing the connector registry. / 暴露连接器目录的 HTTP 请求。
        connector_id: Imported connector key to delete. / 待删除的导入连接器键。

    Returns:
        Empty 204 response. / 空的 204 响应。

    Raises:
        HTTPException: If the connector does not exist. / 连接器不存在时抛出。
    """
    registry = request.app.state.connectors
    async with registry.lock:
        try:
            registry.delete(connector_id)
        except KeyError as exc:
            raise problem(404, "CONNECTOR_NOT_FOUND", "Imported connector not found.") from exc
        request.app.state.reload_models()
    return Response(status_code=204)
