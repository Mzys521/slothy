"""Trusted invocation identity injected by the gateway, never supplied by a model. /
由网关注入的可信调用身份，模型无法提供。"""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolExecutionContext:
    """
    Trusted identity available to a tool during one invocation. /
    单次调用期间工具可用的可信身份。

    The gateway builds it from graph state; model-supplied arguments can never set it. /
    网关从图状态构造它，模型提供的参数无法设置。
    """

    key: str
    user_id: str
    thread_id: str
    run_id: str


execution_context: ContextVar[ToolExecutionContext | None] = ContextVar(
    "tool_execution", default=None
)
