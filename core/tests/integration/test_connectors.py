import io
import json
import socket
import stat
import sys
import threading
import time
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import Field

from app.api import create_app
from app.config import CheckpointSettings
from app.connectors.registry import ConnectorRegistry, list_mcp_tools

BASE = "/api/v1"


class ToolModel(FakeMessagesListChatModel):
    seen: list[list] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    async def _agenerate(self, messages, **kwargs):
        self.seen.append(list(messages))
        return await super()._agenerate(messages, **kwargs)


def make_app(tmp_path, model=None):
    model = model or ToolModel(responses=[AIMessage(content="Ready")])
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        memory_processing="rules",
    )
    return app, model


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for path, text in files.items():
            bundle.writestr(path, text)
    return output.getvalue()


def upload(client, kind, data, filename="connector.json"):
    return client.post(
        BASE + "/connectors/import",
        params={"kind": kind, "filename": filename},
        content=data,
        headers={"content-type": "application/octet-stream"},
    )


def local_bundle():
    return archive(
        {
            "example/tool.json": json.dumps(
                {
                    "name": "local_echo",
                    "description": "Return the supplied text and working directory.",
                    "command": sys.executable,
                    "args": ["${plugin}/echo.py"],
                    "input_schema": {
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                }
            ),
            "example/echo.py": "import json,sys,os\nvalue=json.load(sys.stdin)\n"
            "print(json.dumps({'text':value['text'],'cwd':os.getcwd()},ensure_ascii=False))\n",
        }
    )


@pytest.mark.parametrize("mode", ["chat", "work"])
@pytest.mark.parametrize("disable_pending", [False, True])
def test_local_tools_require_review_and_disabled_pending_never_executes(
    tmp_path, mode, disable_pending
):
    app, model = make_app(tmp_path)
    with TestClient(app) as client:
        imported = upload(client, "local", local_bundle(), "echo.zip")
        assert imported.status_code == 201, imported.text
        (item,) = imported.json()["items"]
        assert not item["enabled"]
        enabled = client.patch(BASE + "/connectors/" + item["id"], json={"enabled": True})
        assert enabled.status_code == 200, enabled.text
        (name,) = enabled.json()["tools"]
        assert (name in client.get(BASE + "/runtime").json()[mode + "_tools"]) == (mode == "work")
        model.responses[:] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": name,
                        "args": {"text": "Isolated tool output 中文"},
                        "id": "local-call",
                    }
                ],
            ),
            AIMessage(content="Finished"),
        ]
        thread = client.post(BASE + "/threads", json={"mode": mode}).json()["thread_id"]
        response = client.post(BASE + "/chat", json={"thread_id": thread, "message": "Run tool"})
        if mode == "chat":
            assert response.status_code == 200, response.text
            activity = next(
                entry for entry in response.json()["activity"] if entry["kind"] == "tool"
            )
            assert activity["error_code"] == "UNAVAILABLE"
            assert "Isolated tool output" not in (activity["result"] or "")
            return
        assert response.status_code == 202, response.text
        review = response.json()
        assert review["calls"][0]["name"] == name
        if disable_pending:
            assert (
                client.patch(
                    BASE + "/connectors/" + item["id"], json={"enabled": False}
                ).status_code
                == 200
            )
        result = client.post(
            f"{BASE}/threads/{thread}/approval/{review['approval_id']}", json={"approved": True}
        )
        assert result.status_code == 200, result.text
        activity = next(entry for entry in result.json()["activity"] if entry["kind"] == "tool")
        if disable_pending:
            assert activity["status"] == "error"
            assert "Isolated tool output" not in (activity["result"] or "")
        else:
            assert activity["status"] == "done"
            assert "Isolated tool output" in activity["result"]
            assert "workspaces" in activity["result"] or "artifacts" in activity["result"]


def test_import_disable_restart_delete_and_contract_errors(tmp_path):
    app, _ = make_app(tmp_path)
    data = local_bundle()
    with TestClient(app) as client:
        item = upload(client, "local", data, "echo.zip").json()["items"][0]
        assert upload(client, "local", data, "echo.zip").status_code == 409
        assert (
            client.patch(BASE + "/connectors/" + item["id"], json={"enabled": True}).status_code
            == 200
        )
        assert (
            client.patch(BASE + "/connectors/" + item["id"], json={"enabled": "yes"}).status_code
            == 422
        )
        missing = client.patch(BASE + "/connectors/missing", json={"enabled": True})
        assert missing.status_code == 404
        assert set(missing.json()) == {"code", "message", "request_id"}
        assert upload(client, "skill", b"bad", "skill.zip").status_code == 422
        assert upload(client, "mcp", b"{}", "../bad.json").status_code == 422
        assert upload(client, "local", b"x" * (10 * 1024 * 1024 + 1)).status_code == 413
        schema = client.get("/openapi.json").json()
        assert "501" not in schema["paths"][BASE + "/connectors/import"]["post"]["responses"]
    app, _ = make_app(tmp_path)
    with TestClient(app) as client:
        rows = client.get(BASE + "/connectors").json()["items"]
        assert any(row["id"] == item["id"] and row["enabled"] for row in rows)
        assert client.delete(BASE + "/connectors/" + item["id"]).status_code == 204
        assert not client.get(BASE + "/connectors").json()["items"]
        assert not list((tmp_path / "connectors").glob("*/echo.py"))


