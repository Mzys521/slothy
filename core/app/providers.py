"""User-owned model connections. Public models never contain credentials."""

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
    id: str
    name: str
    base_url: str
    protocol: Protocol = "openai"
    model_ids: list[str] = Field(default_factory=list)


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
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=128, pattern=r"^[^\s\x00-\x1f]+$")
    name: str | None = Field(default=None, min_length=1, max_length=128)
    context_window: int | None = Field(default=None, ge=1024, le=10_000_000)


class ProviderInput(BaseModel):
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
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("Use an API base URL without credentials, query or fragment.")
        return value

    @model_validator(mode="after")
    def validate_template(self):
        if self.template and self.template not in {item.id for item in TEMPLATES}:
            raise ValueError("Unknown provider template.")
        if len({item.id for item in self.models}) != len(self.models):
            raise ValueError("Model IDs must be unique within a provider.")
        if self.api_key is not None and not self.api_key.get_secret_value().strip():
            self.api_key = None
        return self


class ProviderPublic(BaseModel):
    id: str
    name: str
    template: str | None
    protocol: Protocol
    base_url: str
    models: list[ProviderModel]
    enabled: bool
    has_api_key: bool


class ProviderList(BaseModel):
    items: list[ProviderPublic]
    templates: list[ProviderTemplate]
    setup_required: bool


class DiscoveredModels(BaseModel):
    items: list[ProviderModel]


def protect_secret(value: str, *, decrypt: bool = False) -> str:
    """Windows DPAPI is tied to the current OS account; no hard-coded encryption key."""
    if sys.platform != "win32":
        # Development on other systems is protected with file permissions, never bundled.
        return (
            base64.b64decode(value).decode()
            if decrypt
            else base64.b64encode(value.encode()).decode()
        )

    from ctypes import wintypes

    class Blob(ctypes.Structure):
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
    settings: ProviderInput
    key: SecretStr
    model: str

    def build_provider(self, effort: str | None):
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
                prompt_cache_protocol = "anthropic"

                def get_chat_model(self):
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
    def __init__(self, path: Path):
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
        record = self.records[provider_id]
        return ProviderPublic(
            **record["config"].model_dump(mode="json"), has_api_key=bool(record["secret"])
        )

    def list(self) -> list[ProviderPublic]:
        return [self.public(provider_id) for provider_id in self.records]

    def _write(self, records: dict[str, dict]):
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
        self._write({key: value for key, value in self.records.items() if key != provider_id})

    def catalog(self) -> dict[str, tuple[ModelItem, ProviderConnection]]:
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
