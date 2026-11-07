"""Pre-P0 public contracts and unified tool assembly, with no model/network calls."""

import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from app.agent.prompting.plan import digest
from app.agent.prompts.tools import tool_instructions
from app.agent.tools import Platform, Risk, ToolDescriptor, ToolRegistry, create_builtin_tools
from app.agent.tools.attachments import create_attachment_tools
from app.config import CheckpointSettings, ToolSettings
from app.connectors.registry import ConnectorRegistry
from tests.support.legacy_app import create_app


def tool(name="example"):
    return StructuredTool.from_function(lambda text: text, name=name, description="Return text.")


@pytest.mark.parametrize("case", ["default", "configured"])
def test_versioned_final_names_prompt_bytes_and_model_schemas_are_stable(tmp_path, case):
    baseline = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "tool_layer_v2.json").read_text(encoding="utf-8")
    )[case]
    settings = ToolSettings(
        _env_file=None,
        workspace_path=tmp_path,
        **(
            {"max_code_chars": 1234, "python_timeout_seconds": 7, "max_output_chars": 2048}
            if case == "configured"
            else {}
        ),
    )
    tools = [*create_builtin_tools(settings), *create_attachment_tools(SimpleNamespace())]
    registry = ToolRegistry.from_tools(tools, settings=settings)
    for mode in ("chat", "work"):
        visible = registry.for_mode(mode)
        assert registry.names(mode) == baseline[mode]["names"]
        assert registry.instructions(mode).encode() == baseline[mode]["instructions"].encode()
        assert (
            digest(
                [
                    convert_to_openai_tool(item)
                    for item in sorted(visible, key=lambda item: item.name)
                ]
            )
            == baseline[mode]["schema_sha256"]
        )
        assert tool_instructions(
            [item.name for item in visible] * 2, registry
        ) == registry.instructions(mode)
        assert (
            ToolRegistry.from_tools(reversed(tools)).instructions(mode)
            == (baseline[mode]["instructions"])
        )
        assert all(
            registry.descriptor(item.name).args_schema is item.args_schema for item in visible
        )
    assert registry.descriptor("run_command").timeout_seconds == settings.command_timeout_seconds
    assert registry.descriptor("write_document_python").timeout_seconds == (
        settings.python_timeout_seconds
    )
    assert registry.descriptor("write_document_python").journaled
    assert tool_instructions([]) == ""
    assert ToolRegistry().instructions("chat") == ""


@pytest.mark.parametrize("risk", list(Risk))
def test_descriptor_approval_defaults_and_explicit_override(risk):
    descriptor = ToolDescriptor.from_tool(tool(), risk=risk)
    assert descriptor.requires_approval == (risk != Risk.READ)
    assert ToolDescriptor.from_tool(tool(), risk=risk, requires_approval=True).requires_approval
    with pytest.raises(ValidationError):
        descriptor.name = "changed"
    with pytest.raises(ValidationError):
        ToolDescriptor.from_tool(tool(), risk=risk, timeout_seconds=0)


def test_registry_selection_preserves_order_and_retains_disabled_descriptors():
    registry = ToolRegistry()
    for name, metadata in [
        ("z_read", {"risk": Risk.READ, "mode": {"chat", "work"}}),
        ("a_write", {"risk": Risk.WRITE}),
        ("server", {"risk": Risk.READ, "platforms": {Platform.SERVER}}),
        ("external", {"risk": Risk.EXECUTE, "source": "connector", "mode": {"chat", "work"}}),
    ]:
        item = tool(name)
        registry.register(ToolDescriptor.from_tool(item, **metadata), item)
    assert registry.names("work") == ["z_read", "a_write", "external"]
    assert registry.names("chat") == ["z_read", "external"]
    assert [item.name for item in registry.descriptors("work")] == ["a_write", "external", "z_read"]
    registry.rebuild(disabled_builtin={"z_read", "external"})
    assert registry.names("chat") == ["external"]
    assert registry.descriptor("z_read") is not None
    assert registry.descriptor("missing") is None
    registry.rebuild(platform=Platform.SERVER)
    assert registry.names("work") == ["server"]
    registry.rebuild()
    assert registry.names("chat") == ["z_read", "external"]


def test_unknown_injected_tools_stay_work_only_and_duplicate_imports_are_atomic():
    existing, additional = tool("custom"), tool("additional")
    registry = ToolRegistry.from_tools([existing])
    assert registry.names("chat") == []
    assert registry.names("work") == ["custom"]
    assert registry.descriptor("custom").requires_approval
    with pytest.raises(ValueError, match="unique"):
        registry.register(ToolDescriptor.from_tool(existing, risk=Risk.READ), existing)
    with pytest.raises(ValueError, match="match"):
        registry.register(ToolDescriptor.from_tool(additional, risk=Risk.READ), existing)
    adapter = SimpleNamespace(
        tool_entries=lambda directory: [
            (ToolDescriptor.from_tool(additional, risk=Risk.EXECUTE), additional),
            (ToolDescriptor.from_tool(existing, risk=Risk.EXECUTE), existing),
        ]
    )
    with pytest.raises(ValueError, match="unique"):
        registry.register_connector(adapter)
    assert registry.names("work") == ["custom"]


