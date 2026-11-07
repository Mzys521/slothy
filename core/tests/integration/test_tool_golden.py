import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from app.agent.tools.evaluation import canonical_proposal, contains, evaluate_proposals
from examples.evaluate_tool_golden import catalogs

CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "tool_golden.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_golden_expected_proposals_are_supported_by_final_schemas(tmp_path, case):
    registry = catalogs(tmp_path)
    selected = next(
        tool for tool in registry.for_mode("work") if tool.name == case["expected"]["name"]
    )
    schema = convert_to_openai_tool(selected)["function"]["parameters"]
    Draft202012Validator(schema).validate(case["expected"]["args"])


async def test_evaluator_scores_names_arguments_schema_and_legacy_equivalence(tmp_path):
    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    cases = CASES[:3]
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": case["expected"]["name"], "args": case["expected"]["args"], "id": "case"}
            ],
        )
        for case in cases
    ]
    model = Model(responses=responses)
    report = await evaluate_proposals(model, cases, {"work": catalogs(tmp_path).for_mode("work")})
    assert report["tool_accuracy"] == report["argument_accuracy"] == report["schema_accuracy"] == 1
    model.responses = [AIMessage(content="I cannot perform this operation.")]
    model.i = 0
    report = await evaluate_proposals(model, cases, {"work": catalogs(tmp_path).for_mode("work")})
    assert report["tool_accuracy"] == report["argument_accuracy"] == 0
    assert canonical_proposal(
        {"name": "run_command", "args": {"command": "cat", "arguments": ["x.txt"]}}
    ) == ("workspace_read", {"action": "read", "path": "x.txt"})
    assert not contains({"value": 0.1}, {"value": "0.1"})
