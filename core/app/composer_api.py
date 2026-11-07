"""Composer commands reuse existing services. / 输入区命令复用既有服务。"""

import asyncio
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Query, Request
from langchain_core.messages import HumanMessage, RemoveMessage
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.agent.graph import build_graph_config
from app.agent.prompting.history import extractive_summary
from app.tools.artifacts import ArtifactStore, invocation_key
from app.tools.context import ToolExecutionContext, execution_context
from app.tools.contracts import ToolOperationError, ToolResult
from app.tools.documents import DocumentExporter


class CommandInput(BaseModel):
    """Explicit confirmation for a bounded, named operation. / 对有界命名操作的显式确认。"""

    model_config = ConfigDict(extra="forbid")
    confirmed: StrictBool
    request_id: UUID | None = None


class ExportInput(CommandInput):
    format: Literal["md", "docx"] = "md"


class CommandEvent(BaseModel):
    """Audit identities, not executable user strings. / 审计命令标识，不接收执行字符串。"""

    model_config = ConfigDict(extra="forbid")
    name: Literal[
        "new",
        "rename",
        "stop",
        "retry",
        "status",
        "compact",
        "clear",
        "model",
        "mode",
        "workspace",
        "attach",
        "export",
        "tools",
        "plugins",
        "memory",
        "graph",
        "settings",
        "language",
        "theme",
        "help",
    ]
    thread_id: str | None = Field(default=None, min_length=1, max_length=256)


