"""Offline fixture checks, or explicit live proposal-only baseline comparison.
/ 离线夹具校验，或显式的在线纯提案基线对比。
"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from langchain_core.tools import StructuredTool

from app.agent.tools import Risk, ToolDescriptor, ToolRegistry, create_builtin_tools
from app.agent.tools.attachments import create_attachment_tools
from app.agent.tools.evaluation import evaluate_proposals
from app.config import LLMSettings, ToolSettings
from app.infrastructure.llm import OpenAICompatibleProvider

FIXTURES = Path(__file__).parents[1] / "tests" / "fixtures"


def catalogs(directory):
    """Build a tool registry for fixture validation, including a skill reader stub.
    / 为夹具校验构建工具注册表，包含技能读取占位工具。

    Args:
        directory: Temporary workspace directory used by built-in tools.
            / 内置工具使用的临时工作区目录。

    Returns:
        Registry containing built-in tools plus the skill-reader stub.
        / 含内置工具与技能读取占位工具的注册表。
    """
    settings = ToolSettings(
        _env_file=None,
        workspace_path=directory,
        web_fetch_enabled=True,
        web_fetch_allowed_domains=["example.com"],
    )
    registry = ToolRegistry.from_tools(
        [*create_builtin_tools(settings), *create_attachment_tools(SimpleNamespace())]
    )

    def read_skill_file(skill_id: str, path: str, offset: int = 0):
        """Stub skill-reference reader; the golden check never executes tools.
        / 技能参考读取占位函数；黄金校验不会执行工具。

        Args:
            skill_id: Enabled skill identifier requested by the model.
                / 模型请求的已启用技能标识。
            path: Reference file path inside the skill.
                / 技能内的参考文件路径。
            offset: Byte offset the reader would start from.
                / 读取器将起始的字节偏移量。

        Returns:
            None, because this stub never reads files.
            / None，因为该占位函数不读取文件。
        """
        return None

    tool = StructuredTool.from_function(
        read_skill_file, description="Read an enabled Skill reference file."
    )
    registry.register(ToolDescriptor.from_tool(tool, risk=Risk.READ, mode={"chat", "work"}), tool)
    return registry


async def main(args):
    """Validate golden fixtures offline, or compare live proposals with baselines.
    / 离线校验黄金夹具，或将在线提案与基线进行对比。

    Args:
        args: Parsed CLI arguments with live flag and output path.
            / 含 live 标志与输出路径的解析后命令行参数。
    """
    import jsonschema
    from langchain_core.utils.function_calling import convert_to_openai_tool

    cases = json.loads((FIXTURES / "tool_golden.json").read_text(encoding="utf-8"))
    with TemporaryDirectory() as directory:
        registry = catalogs(directory)
        for case in cases:
            tool = next(
                tool
                for tool in registry.for_mode(case.get("mode", "work"))
                if tool.name == case["expected"]["name"]
            )
            jsonschema.Draft202012Validator(
                convert_to_openai_tool(tool)["function"]["parameters"]
            ).validate(case["expected"]["args"])
        if not args.live:
            report = {
                "kind": "offline-fixture-validation",
                "cases": len(cases),
                "live_tool_accuracy": None,
                "message": "No model quality claim; no tools executed.",
            }
        else:
            os.environ["LANGSMITH_TRACING"] = "false"
            os.environ["LANGCHAIN_TRACING_V2"] = "false"
            settings = LLMSettings(max_tokens=512, temperature=0, timeout_seconds=30, max_retries=0)
            model = OpenAICompatibleProvider(settings).get_chat_model()
            current = await evaluate_proposals(
                model,
                cases,
                {mode: registry.for_mode(mode) for mode in ("chat", "work")},
                instructions={mode: registry.instructions(mode) for mode in ("chat", "work")},
            )
            baseline_tools = json.loads(
                (FIXTURES / "tool_golden_baseline_tools.json").read_text(encoding="utf-8")
            )
            original = json.loads((FIXTURES / "tool_layer_p0.json").read_text(encoding="utf-8"))[
                "default"
            ]
            baseline_guidance = {}
            for mode, tools in baseline_tools.items():
                names = sorted(tool["function"]["name"] for tool in tools)
                lines = original[mode]["instructions"].splitlines()
                lines[1] = "Tools available in this conversation: " + ", ".join(names) + "."
                baseline_guidance[mode] = "\n".join(lines)
            baseline = await evaluate_proposals(
                model, cases, baseline_tools, instructions=baseline_guidance
            )
            shared_ids = {
                case["id"]
                for case in cases
                if (
                    case["expected"]["name"]
                    in {
                        "calculate",
                        "calculate_financial",
                        "list_uploaded_files",
                        "read_uploaded_file",
                        "run_python",
                        "read_skill_file",
                    }
                    or (case["expected"]["name"] == "workspace_read" and case["id"] != "page")
                    or case["id"] == "mkdir"
                )
            }
            shared = {
                "cases": len(shared_ids),
                "current_accuracy": sum(
                    row["name_correct"] for row in current["results"] if row["id"] in shared_ids
                )
                / len(shared_ids),
                "baseline_accuracy": sum(
                    row["name_correct"] for row in baseline["results"] if row["id"] in shared_ids
                )
                / len(shared_ids),
            }
            report = {
                "kind": "live-proposals-only",
                "model": settings.model,
                "current": current,
                "baseline": baseline,
                "shared_capabilities": shared,
                "at_least_baseline": current["tool_accuracy"] >= baseline["tool_accuracy"]
                and shared["current_accuracy"] >= shared["baseline_accuracy"],
            }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            json.dumps(
                {key: value for key, value in report.items() if key not in {"current", "baseline"}},
                ensure_ascii=False,
            )
        )
        if args.live:
            print(
                json.dumps(
                    {
                        "current_accuracy": current["tool_accuracy"],
                        "baseline_accuracy": baseline["tool_accuracy"],
                    }
                )
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true", help="Call the configured provider; proposes tools only."
    )
    parser.add_argument(
        "--output", type=Path, default=Path("../_internal/core-qa/tool-golden.json")
    )
    arguments = parser.parse_args()
    try:
        asyncio.run(main(arguments))
    except Exception as error:
        # Provider errors can contain upstream details; never print credentials or request bodies.
        print(json.dumps({"kind": "evaluation-failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
