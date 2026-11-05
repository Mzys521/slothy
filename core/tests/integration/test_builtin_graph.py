"""内置工具通过真实图运行。 / Execute registered built-ins through the actual graph."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.agent.graph import AgentServices, build_agent_graph, create_initial_state
from app.agent.tools import create_builtin_tools
from app.config import AgentSettings, ToolSettings


class BuiltinModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs: Any):
        return self


@dataclass
class BuiltinProvider:
    model: BaseChatModel

    def get_chat_model(self) -> BaseChatModel:
        return self.model


async def test_graph_calls_real_float_and_financial_tools(tmp_path: Path):
    model = BuiltinModel(
        responses=[
            AIMessage(
                id="request",
                content="",
                tool_calls=[
                    {
                        "id": "normal",
                        "name": "calculate",
                        "args": {"operation": "add", "a": 0.1, "b": 0.2},
                    },
                    {
                        "id": "money",
                        "name": "calculate_financial",
                        "args": {"operation": "add", "a": "0.1", "b": "0.2"},
                    },
                ],
            ),
            AIMessage(id="answer", content="金额结果为 0.30"),
        ]
    )
    settings = AgentSettings(_env_file=None)
    graph = build_agent_graph(
        AgentServices(BuiltinProvider(model)),
        settings=settings,
        tools=create_builtin_tools(ToolSettings(workspace_path=tmp_path, _env_file=None)),
    )
    result = await graph.ainvoke(
        create_initial_state(
            user_id="u", thread_id="t", user_input="计算普通数与金额", settings=settings
        )
    )
    outputs = {
        message.tool_call_id: message.content
        for message in result["messages"]
        if message.type == "tool"
    }
    assert outputs["normal"] == "0.30000000000000004"
    assert outputs["money"] == "0.30"
    assert result["response"] == "金额结果为 0.30"


async def test_graph_returns_actual_python_document_artifacts(tmp_path: Path):
    code = 'from pathlib import Path\nPath("report.md").write_text("# Report", encoding="utf-8")'
    model = BuiltinModel(
        responses=[
            AIMessage(
                id="request",
                content="",
                tool_calls=[
                    {"id": "document", "name": "write_document_python", "args": {"code": code}}
                ],
            ),
            AIMessage(id="answer", content="文档已生成"),
        ]
    )
    settings = AgentSettings(_env_file=None)
    graph = build_agent_graph(
        AgentServices(BuiltinProvider(model)),
        settings=settings,
        tools=create_builtin_tools(ToolSettings(workspace_path=tmp_path, _env_file=None)),
    )
    result = await graph.ainvoke(
        create_initial_state(user_id="u", thread_id="t", user_input="生成文档", settings=settings)
    )
    output = next(message for message in result["messages"] if message.type == "tool")
    payload = json.loads(output.content)
    assert payload["ok"] and len(payload["files"]) == 1
    assert (tmp_path / payload["files"][0]["path"]).read_text(encoding="utf-8") == "# Report"


async def test_graph_reports_numeric_money_arguments_as_validation_error(tmp_path: Path):
    model = BuiltinModel(
        responses=[
            AIMessage(
                id="request",
                content="",
                tool_calls=[
                    {
                        "id": "money",
                        "name": "calculate_financial",
                        "args": {"operation": "add", "a": 0.1, "b": 0.2},
                    }
                ],
            ),
            AIMessage(id="answer", content="金额需要字符串参数"),
        ]
    )
    settings = AgentSettings(_env_file=None)
    graph = build_agent_graph(
        AgentServices(BuiltinProvider(model)),
        settings=settings,
        tools=create_builtin_tools(ToolSettings(workspace_path=tmp_path, _env_file=None)),
    )
    result = await graph.ainvoke(
        create_initial_state(user_id="u", thread_id="t", user_input="金额计算", settings=settings)
    )
    output = next(message for message in result["messages"] if message.type == "tool")
    assert output.status == "error" and "String amounts" in output.content