def install_composer_routes(app, *, user, require_thread, lock_for, problem):
    """Inject existing ownership and lock helpers. / 注入既有归属校验与锁。"""
    router = APIRouter()

    @router.get("/commands/usage")
    async def command_usage(request: Request):
        return await request.app.state.conversations.composer_usage(user)

    @router.post("/commands/events")
    async def command_event(request: Request, body: CommandEvent):
        if body.thread_id:
            await require_thread(request, body.thread_id)
        await request.app.state.conversations.record_command(user, body.thread_id or "", body.name)
        return {"ok": True}

    async def search(
        request: Request,
        kind: str,
        thread_id: str | None,
        workspace_id: str,
        q: str,
        path: str,
        limit: int,
    ):
        if thread_id:
            await require_thread(request, thread_id)
        try:
            async with asyncio.timeout(1.1):
                items = await request.app.state.references.search(
                    user, thread_id, kind, q, path, limit, workspace_id
                )
            return {"items": items}
        except (LookupError, ValueError, OSError, ToolOperationError, TimeoutError):
            raise problem(
                422, "REFERENCE_UNAVAILABLE", "引用不可用，请检查工作区。 / Reference unavailable."
            ) from None

    # Separate routes make the public kind enumeration and endpoint whitelist explicit.
    # 独立路由构成显式类型枚举与端点白名单。
    def search_route(kind):
        async def endpoint(
            request: Request,
            thread_id: str | None = Query(None, max_length=256),
            workspace_id: str = Query("personal", max_length=256),
            q: str = Query("", max_length=120),
            path: str = Query(".", max_length=4096),
            limit: int = Query(8, ge=1, le=8),
        ):
            return await search(request, kind, thread_id, workspace_id, q, path, limit)

        return endpoint

    for plural, kind in (
        ("folders", "folder"),
        ("files", "file"),
        ("sessions", "session"),
        ("plugins", "plugin"),
    ):
        router.add_api_route(
            "/references/" + plural, search_route(kind), methods=["GET"], name="reference_" + plural
        )

    async def checked(request, thread_id, body):
        thread = await require_thread(request, thread_id, writable=True)
        if body.confirmed is not True:
            raise problem(
                409, "CONFIRMATION_REQUIRED", "请先确认此操作。 / Confirm this operation."
            )
        if lock_for(request, thread_id).locked():
            raise problem(409, "RUN_PENDING", "请先停止当前运行。 / Stop the current run first.")
        return thread

    @router.post("/threads/{thread_id}/compact")
    async def compact(request: Request, thread_id: str, body: CommandInput):
        thread = await checked(request, thread_id, body)
        async with lock_for(request, thread_id):
            config = build_graph_config(
                {"user_id": user, "thread_id": thread_id}, request.app.state.agent_settings
            )
            runner = (
                request.app.state.work_runner
                if thread["mode"] == "work"
                else request.app.state.runner
            )
            if runner is None:
                raise problem(503, "MODEL_UNAVAILABLE", "请先配置模型。 / Configure a model first.")
            snapshot = await runner.graph.aget_state(config)
            if snapshot.next:
                raise problem(
                    409, "RUN_PENDING", "请先恢复或清理未完成运行。 / Resume the pending run first."
                )
            messages = list(snapshot.values.get("messages", []))
            turns = [i for i, item in enumerate(messages) if isinstance(item, HumanMessage)]
            if len(turns) <= 2:
                return {
                    "compacted": False,
                    "version": snapshot.values.get("history_summary", {}).get("version", 0),
                    "summary": "上下文较短，无需压缩。 / Context is already short.",
                }
            start = turns[-2]
            previous = snapshot.values.get("history_summary") or {}
            summary = {
                "version": previous.get("version", 0) + 1,
                "from_turn": 1,
                "through_turn": previous.get("through_turn", 0) + len(turns) - 2,
                "method": "extractive",
                "text": extractive_summary(messages[:start], previous),
            }
            await runner.graph.aupdate_state(
                config,
                {
                    "history_summary": summary,
                    "history_compression_pending": False,
                    "messages": [RemoveMessage(id=item.id) for item in messages[:start]],
                },
                as_node="finalize_response",
            )
            await request.app.state.conversations.record_command(user, thread_id, "compact")
            return {
                "compacted": True,
                "version": summary["version"],
                "summary": "已压缩上下文，完整会话仍保留。 / Context compacted; archive preserved.",
            }

    @router.post("/threads/{thread_id}/export")
    async def export(request: Request, thread_id: str, body: ExportInput):
        thread = await checked(request, thread_id, body)
        async with lock_for(request, thread_id):
            try:
                settings = await request.app.state.reference_settings_for(user, thread_id)
            except (LookupError, ValueError, OSError):
                raise problem(
                    409,
                    "WORKSPACE_DIRECTORY_UNAVAILABLE",
                    "工作区不可用。 / Workspace unavailable.",
                ) from None
            paragraphs, cursor, total = [], 0, 0
            truncated = False
            while True:
                rows = await request.app.state.conversations.list_turns(
                    user_id=user, thread_id=thread_id, after_id=cursor, limit=100
                )
                if not rows:
                    break
                for row_id, turn in rows:
                    pair = ["User / 用户\n" + turn.user_input, "Assistant / 助手\n" + turn.response]
                    if sum(map(len, paragraphs)) + sum(map(len, pair)) > 70000 or total >= 500:
                        truncated = True
                        break
                    paragraphs.extend(pair)
                    total += 1
                    cursor = row_id
                if truncated or len(rows) < 100:
                    break
            if truncated:
                paragraphs.append(
                    "[Export truncated; full conversation remains in the archive. / "
                    "导出已截断，完整记录保留在会话中。]"
                )
            sections = [
                {"heading": "", "paragraphs": paragraphs[i : i + 100], "items": []}
                for i in range(0, len(paragraphs), 100)
            ]
            run = "command-" + str(body.request_id or uuid4())
            call = {"id": run, "name": "export_document", "args": {"format": body.format}}
            key = invocation_key(dict(user_id=user, thread_id=thread_id, run_id=run), call)
            context = ToolExecutionContext(key, user, thread_id, run)
            token = execution_context.set(context)
            try:

                async def invoke():
                    raw = await DocumentExporter(settings).run(
                        body.format, "conversation." + body.format, thread["title"][:200], sections
                    )
                    result = ToolResult.model_validate(raw)
                    result.data = {"exported_turns": total, "truncated": truncated}
                    return result

                result, reused, _ = await ArtifactStore(settings).execute(
                    context, "export_document", invoke, version="2"
                )
            except ToolOperationError as error:
                raise problem(422, error.result.error.code, error.result.summary) from None
            except (OSError, ValueError):
                raise problem(
                    422, "EXPORT_FAILED", "无法导出文档，请检查工作目录。 / Export failed."
                ) from None
            finally:
                execution_context.reset(token)
            for artifact in result.artifacts:
                artifact.url = f"/api/v1/threads/{thread_id}/artifacts/{artifact.id}"
            result.data = {**(result.data or {}), "reused": reused}
            await request.app.state.conversations.record_command(user, thread_id, "export")
            return result

    app.include_router(router, prefix="/api/v1")
