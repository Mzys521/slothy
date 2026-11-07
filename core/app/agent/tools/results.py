"""Normalize SDK/native results at the gateway boundary. / 在网关边界规范化 SDK 与原生结果。"""

import json

from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException
from pydantic import ValidationError

from app.agent.tools.descriptor import ToolDescriptor
from app.config import ToolSettings
from app.tools.contracts import ToolOperationError, ToolResult, artifact_for, failure


def error_result(error: Exception) -> ToolResult:
    """
    Convert one execution exception into a model-safe failure result. /
    将执行异常转换为模型安全的失败结果。

    Args:
        error: Exception raised by a tool or its adapter. / 工具或其适配器抛出的异常。

    Returns:
        Failed ToolResult with a stable code and bounded message. /
        含稳定错误码与有界消息的失败 ToolResult。
    """
    if isinstance(error, ToolOperationError):
        return error.result
    if isinstance(error, ValidationError) or type(error).__name__ in {
        "ToolInvocationError",
        "ValidationError",
        "SchemaError",
    }:
        return failure(
            "INVALID_ARGS", "Invalid tool arguments.", hint="Follow the tool's JSON schema."
        )
    if isinstance(error, TimeoutError):
        return failure(
            "TIMEOUT",
            "Tool execution timed out.",
            retryable=True,
            hint="Reduce the work and submit another call.",
        )
    if isinstance(error, ToolException):
        return failure(
            "INVALID_ARGS"
            if isinstance(error.__cause__, (ValueError, ArithmeticError))
            else "EXEC_FAILED",
            "Tool execution failed.",
            hint="Check the arguments or connector settings.",
        )
    if isinstance(error, (ValueError, ArithmeticError)):
        return failure(
            "INVALID_ARGS", "Invalid tool arguments.", hint="Check values, paths and limits."
        )
    if isinstance(error, PermissionError):
        return failure("DENIED", "The operating system denied file access.")
    if isinstance(error, FileNotFoundError):
        return failure("INVALID_ARGS", "The requested file or directory is unavailable.")
    return failure(
        "EXEC_FAILED", "Tool execution failed.", hint="Inspect the request before retrying."
    )


def tool_error_content(error: Exception) -> str:
    """
    Serialize an exception as model-visible tool error content. /
    将异常序列化为模型可见工具错误内容。

    Args:
        error: Exception raised by a tool or its adapter. / 工具或其适配器抛出的异常。

    Returns:
        Wire JSON for the converted failure result. / 转换后失败结果的线格式 JSON。
    """
    return error_result(error).wire()


def normalize_result(
    message: ToolMessage, descriptor: ToolDescriptor, settings: ToolSettings | None, key: str
) -> ToolResult:
    """
    Normalize one SDK ToolMessage into the shared result contract. /
    将 SDK ToolMessage 规范化为共享结果契约。

    Args:
        message: SDK tool message carrying text and success/error status. /
        含文本与成功/错误状态的 SDK 工具消息。
        descriptor: Tool descriptor supplying the expected result kind. /
        提供预期结果类型的工具描述符。
        settings: Optional tool settings used for artifact metadata. /
        用于产物元数据的可选工具配置。
        key: Run or invocation identity mixed into artifact ids. / 混入产物 id
        的运行或调用标识。

    Returns:
        Normalized ToolResult or structured failure envelope. / 规范化 ToolResult
        或结构化失败信封。
    """
    text = message.text
    if message.status == "error":
        try:
            result = ToolResult.model_validate_json(text)
            if not result.ok:
                return result
        except ValueError:
            pass
        return failure("EXEC_FAILED", "Tool execution failed.", hint="Check the arguments.")
    if descriptor.result_kind == "decimal":
        return ToolResult(ok=True, summary="Financial calculation completed.", data={"value": text})
    try:
        value = json.loads(text)
    except ValueError:
        value = text
    if descriptor.result_kind == "envelope":
        return ToolResult.model_validate(value)
    if descriptor.result_kind == "value":
        return ToolResult(ok=True, summary="Calculation completed.", data={"value": value})
    if descriptor.result_kind == "items":
        import mimetypes

        items = [
            {
                **item,
                "name": item.get("filename"),
                "size": item.get("byte_size"),
                "mime": mimetypes.guess_type(item.get("filename", ""))[0]
                or "application/octet-stream",
            }
            for item in value
        ]
        return ToolResult(ok=True, summary="Listed conversation uploads.", data={"items": items})
    if descriptor.result_kind == "page":
        return ToolResult(
            ok=True,
            summary="Read a page of reference text.",
            data={name: item for name, item in value.items() if name != "next_offset"},
            next_offset=value.get("next_offset"),
        )
    if descriptor.result_kind in {"command", "artifacts"}:
        if not isinstance(value, dict):
            return failure("EXEC_FAILED", "Executor returned an invalid result.")
        if value.get("ok") is False:
            code = {
                "OUTPUT_LIMIT": "OUTPUT_TOO_LARGE",
                "ARTIFACT_LIMIT": "OUTPUT_TOO_LARGE",
                "ARTIFACT_PATH_REJECTED": "OUT_OF_WORKSPACE",
                "TIMEOUT": "TIMEOUT",
            }.get(value.get("error"), "EXEC_FAILED")
            return failure(
                code,
                f"Execution failed: {value.get('error') or 'process error'}.",
                retryable=code == "TIMEOUT",
            )
        artifacts = []
        if descriptor.result_kind == "artifacts" and settings:
            for item in value.get("files", []):
                path = settings.workspace_path / item["path"]
                if (
                    not path.resolve().is_relative_to(settings.workspace_path)
                    or path.resolve() != path.absolute()
                ):
                    return failure("OUT_OF_WORKSPACE", "Artifact path is outside the workspace.")
                artifacts.append(artifact_for(path, settings.workspace_path, key))
        return ToolResult(
            ok=True,
            summary="Generated artifacts." if artifacts else "Command completed.",
            data={name: item for name, item in value.items() if name not in {"files", "ok"}},
            artifacts=artifacts,
        )
    return ToolResult(
        ok=True,
        summary=f"{descriptor.name} completed.",
        data={"output": value}
        if descriptor.result_kind == "connector" or not isinstance(value, (dict, list))
        else value,
    )
