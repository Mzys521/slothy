"""Compare real SDK-shaped prefix bytes without remote models. / 离线比较真实消息前缀字节。"""

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


async def probe():
    requests = []

    def respond(messages):
        requests.append(
            json.dumps(
                convert_to_openai_messages(messages),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
        return AIMessage(content="Inspect the current workspace and verify each change. " * 10)

    settings = AgentSettings(_env_file=None, personalization_enabled=False)
    services = AgentServices(
        SimpleNamespace(get_chat_model=lambda: RunnableLambda(respond)),
        skill_reader=lambda: [
            {
                "id": "development",
                "name": "Development guidance",
                "instructions": "Use existing interfaces. Verify the result. " * 80,
            }
        ],
    )
    graph = build_agent_graph(services, settings=settings, checkpointer=InMemorySaver())
    for index in range(20):
        state = create_initial_state(
            user_id="probe",
            thread_id="runtime",
            run_id=f"r{index}",
            user_input=f"Describe implementation step {index}.",
            settings=settings,
        )
        await graph.ainvoke(state, build_graph_config(state, settings))
    rows = []
    for index, (previous, current) in enumerate(zip(requests, requests[1:]), 2):
        common = next(
            (n for n, (a, b) in enumerate(zip(previous, current)) if a != b),
            min(len(previous), len(current)),
        )
        rows.append(
            {
                "turn": index,
                "input_bytes": len(current),
                "common_prefix_bytes": common,
                "common_prefix_share": round(common / len(current), 4),
            }
        )
    return {
        "method": "offline serialized request common-prefix bytes; not provider cache billing",
        "turns": len(requests),
        "provider_cache_read_tokens": None,
        "after_turn_10_mean_prefix_share": round(
            sum(r["common_prefix_share"] for r in rows[8:]) / len(rows[8:]), 4
        ),
        "total_input_bytes": sum(map(len, requests)),
        "requests": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    destination = parser.parse_args().output
    result = asyncio.run(probe())
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "requests"}))