def test_skills_load_reference_files_and_revoke_on_disable(tmp_path):
    app, model = make_app(tmp_path)
    data = archive(
        {
            "sample/SKILL.md": "---\nname: writing-style\n---\nUse the style guide.",
            "sample/references/style.txt": "Reference content",
        }
    )
    with TestClient(app) as client:
        item = upload(client, "skill", data, "writing.zip").json()["items"][0]
        key = item["id"]
        assert app.state.connectors.skill_references() == []
        assert client.patch(BASE + "/connectors/" + key, json={"enabled": True}).status_code == 200
        assert app.state.connectors.skill_references()[0]["name"] == "writing-style"
        model.responses[:] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_skill_file",
                        "args": {"skill_id": key, "path": "references/style.txt"},
                        "id": "skill",
                    }
                ],
            ),
            AIMessage(content="Read the guide"),
        ]
        review = client.post(
            BASE + "/chat", json={"message": "Use the skill", "approval_policy": "every"}
        ).json()
        assert "approval_id" in review
        result = client.post(
            f"{BASE}/threads/{review['thread_id']}/approval/" + review["approval_id"],
            json={"approved": True},
        )
        assert result.status_code == 200, result.text
        assert any(
            "Reference content" in (row.get("result") or "") for row in result.json()["activity"]
        )
        assert any(message.name == "skill_reference" for message in model.seen[0])
        with pytest.raises(Exception, match="relative path"):
            app.state.connectors.read_skill_file(key, "../../connectors.json", 0)
        assert client.patch(BASE + "/connectors/" + key, json={"enabled": False}).status_code == 200
        assert app.state.connectors.skill_references() == []
        model.responses[:] = [AIMessage(content="Without the skill")]
        model.i = 0
        assert (
            client.post(
                BASE + "/chat", json={"thread_id": review["thread_id"], "message": "Continue"}
            ).status_code
            == 200
        )
        assert not any(message.name == "skill_reference" for message in model.seen[-1])
        with pytest.raises(Exception, match="not enabled"):
            app.state.connectors.read_skill_file(key, "references/style.txt", 0)


@pytest.mark.parametrize(
    "path",
    [
        "../SKILL.md",
        "/SKILL.md",
        "C:/SKILL.md",
        "a/../../SKILL.md",
        "a\\..\\SKILL.md",
        "CON.txt",
        "a:stream",
        "file. ",
    ],
)
def test_zip_paths_cannot_escape_or_use_windows_special_names(tmp_path, path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as client:
        response = upload(client, "skill", archive({path: "text"}), "unsafe.zip")
        assert response.status_code == 422, response.text
        assert client.get(BASE + "/connectors").json()["items"] == []
        assert not (tmp_path / "SKILL.md").exists()


def test_symlinks_and_archive_bombs_are_rejected(tmp_path):
    app, _ = make_app(tmp_path)
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as bundle:
        link = zipfile.ZipInfo("SKILL.md")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(link, "../outside")
    with TestClient(app) as client:
        assert upload(client, "skill", data.getvalue(), "link.zip").status_code == 422
        bomb = io.BytesIO()
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("SKILL.md", "x" * (20 * 1024 * 1024 + 1))
        assert upload(client, "skill", bomb.getvalue(), "bomb.zip").status_code == 422


def test_mcp_stdio_discovers_and_calls_real_protocol_after_review(tmp_path):
    script = tmp_path / "mcp_server.py"
    script.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "app=FastMCP('echo')\n@app.tool()\n"
        "def echo(text: str) -> str:\n return 'MCP real protocol: '+text\n"
        "if __name__=='__main__': app.run(transport='stdio')\n",
        encoding="utf-8",
    )
    data = json.dumps(
        {
            "mcpServers": {
                "echo": {
                    "command": sys.executable,
                    "args": [str(script)],
                    "env": {"PRIVATE_TEST": "not-public"},
                }
            }
        }
    ).encode()
    app, model = make_app(tmp_path)
    with TestClient(app) as client:
        imported = upload(client, "mcp", data)
        assert imported.status_code == 201, imported.text
        item = imported.json()["items"][0]
        assert "not-public" not in imported.text
        stored = (tmp_path / "connectors" / "connectors.json").read_text()
        assert "not-public" not in stored
        response = client.patch(BASE + "/connectors/" + item["id"], json={"enabled": True})
        assert response.status_code == 200, response.text
        (name,) = response.json()["tools"]
        model.responses[:] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": name,
                        "args": {"text": "hello"},
                        "id": "mcp",
                    }
                ],
            ),
            AIMessage(content="MCP done"),
        ]
        review = client.post(BASE + "/chat", json={"mode": "work", "message": "Call MCP"}).json()
        assert "approval_id" in review
        result = client.post(
            f"{BASE}/threads/{review['thread_id']}/approval/" + review["approval_id"],
            json={"approved": True},
        )
        assert result.status_code == 200, result.text
        assert any(
            "MCP real protocol: hello" in (row.get("result") or "")
            for row in result.json()["activity"]
        )


