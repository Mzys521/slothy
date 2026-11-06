"""Connector HTTP contract; no execution or registry logic in routes."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError

from app.connectors.registry import MAX_IMPORT

router = APIRouter()


class ConnectorPublic(BaseModel):
    id: str
    kind: Literal["local", "mcp", "skill"]
    name: str
    description: str
    enabled: bool
    builtin: bool
    tools: list[str]
    transport: str | None
    file_count: int


class ConnectorList(BaseModel):
    items: list[ConnectorPublic]


class ConnectorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class ConnectorError(BaseModel):
    code: str
    message: str
    request_id: str


ERRORS = {status: {"model": ConnectorError} for status in (404, 409, 413, 422, 502)}


def problem(status, code, message):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.get("/connectors", response_model=ConnectorList)
async def list_connectors(request: Request):
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
            # Pydantic errors can contain configuration secrets; do not echo input.
            message = (
                "Invalid connector format. Check the import format."
                if isinstance(exc, (ValidationError, SchemaError, RecursionError))
                else str(exc)[:500]
            )
            raise problem(422, "CONNECTOR_INVALID", message) from exc
    return {"items": items}


@router.patch("/connectors/{connector_id}", response_model=ConnectorPublic, responses=ERRORS)
async def update_connector(request: Request, connector_id: str, body: ConnectorUpdate):
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
    registry = request.app.state.connectors
    async with registry.lock:
        try:
            registry.delete(connector_id)
        except KeyError as exc:
            raise problem(404, "CONNECTOR_NOT_FOUND", "Imported connector not found.") from exc
        request.app.state.reload_models()
    return Response(status_code=204)
