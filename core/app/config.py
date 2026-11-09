"""Validated model and agent runtime settings. / 模型与运行参数配置。"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    Field,
    JsonValue,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from app.personalization.settings import MemoryConfiguration

ProviderName = Literal["openai_compatible", "deepseek", "mimo"]

_DEFAULT_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "mimo": "https://api.xiaomimimo.com/v1",
}

# 中文：根目录配置先加载，后端局部配置可覆盖同名项，最后由进程环境覆盖。
# English: Load repository settings first, backend overrides next, process environment last.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_ENV_FILES = (
    ()
    if os.environ.get("SLOTHY_DESKTOP") == "1"
    else (_BACKEND_DIR.parent / ".env", _BACKEND_DIR / ".env")
)

# 中文：只从选中服务读取整组连接信息，不跨服务兜底密钥或模型。
# English: Resolve one selected service's connection variables; never mix vendor credentials.
_PROVIDER_VARIABLES = {
    "openai_compatible": {
        "api_key": "OPENAI_API_KEY",
        "base_url": "OPENAI_API_BASE",
        "model": "OPENAI_MODEL",
    },
    "deepseek": {
        "api_key": "DEEPSEEK_API_KEY",
        "base_url": "DEEPSEEK_API_URL",
        "model": "DEEPSEEK_MODEL",
    },
    "mimo": {"api_key": "MIMO_API_KEY", "base_url": "MIMO_API_BASE", "model": "MIMO_MODEL"},
}


class _ProjectSettings(BaseSettings):
    """Shared loading with hidden error inputs. / 共用加载规则，隐藏错误输入。"""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
        populate_by_name=True,
    )


class LLMSettings(_ProjectSettings):
    """Load LLM_* and the selected service's variables. / 读取 LLM_* 与服务变量。

    Explicit arguments win over LLM_* overrides and then selected-service variables;
    process environment wins over dotenv files within each variable name. /
    显式参数优先，其次 LLM_*，最后服务变量；同名变量内环境优先于文件。
    """

    model_config = SettingsConfigDict(env_prefix="LLM_")

    # 中文：服务选择器；决定读取哪组密钥、模型和地址。
    # English: Selects the vendor's credential, model, and endpoint namespace.
    provider: ProviderName = "openai_compatible"
    # Skip LLM_* overrides; read only the selected vendor's variables. / 只读所选服务变量。
    vendor_credentials_only: bool = Field(default=False, exclude=True, repr=False)
    # 中文：实际模型或部署名称，必须显式配置，不自动升级。
    # English: Explicit model/deployment name; never upgraded automatically.
    model: str = Field(min_length=1)
    # 中文：所选服务的密钥，不出现在 repr、配置导出或错误输入中。
    # English: Selected service's secret; excluded from repr, dumps, and error input displays.
    api_key: SecretStr = Field(repr=False, exclude=True)
    # 中文：HTTP(S) API 根地址，不包含 /chat/completions；命名服务可用默认地址。
    # English: HTTP(S) API base without /chat/completions; named vendors have endpoint defaults.
    base_url: AnyHttpUrl | None = None
    # 中文：每次 HTTP 尝试的连接/读取等操作超时秒数，不是整个 Graph 的总时限。
    # English: HTTP operation timeout per attempt, not a wall-clock limit for the whole graph.
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    # 中文：SDK 允许的额外重试次数；2 表示首次尝试之外最多再重试两次。
    # English: Additional SDK retries; 2 permits at most three attempts in total.
    max_retries: int = Field(default=2, ge=0)
    # 中文：采样温度 0～2；留空沿用服务默认值，思考模式可能忽略该参数。
    # English: Sampling temperature from 0 to 2; unset keeps vendor defaults and may be ignored.
    temperature: float | None = Field(default=None, ge=0, le=2)
    # 中文：单次模型调用的输出 token 上限；不是字符数，也不是个性化上下文预算。
    # English: Output token cap per model call, separate from character and context budgets.
    max_tokens: int | None = Field(default=None, gt=0)
    # 中文：是否请求流式 token 用量统计，仅在服务支持时开启。
    # English: Requests token-usage metadata during streaming when supported by the service.
    stream_usage: bool = False
    # 中文：服务扩展请求字段，例如 thinking；不用于覆盖消息或运行步数。
    # English: Vendor request extensions, such as thinking; never messages or graph step limits.
    extra_body: dict[str, JsonValue] = Field(default_factory=dict)
    # 中文：模型内置联网搜索策略：auto（支持则默认启用）/ native（仅支持时启用）/
    # off（禁用）；不支持的模型自动忽略，绝不发送不兼容参数。
    # English: Native web-search policy: auto (enable when supported) / native (only when
    # supported) / off; unsupported models are ignored and never receive incompatible fields.
    web_search: Literal["auto", "native", "off"] = "auto"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource | Callable[[], dict[str, Any]], ...]:
        """Add selected-vendor variables as the last fallback. / 服务变量作为末级回退。

        Args:
            settings_cls: Settings class holding field defaults. / 设置类。
            init_settings: Explicit constructor arguments. / 显式参数。
            env_settings: Process environment source. / 环境变量。
            dotenv_settings: Dotenv file source. / 点文件。
            file_secret_settings: File-based secret source. / 密钥文件。

        Returns:
            Sources in precedence order, vendor resolver last. / 按优先级的来源序列。
        """

        def source_value(source: PydanticBaseSettingsSource, name: str) -> Any:
            """Read one variable from a settings source. / 从设置来源读取单个变量。

            Args:
                source: Pydantic settings source. / 设置来源。
                name: Variable name. / 变量名。

            Returns:
                The stored value, or None. / 存储值或 None。
            """
            variables = getattr(source, "env_vars", {})
            return variables.get(name if source.case_sensitive else name.lower())

        def selected_vendor_source() -> dict[str, Any]:
            """Resolve variables for the selected vendor only. / 仅解析所选服务变量。"""
            selected = (
                init_settings().get("provider")
                or source_value(env_settings, "LLM_PROVIDER")
                or source_value(dotenv_settings, "LLM_PROVIDER")
                or settings_cls.model_fields["provider"].default
            )
            if not isinstance(selected, str):
                return {}
            resolved = {}
            for field, variable in _PROVIDER_VARIABLES.get(selected, {}).items():
                value = source_value(env_settings, variable) or source_value(
                    dotenv_settings, variable
                )
                if value:
                    resolved[field] = value
            return resolved

        def generic_source(source: PydanticBaseSettingsSource) -> dict[str, Any]:
            """Load one source, dropping credentials when requested. / 读取来源并按需丢弃凭据。

            Args:
                source: Pydantic settings source. / 设置来源。

            Returns:
                Field values from that source. / 该来源的字段值。
            """
            values = source()
            if init_settings().get("vendor_credentials_only"):
                for field in ("api_key", "base_url", "model", "extra_body"):
                    values.pop(field, None)
            return values

        return (
            init_settings,
            lambda: generic_source(env_settings),
            lambda: generic_source(dotenv_settings),
            lambda: generic_source(file_secret_settings),
            selected_vendor_source,
        )

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        """Strip and reject a blank model name. / 去空白并拒绝空模型名。

        Args:
            value: Raw model or deployment name. / 原始模型名。

        Returns:
            Trimmed model name. / 去空白后的名称。

        Raises:
            ValueError: If the name is blank. / 名称为空时抛出。
        """
        value = value.strip()
        if not value:
            raise ValueError("LLM_MODEL must not be blank")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        """Strip and reject a blank API key. / 去空白并拒绝空密钥。

        Args:
            value: Selected service's secret. / 所选服务密钥。

        Returns:
            Trimmed SecretStr. / 去空白后的密钥。

        Raises:
            ValueError: If the key is blank. / 密钥为空时抛出。
        """
        key = value.get_secret_value().strip()
        if not key:
            raise ValueError("LLM_API_KEY must not be blank")
        return SecretStr(key)

    @model_validator(mode="after")
    def validate_endpoint(self) -> Self:
        """Require an explicit base URL for the generic compatible provider.

        通用兼容服务必须显式提供根地址。
        """
        if self.base_url is None and self.provider == "openai_compatible":
            raise ValueError("LLM_BASE_URL is required for openai_compatible")
        return self

    @field_validator("extra_body")
    @classmethod
    def validate_extra_body(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Reject extensions that override validated core fields. / 禁止扩展覆盖核心字段。

        Args:
            value: Vendor request extensions. / 服务扩展字段。

        Returns:
            The unchanged extensions. / 原样返回扩展。

        Raises:
            ValueError: If a reserved field is present. / 出现保留字段时抛出。
        """
        # 中文：扩展字段最终会合并进请求，禁止其覆盖已校验的核心字段。
        # English: Extensions merge into the wire payload; protect validated core fields.
        reserved = {
            "model",
            "messages",
            "stream",
            "max_tokens",
            "max_completion_tokens",
        }.intersection(value)
        if reserved:
            raise ValueError(f"LLM_EXTRA_BODY cannot override: {', '.join(sorted(reserved))}")
        return value

    @property
    def resolved_base_url(self) -> str:
        """Resolve the URL without cross-vendor credentials. / 解析服务地址。"""
        if self.base_url is not None:
            return str(self.base_url).rstrip("/")
        return _DEFAULT_BASE_URLS[self.provider]


