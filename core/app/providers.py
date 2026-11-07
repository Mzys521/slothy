"""User-owned model connections; public models hide credentials. / 用户模型连接配置。"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from langchain_anthropic import ChatAnthropic
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.config import LLMSettings
from app.infrastructure.llm.catalog import EFFORTS, VENDOR_MODELS, ModelItem
from app.infrastructure.llm.openai_compatible import OpenAICompatibleProvider

Protocol = Literal["openai", "anthropic"]
Identifier = str


class ProviderTemplate(BaseModel):
    """One-click setup preset for a provider. / 服务商的一键创建预设模板。"""

    id: str
    name: str
    base_url: str
    protocol: Protocol = "openai"
    model_ids: list[str] = Field(default_factory=list)


# Built-in presets only; user records live in providers.json. / 仅内置预设，用户记录另存。
TEMPLATES = [
    ProviderTemplate(
        id="deepseek",
        name="DeepSeek",
        base_url="https://api.deepseek.com",
        model_ids=list(VENDOR_MODELS["deepseek"]),
    ),
    ProviderTemplate(
        id="mimo",
        name="xiaomi",
        base_url="https://api.xiaomimimo.com/v1",
        model_ids=list(VENDOR_MODELS["mimo"]),
    ),
    ProviderTemplate(id="openai", name="OpenAI", base_url="https://api.openai.com/v1"),
    ProviderTemplate(
        id="anthropic", name="Anthropic", base_url="https://api.anthropic.com", protocol="anthropic"
    ),
    ProviderTemplate(id="kimi", name="Kimi", base_url="https://api.moonshot.cn/v1"),
    ProviderTemplate(
        id="qwen", name="Qwen", base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    ),
]


class ProviderModel(BaseModel):
    """A model entry declared under one provider. / 单个服务商下声明的模型条目。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128, pattern=r"^[^\s\x00-\x1f]+$")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)


class ProviderInput(BaseModel):
    """Validated, persisted provider connection configuration. / 服务商连接配置。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,47}$")
    name: str = Field(min_length=1, max_length=80)
    template: str | None = None
    protocol: Protocol = "openai"
    base_url: AnyHttpUrl
    api_key: SecretStr | None = Field(default=None, repr=False, exclude=True)
    models: list[ProviderModel] = Field(default_factory=list, max_length=200)
    enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        """Reject userinfo, query or fragment in the base URL. / 拒绝非法根地址。

        Args:
            value: Parsed provider base URL. / 解析后的服务商根地址。

        Returns:
            The unchanged URL when valid. / 校验通过时原样返回。

        Raises:
            ValueError: If the URL has userinfo, query or fragment. / URL 含用户信息等。
        """
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("Use an API base URL without credentials, query or fragment.")
        return value

    @model_validator(mode="after")
    def validate_template(self):
        """Reject unknown templates, duplicate model IDs and blank keys. / 拒绝非法模板。

        Raises:
            ValueError: If the template is unknown or model IDs repeat. / 模板未知或 ID 重复。
        """
        if self.template and self.template not in {item.id for item in TEMPLATES}:
            raise ValueError("Unknown provider template.")
        if len({item.id for item in self.models}) != len(self.models):
            raise ValueError("Model IDs must be unique within a provider.")
        if self.api_key is not None and not self.api_key.get_secret_value().strip():
            self.api_key = None
        return self


class ProviderPublic(BaseModel):
    """Credential-free provider view returned by the API. / API 返回的不含凭据的服务商视图。"""

    id: str
    name: str
    template: str | None
    protocol: Protocol
    base_url: str
    models: list[ProviderModel]
    enabled: bool
    has_api_key: bool


class ProviderList(BaseModel):
    """Provider list payload with presets and setup flag. / 含预设模板与初始化标记的服务商列表。"""

    items: list[ProviderPublic]
    templates: list[ProviderTemplate]
    setup_required: bool


class DiscoveredModels(BaseModel):
    """Models fetched from a provider's model endpoint. / 从服务商模型端点拉取到的模型。"""

    items: list[ProviderModel]


