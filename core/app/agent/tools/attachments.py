"""Read-only tool adapters for files uploaded to the current thread."""

from typing import Annotated

from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langgraph.prebuilt import InjectedState
from pydantic import Field

from app.infrastructure.conversations import SQLiteConversationStore


def create_attachment_tools(store: SQLiteConversationStore) -> list[BaseTool]:
    async def list_uploaded_files(
        user: Annotated[str, InjectedState("user_id")],
        thread: Annotated[str, InjectedState("thread_id")],
    ):
        return await store.list_attachments(user, limit=50, offset=0, thread_id=thread)

    async def read_uploaded_file(
        file_id: Annotated[str, Field(min_length=1, max_length=128)],
        user: Annotated[str, InjectedState("user_id")],
        thread: Annotated[str, InjectedState("thread_id")],
        offset: Annotated[int, Field(ge=0)] = 0,
    ):
        try:
            item = (await store.read_attachments(user, thread, [file_id]))[0]
        except LookupError as error:
            raise ToolException(str(error)) from error
        end = offset + 8_000
        return {
            "filename": item["filename"],
            "text": item["content"][offset:end],
            "next_offset": end if end < len(item["content"]) else None,
        }

    return [
        StructuredTool.from_function(
            coroutine=list_uploaded_files,
            description="List uploaded files and their IDs in the current conversation.",
        ),
        StructuredTool.from_function(
            coroutine=read_uploaded_file,
            description="Read a page of text from an uploaded file in the current conversation.",
            handle_tool_error=True,
        ),
    ]