class AgentSettings(_ProjectSettings):
    """Deployment defaults for graph and personalization. / 图与个性化默认值。"""

    model_config = SettingsConfigDict(env_prefix="AGENT_")

    # 中文：单次 Graph 的最大 super-step 数；包括读取、构建、模型和工具节点。
    # English: Maximum graph super-steps per run, including reads, context, model, and tools.
    max_steps: int = Field(default=25, ge=1)
    # Legacy layout/library history cap. / 旧版历史轮次上限。
    # The layered HTTP runtime uses HISTORY_* batch limits. / 分层运行时改用 HISTORY_* 批次限制。
    history_max_turns: int = Field(default=20, ge=1)
    # 中文：画像、记忆检索和上下文构建各自的超时秒数，超时后降级为普通回答。
    # English: Timeout for each profile/retrieval/context read, followed by graceful fallback.
    context_read_timeout_seconds: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    # 中文：全局个性化开关；关闭后，单次请求不能重新开启历史画像和记忆注入。
    # English: Global personalization gate; a request cannot re-enable it when disabled.
    personalization_enabled: bool = Field(default=True, validation_alias="PERSONALIZATION_ENABLED")
    # 中文：检索最终返回的相关记忆条数上限，仍必须按用户、状态与作用域过滤。
    # English: Maximum returned memories after user, active-status, and scope filtering.
    memory_top_k: int = Field(default=8, ge=1, validation_alias="MEMORY_TOP_K")
    # 中文：画像与记忆共同使用的个性化上下文 token 预算，不是模型总上下文长度。
    # English: Shared profile-and-memory token budget, not the model's entire context window.
    memory_context_token_budget: int = Field(
        default=1200, ge=1, validation_alias="MEMORY_CONTEXT_TOKEN_BUDGET"
    )


