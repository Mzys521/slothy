"""Offline prefix-stability probe. No credentials, network model or production data."""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, convert_to_openai_messages
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import (
    AgentServices,
    build_agent_graph,
    build_graph_config,
    create_initial_state,
)
from app.config import AgentSettings
from app.infrastructure.workspace import BoundedWorkspaceContext


async def probe():
    requests = []

    def respond(messages):
        wire = convert_to_openai_messages(messages)
        requests.append(
            json.dumps(wire, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        )
        return AIMessage(content="建议先确认任务边界，再拆分步骤并逐项验证。" * 16)

    class Profiles:
        async def get_profile(self, user_id):
            return {
                "user_id": user_id,
                "version": 1 + len(requests) // 3,
                "preferences": {"language": "中文", "structure": "先给结论"},
            }

    class Memories:
        async def retrieve(self, *, user_id, query, **_kwargs):
            return [
                {
                    "id": "memory-" + query,
                    "user_id": user_id,
                    "scope": "global",
                    "scope_ref": None,
                    "status": "active",
                    "type": "semantic",
                    "importance": 0.7,
                    "title": "当前项目线索",
                    "content": "与本轮问题有关的线索：" + query,
                }
            ]

    settings = AgentSettings(_env_file=None)
    graph = build_agent_graph(
        AgentServices(
            SimpleNamespace(get_chat_model=lambda: RunnableLambda(respond)),
            Profiles(),
            Memories(),
            BoundedWorkspaceContext(),
        ),
        settings=settings,
        checkpointer=InMemorySaver(),
    )
    for index in range(20):
        state = create_initial_state(
            user_id="prefix-probe",
            thread_id="offline",
            run_id=f"probe-{index}",
            user_input=f"请给出第 {index + 1} 项实施建议",
            settings=settings,
        )
        await graph.ainvoke(state, build_graph_config(state, settings))
    rows = []
    for index in range(1, len(requests)):
        previous, current = requests[index - 1], requests[index]
        common = next(
            (offset for offset, pair in enumerate(zip(previous, current)) if pair[0] != pair[1]),
            min(len(previous), len(current)),
        )
        rows.append(
            {
                "turn": index + 1,
                "input_bytes": len(current),
                "stable_prefix_bytes": common,
                "stable_prefix_share": round(common / len(current), 4),
            }
        )
    return {
        "method": "offline serialized request common-prefix bytes, not provider cache billing",
        "turns": 20,
        "after_turn_10_mean_prefix_share": round(
            sum(row["stable_prefix_share"] for row in rows[8:]) / len(rows[8:]), 4
        ),
        "provider_cache_read_tokens": None,
        "requests": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    destination = parser.parse_args().output
    result = asyncio.run(probe())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "requests"}, ensure_ascii=False
        )
    )