def test_mcp_connection_failure_is_real_and_preserves_disabled_state(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as client:
        data = json.dumps({"mcpServers": {"missing": {"command": "no-such-mcp-command"}}}).encode()
        item = upload(client, "mcp", data).json()["items"][0]
        failed = client.patch(BASE + "/connectors/" + item["id"], json={"enabled": True})
        assert failed.status_code == 502, failed.text
        assert not client.get(BASE + "/connectors").json()["items"][0]["enabled"]


def test_streamable_http_mcp_can_be_imported_and_invoked(tmp_path):
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    server_app = FastMCP("http-test", stateless_http=True, json_response=True)

    @server_app.tool()
    def echo(text: str) -> str:
        return "HTTP MCP: " + text

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(server_app.streamable_http_app(), log_level="critical"))
    worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    worker.start()
    try:
        for _ in range(500):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started
        app, model = make_app(tmp_path)
        with TestClient(app) as client:
            data = json.dumps(
                {
                    "mcpServers": {
                        "http-echo": {
                            "url": f"http://127.0.0.1:{port}/mcp",
                            "type": "streamable-http",
                        }
                    }
                }
            ).encode()
            item = upload(client, "mcp", data).json()["items"][0]
            result = client.patch(BASE + "/connectors/" + item["id"], json={"enabled": True})
            assert result.status_code == 200, result.text
            (name,) = result.json()["tools"]
            model.responses[:] = [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": name,
                            "args": {"text": "verified"},
                            "id": "http",
                        }
                    ],
                ),
                AIMessage(content="Done"),
            ]
            review = client.post(
                BASE + "/chat", json={"mode": "work", "message": "Call HTTP MCP"}
            ).json()
            assert "approval_id" in review
            completed = client.post(
                f"{BASE}/threads/{review['thread_id']}/approval/" + review["approval_id"],
                json={"approved": True},
            )
            assert completed.status_code == 200, completed.text
            assert any(
                "HTTP MCP: verified" in (row.get("result") or "")
                for row in completed.json()["activity"]
            )
    finally:
        server.should_exit = True
        worker.join(10)
        listener.close()
        assert not worker.is_alive()


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {"text": {"type": "bogus"}}},
        {"type": "object", "properties": {"text": {"$ref": "https://example.com/schema"}}},
    ],
)
def test_invalid_or_remote_tool_schemas_return_validation_error(tmp_path, schema):
    app, _ = make_app(tmp_path)
    with TestClient(app) as client:
        data = json.dumps(
            {
                "name": "invalid",
                "description": "Invalid schema",
                "command": "echo",
                "input_schema": schema,
            }
        ).encode()
        failed = upload(client, "local", data)
        assert failed.status_code == 422, failed.text
        assert failed.json()["code"] == "CONNECTOR_INVALID"


@pytest.mark.asyncio
async def test_mcp_pagination_is_used_for_discovery_and_execution(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager

    from mcp.types import CallToolResult, TextContent, Tool

    from app.connectors import registry as module

    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    calls = []
    pages = [
        Tool(name="first", description="First tool", inputSchema=schema),
        Tool(name="second", description="Second tool", inputSchema=schema),
    ]

    class Session:
        async def list_tools(self, cursor=None):
            return SimpleNamespace(
                tools=[pages[0 if cursor is None else 1]],
                nextCursor="page-two" if cursor is None else None,
            )

        async def call_tool(self, name, arguments):
            calls.append(name)
            return CallToolResult(content=[TextContent(type="text", text="Page two executed")])

    @asynccontextmanager
    async def connection(*args):
        yield Session()

    monkeypatch.setattr(module, "mcp_session", connection)
    registry = ConnectorRegistry(tmp_path / "connectors", [])
    imported = await registry.import_file(
        "mcp",
        "mcp.json",
        json.dumps(
            {
                "mcpServers": {"paged": {"url": "https://example.test/mcp"}},
            }
        ).encode(),
    )
    await registry.toggle(imported[0]["id"], True)
    tool = registry.tools(str(tmp_path))[1]
    assert "Page two executed" in await tool.ainvoke({})
    assert calls == ["second"]
    original_name = tool.name
    pages[1] = Tool(name="second", description="Changed operation", inputSchema=schema)
    await registry.toggle(imported[0]["id"], True)
    assert registry.tools(str(tmp_path))[1].name != original_name
    with pytest.raises(Exception, match="changed"):
        await tool.ainvoke({})
    assert calls == ["second"]
    pages[1] = pages[0]
    with pytest.raises(ValueError, match="duplicate"):
        await list_mcp_tools(Session())