class DecisionSettings(_ProjectSettings):
    """Model-driven decision layer settings. / 决策模型驱动的决策层配置。

    The decision model uses an OpenAI-compatible endpoint. Endpoint/model/key may be given
    as ``DECISION_*`` or as the existing ``DASHSCOPE__*`` variables so the same local
    configuration can back the decision model. ``mode=off`` disables model calls and keeps
    the pre-decision behavior exactly.
    / 决策模型使用 OpenAI 兼容端点，端点/模型/密钥可用 ``DECISION_*`` 或现有
    ``DASHSCOPE__*`` 变量；``mode=off`` 关闭模型调用并保持引入决策层前的行为。
    """

    model_config = SettingsConfigDict(env_prefix="DECISION_")

    # 中文：off 关闭决策模型（默认），shadow 只计算并记录，enforce 按计划执行路由。
    # English: off disables the model (default), shadow records only, enforce applies the route.
    mode: Literal["off", "shadow", "enforce"] = "off"
    # 中文：决策模型 ID；空值时回退规则，不会隐式使用主聊天模型。
    # English: Decision model id; empty means rules fallback, never the main chat model.
    model: str | None = Field(
        default=None,
        max_length=256,
        validation_alias=AliasChoices("DECISION_MODEL", "DASHSCOPE__MODEL"),
    )
    # 中文：决策模型密钥；只用于该端点，不参与聊天模型供应商选择。
    # English: Decision endpoint secret; never used for the chat provider selection.
    api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("DECISION_API_KEY", "DASHSCOPE__API_KEY"),
    )
    # 中文：决策模型 OpenAI 兼容根地址（含 /v1，不含 /chat/completions）。
    # English: OpenAI-compatible base URL including /v1, without /chat/completions.
    base_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias=AliasChoices("DECISION_BASE_URL", "DASHSCOPE__MODEL_URL"),
    )
    # 中文：单次决策调用超时秒数；超时后回退规则决策。
    # English: Per-decision timeout in seconds; timeout falls back to rules.
    timeout_seconds: float = Field(default=2.0, gt=0, le=30, allow_inf_nan=False)
    # 中文：决策模型输出 token 上限，保持计划 JSON 简短。
    # English: Decision output token cap, keeping the plan JSON compact.
    max_tokens: int = Field(default=1024, ge=128, le=8192)
    # 中文：引用上下文预算上限；模型输出超过时由护栏收敛。
    # English: Reference-context budget ceiling; guards clamp larger proposals.
    max_reference_budget: int = Field(default=12000, ge=0, le=100000)


