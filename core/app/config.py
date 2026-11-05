"""模型与运行参数配置。 / Validated model and agent runtime settings."""

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
    """共用加载规则；禁止输出输入中的密钥。 / Shared loading with hidden error inputs."""

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
    """读取 LLM_* 与所选服务的变量。 / Load LLM_* and the selected service's variables.

    中文：显式参数优先，其次 LLM_*，最后所选服务的命名变量；各变量内环境优先于文件。
    English: Explicit arguments win over LLM_* overrides and then selected-service variables;
    process environment wins over dotenv files within each variable name.
    """

    model_config = SettingsConfigDict(env_prefix="LLM_")

    # 中文：服务选择器；决定读取哪组密钥、模型和地址。
    # English: Selects the vendor's credential, model, and endpoint namespace.
    provider: ProviderName = "openai_compatible"
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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource | Callable[[], dict[str, Any]], ...]:
        """添加服务变量作为最后一级回退。 / Add selected-vendor variables as the last fallback."""

        def source_value(source: PydanticBaseSettingsSource, name: str) -> Any:
            variables = getattr(source, "env_vars", {})
            return variables.get(name if source.case_sensitive else name.lower())

        def selected_vendor_source() -> dict[str, Any]:
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
        value = value.strip()
        if not value:
            raise ValueError("LLM_MODEL must not be blank")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value().strip()
        if not key:
            raise ValueError("LLM_API_KEY must not be blank")
        return SecretStr(key)

    @model_validator(mode="after")
    def validate_endpoint(self) -> Self:
        if self.base_url is None and self.provider == "openai_compatible":
            raise ValueError("LLM_BASE_URL is required for openai_compatible")
        return self

    @field_validator("extra_body")
    @classmethod
    def validate_extra_body(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
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
        """解析服务地址，不跨服务借用密钥。 / Resolve the URL without cross-vendor credentials."""
        if self.base_url is not None:
            return str(self.base_url).rstrip("/")
        return _DEFAULT_BASE_URLS[self.provider]


class AgentSettings(_ProjectSettings):
    """Graph 与个性化的部署默认值。 / Deployment defaults for graph and personalization."""

    model_config = SettingsConfigDict(env_prefix="AGENT_")

    # 中文：单次 Graph 的最大 super-step 数；包括读取、构建、模型和工具节点。
    # English: Maximum graph super-steps per run, including reads, context, model, and tools.
    max_steps: int = Field(default=25, ge=1)
    # Legacy layout/library history cap. The layered HTTP runtime uses HISTORY_* batch limits.
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


class MemorySettings(_ProjectSettings, MemoryConfiguration):
    """Planner defaults; persisted UI configuration overrides these in the settings API."""

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
        # Environment values are strings; the public JSON contract remains strict.
        if isinstance(value, str) and value.strip().isdecimal():
            return int(value)
        return value


class PromptSettings(_ProjectSettings):
    """Prompt layout and transparent local cache limits; independent of model settings."""

    model_config = SettingsConfigDict(env_prefix="PROMPT_", extra="ignore", populate_by_name=True)
    version: str = Field(default="2026-11-05.1", min_length=1, max_length=80)
    layout: Literal["layered", "legacy"] = "layered"
    layered_l3: bool = True
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
        if self.history_soft_limit >= self.history_hard_limit:
            raise ValueError("HISTORY_SOFT_LIMIT must be below HISTORY_HARD_LIMIT")
        if self.history_compression_batch > self.history_hard_limit - 1:
            raise ValueError("HISTORY_COMPRESSION_BATCH must retain at least one complete turn")
        return self


class CheckpointSettings(_ProjectSettings):
    """SQLite 快照配置，不依赖 Graph。 / SQLite checkpoint configuration independent of Graph."""

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
        """校验持久化文件路径。 / Validate and resolve a durable filesystem path."""
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
    """内置工具的工作区与资源限制。 / Built-in tool workspace and resource limits."""

    model_config = SettingsConfigDict(env_prefix="TOOLS_")

    # 中文：工具输入与产物的根目录，相对路径固定相对 core/。
    # English: Root for tool inputs/artifacts; relative paths resolve against core/.
    workspace_path: Path = Path("data/artifacts")
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

    @field_validator("workspace_path", mode="before")
    @classmethod
    def resolve_workspace(cls, value: Any) -> Path:
        """解析工作区，拒绝现有普通文件。 / Resolve the workspace and reject existing files."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("TOOLS_WORKSPACE_PATH must not be blank")
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else _BACKEND_DIR / path).resolve()
        if path.is_file():
            raise ValueError("TOOLS_WORKSPACE_PATH must be a directory")
        return path
