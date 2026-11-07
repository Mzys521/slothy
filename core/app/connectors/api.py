"""Connector HTTP contract; routes hold no execution or registry logic. /
连接器 HTTP 契约；路由不包含执行或目录逻辑。"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from app.connectors.registry import MAX_IMPORT

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


class ConnectorList(BaseModel):
    """All connectors visible to the desktop client. / 桌面客户端可见的全部连接器。"""

    items: list[ConnectorPublic]


class ConnectorUpdate(BaseModel):
    """Enable or disable one connector. / 启用或停用单个连接器。"""

    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class ConnectorError(BaseModel):
    """Error body returned by the connector routes. / 连接器路由返回的错误响应体。"""

    code: str
    message: str
    request_id: str


ERRORS = {status: {"model": ConnectorError} for status in (404, 409, 413, 422, 502)}


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
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_IMPORT:
            raise problem(413, "CONNECTOR_TOO_LARGE", "Imports must be no larger than 10 MiB.")
        data.extend(chunk)
    registry = request.app.state.connectors
    async with registry.lock:
        try:
            items = await registry.import_file(kind, filename, bytes(data))
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
            item = await registry.toggle(connector_id, body.enabled)
        except KeyError as exc:
            raise problem(404, "CONNECTOR_NOT_FOUND", "Connector not found.") from exc
        except ValueError as exc:
            raise problem(422, "CONNECTOR_LIMIT", str(exc)) from exc
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