class MemorySettings(_ProjectSettings, MemoryConfiguration):
    """Planner defaults overridden by persisted settings-API config. / 规划器默认值。"""

    model_config = SettingsConfigDict(env_prefix="MEMORY_", extra="ignore")

    @field_validator(
        "batch_size",
        "idle_seconds",
        "consolidate_every",
        "forget_interval_hours",
        "active_limit",
        "observed_min_threads",
        "episodic_ttl_days",
        "fact_ttl_days",
        "retention_days",
        "daily_token_budget",
        mode="before",
    )
    @classmethod
    def parse_environment_integer(cls, value: Any) -> Any:
        """Convert decimal environment strings to integers. / 环境字符串转整数。

        Args:
            value: Raw field value from any source. / 原始字段值。

        Returns:
            An int for decimal strings, else the value. / 十进制串返回整数，其余原样。
        """
        # Environment values are strings; the public JSON contract stays strict.
        # 中文：环境值是字符串，公开 JSON 契约保持严格。
        if isinstance(value, str) and value.strip().isdecimal():
            return int(value)
        return value


class PromptSettings(_ProjectSettings):
    """Prompt layout and local cache limits, independent of models. / 提示词布局与缓存限制。"""

    model_config = SettingsConfigDict(env_prefix="PROMPT_", extra="ignore", populate_by_name=True)
    version: str = Field(default="2026-11-09.context-v1", min_length=1, max_length=80)
    layout: Literal["layered", "legacy"] = "layered"
    layered_l3: bool = True
    # Operational snapshots are checkpoint-only and separate from memory budgets.
    # 中文：运行快照只存检查点并限制容量，不改变长期记忆预算。
    runtime_context_max_bytes: int = Field(default=32768, ge=512, le=1000000)
    runtime_context_max_snapshots: int = Field(default=8, ge=1, le=64)
    cache_breakpoints: Literal["anthropic:auto", "auto", "off"] = "anthropic:auto"
    history_soft_limit: int = Field(
        default=20,
        ge=2,
        le=200,
        validation_alias=AliasChoices("HISTORY_SOFT_LIMIT", "history_soft_limit"),
    )
    history_hard_limit: int = Field(
        default=24,
        ge=3,
        le=240,
        validation_alias=AliasChoices("HISTORY_HARD_LIMIT", "history_hard_limit"),
    )
    history_compression_batch: int = Field(
        default=8,
        ge=1,
        le=200,
        validation_alias=AliasChoices("HISTORY_COMPRESSION_BATCH", "history_compression_batch"),
    )
    history_summary_model: str = Field(
        default="",
        max_length=200,
        validation_alias=AliasChoices("HISTORY_SUMMARY_MODEL", "history_summary_model"),
    )
    retrieval_cache_ttl_seconds: int = Field(
        default=600,
        ge=0,
        le=86400,
        validation_alias=AliasChoices("RETRIEVAL_CACHE_TTL_SECONDS", "retrieval_cache_ttl_seconds"),
    )
    retrieval_cache_max_entries: int = Field(
        default=500,
        ge=1,
        le=10000,
        validation_alias=AliasChoices("RETRIEVAL_CACHE_MAX_ENTRIES", "retrieval_cache_max_entries"),
    )

    @model_validator(mode="after")
    def validate_history(self) -> Self:
        """Reject inconsistent history limits. / 拒绝不一致的历史限制。

        Raises:
            ValueError: If limits or batch size are inconsistent. / 限制不一致时抛出。
        """
        if self.history_soft_limit >= self.history_hard_limit:
            raise ValueError("HISTORY_SOFT_LIMIT must be below HISTORY_HARD_LIMIT")
        if self.history_compression_batch > self.history_hard_limit - 1:
            raise ValueError("HISTORY_COMPRESSION_BATCH must retain at least one complete turn")
        return self


class ReferenceSettings(_ProjectSettings):
    """Budget for explicit composer reference context. / 输入区显式引用预算。"""

    model_config = SettingsConfigDict(env_prefix="REFERENCES_")
    context_chars: int = Field(default=12000, ge=2000, le=64000)