def test_legacy_disable_flags_cover_replacements_and_hidden_aliases_are_recovery_only(tmp_path):
    registry = ToolRegistry.from_tools(
        create_builtin_tools(ToolSettings(_env_file=None, workspace_path=tmp_path))
    )
    assert registry.unavailable("run_command") == "UNAVAILABLE"
    assert registry.unavailable("run_command", legacy=True) is None
    registry.rebuild(disabled_builtin={"run_command", "write_document_python"})
    assert "workspace_read" not in registry.names("chat")
    assert "workspace_write" not in registry.names("work")
    assert "run_python" not in registry.names("work")
    registry.rebuild(disabled_builtin={"workspace_write"})
    assert registry.unavailable("run_command", legacy=True, arguments={"command": "cat"}) is None
    assert (
        registry.unavailable("run_command", legacy=True, arguments={"command": "mkdir"})
        == "UNAVAILABLE"
    )


async def test_connector_adapter_preserves_modes_approval_schemas_and_empty_guidance(
    tmp_path, monkeypatch
):
    adapter = ConnectorRegistry(tmp_path / "connectors", [])
    (local,) = await adapter.import_file(
        "local",
        "local.json",
        json.dumps(
            {
                "name": "echo",
                "description": "Echo text",
                "command": sys.executable,
                "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            }
        ).encode(),
    )
    await adapter.toggle(local["id"], True)

    async def discover(config):
        return [{"name": "read", "description": "Read text", "input_schema": {"type": "object"}}]

    monkeypatch.setattr("app.connectors.registry.discover", discover)
    connections = await adapter.import_file(
        "mcp",
        "servers.json",
        json.dumps(
            {
                "mcpServers": {
                    "stdio": {"command": sys.executable},
                    "http": {"url": "https://example.com/mcp"},
                    "sse": {"url": "https://example.com/sse", "type": "sse"},
                }
            }
        ).encode(),
    )
    for item in connections:
        await adapter.toggle(item["id"], True)

    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: example\n---\nReference guidance.")
    (skill,) = await adapter.import_file("skill", "example.zip", bundle.getvalue())
    await adapter.toggle(skill["id"], True)

    registry = ToolRegistry()
    registry.register_connector(adapter, str(tmp_path))
    assert registry.names("chat") == ["read_skill_file"]
    assert len(registry.names("work")) == 5
    assert [convert_to_openai_tool(item) for item in registry.for_mode("work")] == [
        convert_to_openai_tool(item) for item in adapter.tools(str(tmp_path))
    ]
    assert registry.instructions("chat") == tool_instructions(registry.names("chat"), registry)
    assert all(item.instruction for item in registry.descriptors("chat"))
    assert registry.descriptor(local["tools"][0]).risk == Risk.EXECUTE
    for connection in connections:
        name = adapter.public(connection["id"], adapter.records[connection["id"]])["tools"][0]
        assert registry.descriptor(name).risk == (
            Risk.EXECUTE if connection["transport"] == "stdio" else Risk.NETWORK
        )
    assert registry.descriptor("read_skill_file").risk == Risk.READ
    assert not registry.descriptor("read_skill_file").requires_approval
    await adapter.toggle(local["id"], False)
    await adapter.toggle(skill["id"], False)
    refreshed = ToolRegistry()
    refreshed.register_connector(adapter)
    assert local["tools"][0] not in refreshed.names("work")
    assert "read_skill_file" not in refreshed.names("chat")


def test_api_runtime_and_registry_refresh_together_after_builtin_disable(tmp_path):
    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = Model(responses=[AIMessage(content="Ready")])
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        work_tools=create_builtin_tools(
            ToolSettings(workspace_path=tmp_path / "files", python_timeout_seconds=7)
        ),
    )
    with TestClient(app) as client:
        for disabled in (False, True):
            if disabled:
                assert (
                    client.patch(
                        "/api/v1/connectors/builtin-calculate", json={"enabled": False}
                    ).status_code
                    == 200
                )
            runtime = client.get("/api/v1/runtime").json()
            registry = app.state.tool_registry
            for mode in ("chat", "work"):
                assert runtime[mode + "_tools"] == registry.names(mode)
                assert ("calculate" in runtime[mode + "_tools"]) is not disabled
                assert registry.instructions(mode) == tool_instructions(
                    runtime[mode + "_tools"], registry
                )
            assert registry.descriptor("calculate") is not None
            # Injected executors may use their own settings; do not invent their limits.
            assert registry.descriptor("write_document_python").timeout_seconds == 7