def protect_secret(value: str, *, decrypt: bool = False) -> str:
    """Encrypt or decrypt a provider secret with OS facilities. / 使用操作系统设施加解密服务商密钥。

    Windows DPAPI is tied to the current OS account; no hard-coded encryption key. /
    Windows DPAPI 与当前操作系统账户绑定，不使用硬编码密钥。

    Args:
        value: Protected base64 text, or plaintext when encrypting. / 加密时为明文，解密时为密文。
        decrypt: False encrypts a key; True restores plaintext. / False 加密，True 还原明文。

    Returns:
        Base64 text when encrypting, plaintext when decrypting. / 加密返回 base64 文本。

    Raises:
        OSError: If Windows DPAPI cannot access the saved credential. / DPAPI 不可用时抛出。
    """
    if sys.platform != "win32":
        # Development on other systems relies on file permissions, never bundled.
        # 中文：其他系统的开发环境依赖文件权限保护，从不打包分发。
        return (
            base64.b64decode(value).decode()
            if decrypt
            else base64.b64encode(value.encode()).decode()
        )

    from ctypes import wintypes

    class Blob(ctypes.Structure):
        """ctypes view of a Win32 DATA_BLOB. / Win32 DATA_BLOB 的 ctypes 视图。"""

        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    data = base64.b64decode(value) if decrypt else value.encode("utf-8")
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, target = Blob(len(data), buffer), Blob()
    function = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("Windows could not access the saved API credential.")
    try:
        output = ctypes.string_at(target.pbData, target.cbData)
        return output.decode("utf-8") if decrypt else base64.b64encode(output).decode()
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.pbData)


@dataclass(frozen=True)
class ProviderConnection:
    """One resolved provider/model pair with its secret. / 已解析的服务商与模型组合及其密钥。"""

    settings: ProviderInput
    key: SecretStr
    model: str

    def build_provider(self, effort: str | None):
        """Build the chat model adapter for this connection. / 为该连接构建聊天模型适配器。

        Args:
            effort: Reasoning effort level, or None for the default. / 推理强度级别，None 表示默认。

        Returns:
            Anthropic or OpenAI-compatible provider adapter. / Anthropic 或 OpenAI 兼容适配器。
        """
        if self.settings.protocol == "anthropic":
            model = ChatAnthropic(
                model_name=self.model,
                api_key=self.key.get_secret_value(),
                base_url=str(self.settings.base_url).rstrip("/"),
                max_tokens=8192,
                timeout=60,
                max_retries=2,
            )

            class Provider:
                """Minimal adapter around one ChatAnthropic instance. / ChatAnthropic 适配器。"""

                prompt_cache_protocol = "anthropic"

                def get_chat_model(self):
                    """Return the shared ChatAnthropic client. / 返回共享客户端。"""
                    return model

            return Provider()
        vendor = self.settings.template
        llm = LLMSettings(
            _env_file=None,
            provider=vendor if vendor in ("deepseek", "mimo") else "openai_compatible",
            vendor_credentials_only=True,
            model=self.model,
            api_key=self.key,
            base_url=self.settings.base_url,
            extra_body={},
            stream_usage=True,
        )
        return OpenAICompatibleProvider(llm, effort=effort)


