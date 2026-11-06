"""Keep tests isolated from local credentials and provider configuration."""

import os

import pytest

from app.config import (
    AgentSettings,
    CheckpointSettings,
    LLMSettings,
    MemorySettings,
    PromptSettings,
    ToolSettings,
)


@pytest.fixture(autouse=True)
def isolate_llm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # 中文：测试不得意外读取真实 .env 或厂商密钥。
    # English: Prevent tests from reading real dotenv files or vendor credentials.
    for settings_class in (
        LLMSettings,
        AgentSettings,
        CheckpointSettings,
        ToolSettings,
        MemorySettings,
        PromptSettings,
    ):
        monkeypatch.setitem(settings_class.model_config, "env_file", None)
    for name in tuple(os.environ):
        if name.upper().startswith(
            (
                "LLM_",
                "AGENT_",
                "CHECKPOINT_",
                "TOOLS_",
                "OPENAI_",
                "DEEPSEEK_",
                "MIMO_",
                "MEMORY_",
                "PROMPT_",
                "HISTORY_",
                "RETRIEVAL_",
            )
        ) or (
            name.upper()
            in {"PERSONALIZATION_ENABLED", "MEMORY_TOP_K", "MEMORY_CONTEXT_TOKEN_BUDGET"}
        ):
            monkeypatch.delenv(name)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
