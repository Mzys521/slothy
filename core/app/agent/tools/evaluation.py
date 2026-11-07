"""Tool-proposal scoring; fixture validation is distinct from live model quality.
/ 工具提案评分；夹具校验不等同于真实模型质量。
"""

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.utils.function_calling import convert_to_openai_tool


def contains(actual, expected):
    """Recursively check that a value contains the expected structure.
    / 递归检查一个值是否包含期望的结构。

    Args:
        actual: Value to inspect, such as tool-call arguments.
            / 要检查的值，例如工具调用参数。
        expected: Expected scalar, mapping or list structure.
            / 期望的标量、映射或列表结构。

    Returns:
        True when all expected values are present and equal.
        / 当所有期望值都存在且相等时返回 True。
    """
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and contains(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(contains(left, right) for left, right in zip(actual, expected, strict=True))
        )
    return (
        type(actual) is type(expected)
        and actual == expected
        or (
            isinstance(actual, (float, int))
            and not isinstance(actual, bool)
            and isinstance(expected, (float, int))
            and not isinstance(expected, bool)
            and actual == expected
        )
    )


def canonical_proposal(call):
    """Map a tool call to the canonical evaluation name and arguments.
    / 将工具调用映射为规范的评估名称与参数。

    Args:
        call: Tool call mapping with "name" and optional "args".
            / 含 "name" 与可选 "args" 的工具调用字典。

    Returns:
        Canonical tool name and arguments used for scoring.
        / 用于评分的规范工具名称与参数。
    """
    name, arguments = call["name"], call.get("args", {})
    if name == "write_document_python":
        return "run_python", arguments
    if name == "run_command":
        command = arguments.get("command")
        args = arguments.get("arguments", [])
        if command in {"ls", "cat", "mkdir"}:
            return (
                "workspace_write" if command == "mkdir" else "workspace_read",
                {
                    "action": {"ls": "list", "cat": "read", "mkdir": "mkdir"}[command],
                    "path": args[0] if args else ".",
                },
            )
        if command == "rg":
            return "workspace_read", {
                "action": "search",
                "pattern": args[0] if args else "",
                "path": args[1] if len(args) > 1 else ".",
            }
    return name, arguments


async def evaluate_proposals(
    model, cases: list[dict], catalogs: dict[str, list], *, instructions=None
):
    """Score proposal-only tool calls against golden cases.
    / 依据黄金用例对仅提案的工具调用进行评分。

    Args:
        model: Chat model used to propose one tool call per case.
            / 用于为每个用例提出一次工具调用的聊天模型。
        cases: Golden cases with prompt, expected name and args.
            / 含提示词、期望名称与参数的黄金用例。
        catalogs: Tool lists keyed by chat/work mode.
            / 按 chat/work 模式索引的工具列表。
        instructions: Optional extra system instructions per mode.
            / 各模式可选的额外系统指令。

    Returns:
        Accuracy summary and per-case selection results.
        / 准确率汇总与逐用例选择结果。
    """
    import jsonschema

    catalogs = {
        mode: sorted(tools, key=lambda tool: convert_to_openai_tool(tool)["function"]["name"])
        for mode, tools in catalogs.items()
    }
    bound = {mode: model.bind_tools(tools) for mode, tools in catalogs.items()}
    rows = []
    for case in cases:
        mode = case.get("mode", "work")
        system = (
            "Select one available tool for the request; do not execute it. If no tool supports "
        )
        system += (
            "the operation, state that limitation. Use literal paths and values from the request."
        )
        if instructions:
            system += instructions.get(mode, "")
        response = await bound[mode].ainvoke(
            [SystemMessage(content=system), HumanMessage(content=case["prompt"])]
        )
        calls = response.tool_calls
        name_correct = args_correct = schema_valid = False
        selected = calls[0]["name"] if len(calls) == 1 else None
        if len(calls) == 1:
            name, arguments = canonical_proposal(calls[0])
            name_correct = name == case["expected"]["name"]
            args_correct = name_correct and contains(arguments, case["expected"].get("args", {}))
            schema = next(
                (
                    convert_to_openai_tool(tool)["function"]["parameters"]
                    for tool in catalogs[mode]
                    if convert_to_openai_tool(tool)["function"]["name"] == selected
                ),
                None,
            )
            if schema:
                try:
                    jsonschema.Draft202012Validator(schema).validate(calls[0].get("args", {}))
                    schema_valid = True
                except jsonschema.ValidationError:
                    pass
        rows.append(
            {
                "id": case["id"],
                "selected": selected,
                "name_correct": name_correct,
                "arguments_correct": args_correct,
                "schema_valid": schema_valid,
            }
        )
    count = len(rows)
    return {
        "cases": count,
        "tool_accuracy": sum(row["name_correct"] for row in rows) / count,
        "argument_accuracy": sum(row["arguments_correct"] for row in rows) / count,
        "schema_accuracy": sum(row["schema_valid"] for row in rows) / count,
        "results": rows,
    }