class CheckpointSettings(_ProjectSettings):
    """SQLite checkpoint configuration independent of Graph. / SQLite 快照配置。"""

    model_config = SettingsConfigDict(env_prefix="CHECKPOINT_")

    # 中文：SQLite 文件路径；相对路径固定相对 core/，不是启动工作目录。
    # English: SQLite file path; relative paths resolve against core/, not the working directory.
    sqlite_path: Path = Path("data/checkpoints.sqlite")
    # 中文：SQLite 写锁等待毫秒数，不是模型或整个运行的超时。
    # English: SQLite lock-wait timeout in milliseconds, separate from model/run timeouts.
    busy_timeout_ms: int = Field(default=5000, ge=1, le=60000)

    @field_validator("sqlite_path", mode="before")
    @classmethod
    def validate_sqlite_path(cls, value: Any) -> Path:
        """Validate and resolve a durable filesystem path. / 校验并解析持久化路径。

        Args:
            value: Raw configured path. / 原始路径配置。

        Returns:
            Absolute filesystem path. / 绝对路径。

        Raises:
            ValueError: If the path is a memory URI or directory. / 内存 URI 或目录时抛出。
        """
        if isinstance(value, str) and (
            not value.strip() or value == ":memory:" or value.startswith(("sqlite:", "file:"))
        ):
            raise ValueError("CHECKPOINT_SQLITE_PATH must be a filesystem file path")
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else _BACKEND_DIR / path).resolve()
        if path.is_dir():
            raise ValueError("CHECKPOINT_SQLITE_PATH must not be a directory")
        return path


class ToolSettings(_ProjectSettings):
    """Built-in tool workspace and resource limits. / 内置工具限制。"""

    model_config = SettingsConfigDict(env_prefix="TOOLS_")

    # 中文：工具输入与产物的根目录，相对路径固定相对 core/。
    # English: Root for tool inputs/artifacts; relative paths resolve against core/.
    workspace_path: Path = Path("data/artifacts")
    # Optional artifact directory; None keeps workspace_path. / 可选产物目录，None 沿用工作区。
    artifacts_path: Path | None = None
    # 中文：外部只读命令的最长执行秒数。
    # English: Maximum execution time for external read-only commands.
    command_timeout_seconds: float = Field(default=10, gt=0, le=60, allow_inf_nan=False)
    # 中文：每次文档 Python 子进程的最长执行秒数。
    # English: Maximum execution time for each document Python process.
    python_timeout_seconds: float = Field(default=30, gt=0, le=120, allow_inf_nan=False)
    # 中文：返回给模型的输出字符上限，超限会终止外部进程。
    # English: Returned output character cap; excessive process output terminates the process.
    max_output_chars: int = Field(default=16000, ge=256, le=100000)
    # 中文：允许提交的 Python 源码字符上限。
    # English: Maximum accepted Python source length.
    max_code_chars: int = Field(default=20000, ge=1, le=100000)
    # 中文：单次 Python 产物总字节数上限，完成后检查，不是 OS 磁盘配额。
    # English: Post-execution total artifact byte cap, not an operating-system disk quota.
    max_artifact_bytes: int = Field(default=10485760, ge=1, le=104857600)
    # 中文：单次 Python 返回的产物文件数量上限。
    # English: Maximum number of returned files from one Python run.
    max_artifact_files: int = Field(default=20, ge=1, le=100)
    # Feature gates for the layered tool suite. / 分层工具集的功能开关。
    workspace_files_enabled: bool = True
    export_document_enabled: bool = True
    get_datetime_enabled: bool = True
    run_python_enabled: bool = True
    # Web fetch defaults off and is domain-limited. / 网页抓取默认关闭且限域名。
    web_fetch_enabled: bool = False
    web_fetch_allowed_domains: list[str] = Field(default_factory=list)
    web_fetch_max_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    # Per-run resource budget shared by all tool calls. / 所有工具调用共享的单次运行预算。
    max_calls_per_run: int = Field(default=12, ge=1, le=100)
    max_total_output_chars: int = Field(default=64000, ge=256, le=1_000_000)
    max_total_duration_seconds: float = Field(default=120, gt=0, le=600, allow_inf_nan=False)
    failure_streak_limit: int = Field(default=3, ge=1, le=20)
    max_workspace_read_bytes: int = Field(default=1_048_576, ge=1024, le=100_000_000)
    max_workspace_search_bytes: int = Field(default=10_485_760, ge=1024, le=100_000_000)
    max_artifact_runs: int = Field(default=100, ge=1, le=10000)

    @field_validator("workspace_path", mode="before")
    @classmethod
    def resolve_workspace(cls, value: Any) -> Path:
        """Resolve the workspace directory and reject existing files. / 解析工作区目录。

        Args:
            value: Raw configured workspace path. / 原始工作区路径。

        Returns:
            Absolute workspace directory. / 绝对目录路径。

        Raises:
            ValueError: If blank or pointing at a file. / 为空或指向文件时抛出。
        """
        if isinstance(value, str) and not value.strip():
            raise ValueError("TOOLS_WORKSPACE_PATH must not be blank")
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else _BACKEND_DIR / path).resolve()
        if path.is_file():
            raise ValueError("TOOLS_WORKSPACE_PATH must be a directory")
        return path
