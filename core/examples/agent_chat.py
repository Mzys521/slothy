"""在线 Agent 图示例。 / Online graph example: python -m examples.agent_chat '你好' [--stream]."""

import argparse
import asyncio
import json
import sys
from uuid import uuid4

from app.agent.graph import (
    AgentServices,
    build_agent_graph,
    build_graph_config,
)
from app.agent.graph.review import pending_review
from app.agent.prompts.system import BASE_SYSTEM_PROMPT
from app.agent.tools import ToolRegistry, create_builtin_tools
from app.config import AgentSettings, CheckpointSettings
from app.infrastructure.checkpoints import SQLiteCheckpointStore
from app.infrastructure.conversations import SQLiteConversationStore
from app.infrastructure.llm import create_model_provider
from app.services import AgentRunner


async def main(args: argparse.Namespace) -> None:
    """args：由 CLI 校验的用户、线程、请求及显示选项。 / Validated CLI invocation options."""
    settings = AgentSettings()
    services = AgentServices(model_provider=create_model_provider())
    thread_id = args.thread_id if args.thread_id is not None else str(uuid4())
    message = (
        None
        if args.resume
        else ("请用一句话介绍你自己。" if args.message is None else args.message)
    )
    print(f"thread_id={thread_id}", file=sys.stderr)
    # 中文：连接覆盖整次调用，关闭后下次命令仍可使用同一线程续聊或恢复。
    # English: Keep the connection alive for the run; the same thread survives later commands.
    checkpoint_settings = CheckpointSettings()
    async with (
        SQLiteCheckpointStore(checkpoint_settings) as checkpointer,
        SQLiteConversationStore.open(checkpoint_settings) as conversations,
    ):
        registry = ToolRegistry.from_tools(create_builtin_tools())
        graph = build_agent_graph(
            services,
            settings=settings,
            checkpointer=checkpointer,
            tools=registry.for_mode("work"), tool_registry=registry, mode="work",
            require_tool_review=True,
            system_prompt=BASE_SYSTEM_PROMPT + registry.instructions("work"),
        )
        runner = AgentRunner(graph, settings, conversation_writer=conversations)
        config = build_graph_config({"user_id": args.user_id, "thread_id": thread_id}, settings)
        while True:
            review = pending_review(await graph.aget_state(config))
            decision = None
            if review:
                if message is not None:
                    raise ValueError("Use --resume to review the pending tool actions.")
                print(json.dumps(review["calls"], indent=2, ensure_ascii=False), file=sys.stderr)
                if not sys.stdin.isatty():
                    print(
                        "Approval pending. Run --resume in an interactive terminal.",
                        file=sys.stderr,
                    )
                    return
                answer = await asyncio.to_thread(input, "Run these actions? [y/N] ")
                decision = (review["approval_id"], answer.strip().lower() in {"y", "yes"})
            mode = "messages" if args.stream else "values"
            async for item in runner.astream(
                user_id=args.user_id,
                thread_id=thread_id,
                message=message,
                decision=decision,
                stream_mode=mode,
            ):
                if args.stream:
                    chunk, metadata = item
                    if metadata.get("langgraph_node") == "agent":
                        print(chunk.text, end="", flush=True)
            snapshot = await graph.aget_state(config)
            if not pending_review(snapshot):
                print("" if args.stream else snapshot.values["response"])
                return
            message = None


if __name__ == "__main__":
    # 中文：帮助信息不创建事件循环、不读取密钥或打开数据库。
    # English: Help does not start a loop, load secrets, or open a database.
    parser = argparse.ArgumentParser(description="SQLite Agent graph / SQLite 在线 Agent 图")
    parser.add_argument(
        "message", nargs="?", help="本轮请求，恢复时不传 / New request; omit to resume"
    )
    parser.add_argument("--stream", action="store_true", help="流式文本 / Stream model text")
    parser.add_argument(
        "--user-id", default="local-user", help="本地开发用户 ID / Local development user"
    )
    parser.add_argument(
        "--thread-id", help="续聊或恢复使用相同线程 / Reuse a thread to continue/resume"
    )
    parser.add_argument(
        "--resume", action="store_true", help="恢复未完成执行 / Resume an unfinished run"
    )
    args = parser.parse_args()
    if args.resume and (args.thread_id is None or args.message is not None):
        parser.error(
            "--resume 需要 --thread-id 且不传新消息 / requires a thread and no new message"
        )
    asyncio.run(main(args))