class ProviderStore:
    """JSON-backed provider records with encrypted secrets. / 加密存储的服务商记录。"""

    def __init__(self, path: Path):
        """Load version-1 provider records from a JSON file. / 从 JSON 文件加载版本 1 的服务商记录。

        Args:
            path: providers.json location. / providers.json 路径。

        Raises:
            ValueError: If the document version is not 1. / 文档版本不是 1 时抛出。
        """
        self.path = path
        self.lock = asyncio.Lock()
        self.records: dict[str, dict] = {}
        if path.exists():
            document = json.loads(path.read_text(encoding="utf-8"))
            if document.get("version") != 1:
                raise ValueError("The provider configuration version is not supported.")
            for record in document["providers"]:
                validated = ProviderInput.model_validate(record["config"])
                self.records[validated.id] = {"config": validated, "secret": record["secret"]}

    def public(self, provider_id: str) -> ProviderPublic:
        """Return the credential-free view of one provider. / 返回单个服务商的不含凭据视图。

        Args:
            provider_id: Provider record ID. / 服务商记录 ID。

        Returns:
            Public provider payload including has_api_key. / 含 has_api_key 的公开服务商数据。
        """
        record = self.records[provider_id]
        return ProviderPublic(
            **record["config"].model_dump(mode="json"), has_api_key=bool(record["secret"])
        )

    def list(self) -> list[ProviderPublic]:
        """Return public views of all stored providers. / 返回所有已存服务商的公开视图。"""
        return [self.public(provider_id) for provider_id in self.records]

    def _write(self, records: dict[str, dict]):
        """Atomically replace providers.json with the given records. / 原子替换 providers.json。

        Args:
            records: Provider ID to config-and-secret mapping. / 服务商 ID 到配置与密钥的映射。
        """
        document = {
            "version": 1,
            "providers": [
                {"config": record["config"].model_dump(mode="json"), "secret": record["secret"]}
                for record in records.values()
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.path)
        self.records = records

    def save(self, body: ProviderInput):
        """Insert or replace a provider, reusing its stored secret. / 新增或替换服务商。

        Args:
            body: Validated provider input. / 已校验的服务商输入。

        Raises:
            ValueError: If no existing or supplied API key is available. / 没有可用 API 密钥时抛出。
        """
        previous = self.records.get(body.id)
        secret = (
            protect_secret(body.api_key.get_secret_value().strip())
            if body.api_key
            else previous["secret"]
            if previous
            else None
        )
        if not secret:
            raise ValueError("Enter an API key before saving this provider.")
        clean = ProviderInput.model_validate(body.model_dump(mode="json"))
        self._write({**self.records, body.id: {"config": clean, "secret": secret}})

    def delete(self, provider_id: str):
        """Remove one provider record and rewrite the file. / 删除单个服务商记录并重写文件。

        Args:
            provider_id: Provider record ID. / 服务商记录 ID。
        """
        self._write({key: value for key, value in self.records.items() if key != provider_id})

    def catalog(self) -> dict[str, tuple[ModelItem, ProviderConnection]]:
        """Flatten enabled providers into "<provider>/<model>" entries. / 展开为模型条目。

        Returns:
            Model ID to public item and connection mapping. / 模型 ID 到条目与连接的映射。
        """
        catalog = {}
        for provider_id, record in self.records.items():
            config = record["config"]
            if not config.enabled:
                continue
            key = SecretStr(protect_secret(record["secret"], decrypt=True))
            for model in config.models:
                known = model.id in VENDOR_MODELS.get(config.template, ())
                levels = EFFORTS[config.template] if known else ()
                identifier = provider_id + "/" + model.id
                catalog[identifier] = (
                    ModelItem(
                        id=identifier,
                        provider=config.template or provider_id,
                        display_name=model.name or model.id,
                        context_window=model.context_window or (1_048_576 if known else None),
                        reasoning_levels=levels,
                        default_effort="none" if levels else None,
                    ),
                    ProviderConnection(config, key, model.id),
                )
        return catalog

    async def discover(self, body: ProviderInput) -> list[ProviderModel]:
        """Fetch model IDs from the provider's model endpoint. / 从服务商的模型端点拉取模型 ID。

        Args:
            body: Provider input with URL, protocol and optional key. / 含地址与密钥。

        Returns:
            Up to 200 discovered models. / 最多 200 个发现的模型。

        Raises:
            ValueError: If no key is available or the provider rejects it. / 无密钥或被拒绝。
        """
        secret = body.api_key
        if secret is None:
            previous = self.records.get(body.id)
            if previous:
                secret = SecretStr(protect_secret(previous["secret"], decrypt=True))
        if not secret:
            raise ValueError("Enter an API key to fetch models.")
        key = secret.get_secret_value().strip()
        headers = (
            {"x-api-key": key, "anthropic-version": "2023-06-01"}
            if body.protocol == "anthropic"
            else {"Authorization": "Bearer " + key}
        )
        base = str(body.base_url).rstrip("/")
        suffix = "/models" if body.protocol == "openai" or base.endswith("/v1") else "/v1/models"
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
            response = await client.get(base + suffix, headers=headers)
            if response.status_code in (401, 403):
                raise ValueError("The provider rejected this API key (401/403).")
            response.raise_for_status()
            data = response.json().get("data", [])
        result = {}
        for row in data[:200]:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                model = ProviderModel(id=row["id"], name=row.get("display_name") or row["id"])
                result[model.id] = model
        return list(result.values())
