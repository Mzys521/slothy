import asyncio
import json

from langchain_core.tools import StructuredTool

from app.agent.metrics import run_metrics
from app.agent.tools import Risk, ToolDescriptor, ToolRegistry, create_builtin_tools
from app.config import ToolSettings
from tests.support.tool_calls import call, execute_calls


async def test_call_budget_and_failure_streak_are_per_run(tmp_path):
    settings = ToolSettings(_env_file=None, workspace_path=tmp_path, max_calls_per_run=2)
    registry = ToolRegistry.from_tools(create_builtin_tools(settings))
    args = {"operation": "add", "a": 1, "b": 2}
    results, state = await execute_calls(
        registry, [call("calculate", args, str(i)) for i in range(3)]
    )
    assert [item.ok for item in results] == [True, True, False]
    assert results[-1].error.code == "BUDGET_EXCEEDED"
    (metric,) = run_metrics(state).tools
    assert metric.calls == 3 and metric.ok == 2 and metric.errors == 1
    results, _ = await execute_calls(registry, [call("calculate", args)])
    assert results[0].ok

    registry.settings = settings.model_copy(
        update={"max_calls_per_run": 12, "failure_streak_limit": 2}
    )
    invalid = {"operation": "divide", "a": 1, "b": 0}
    results, _ = await execute_calls(
        registry, [call("calculate", invalid, str(i)) for i in range(3)]
    )
    assert results[-1].error.code == "BUDGET_EXCEEDED"


async def test_total_output_budget_limits_and_unknown_tool(tmp_path):
    registry = ToolRegistry.from_tools(
        create_builtin_tools(
            ToolSettings(_env_file=None, workspace_path=tmp_path, max_total_output_chars=256)
        )
    )
    results, _ = await execute_calls(
        registry,
        [
            call("get_datetime", {}, "time"),
            call("missing", {}, "missing"),
        ],
    )
    assert results[0].error.code == "BUDGET_EXCEEDED"
    assert results[1].error.code == "UNKNOWN_TOOL"


async def test_single_timeout_and_total_execution_budget(tmp_path):
    async def wait():
        await asyncio.sleep(0.1)
        return "done"

    tool = StructuredTool.from_function(coroutine=wait, description="Wait.")
    registry = ToolRegistry()
    registry.settings = ToolSettings(_env_file=None, workspace_path=tmp_path)
    registry.register(ToolDescriptor.from_tool(tool, risk=Risk.READ, timeout_seconds=0.01), tool)
    results, state = await execute_calls(registry, [call("wait", {})])
    assert results[0].error.code == "TIMEOUT"
    assert run_metrics(state).tools[0].timed_out == 1
    registry.settings = registry.settings.model_copy(update={"max_total_duration_seconds": 0.005})
    results, _ = await execute_calls(registry, [call("wait", {})])
    assert results[0].error.code == "BUDGET_EXCEEDED"


async def test_failure_messages_never_expose_exception_credentials(tmp_path):
    async def fail():
        raise RuntimeError("api_key=private-token")

    tool = StructuredTool.from_function(coroutine=fail, description="Fail.")
    registry = ToolRegistry.from_tools(
        [tool], settings=ToolSettings(_env_file=None, workspace_path=tmp_path)
    )
    results, state = await execute_calls(registry, [call("fail", {})])
    assert results[0].error.code == "EXEC_FAILED"
    assert "private-token" not in json.dumps(state["messages"][-1].model_dump())
