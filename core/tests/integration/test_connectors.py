import base64
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
import yaml
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


def test_mcp_form_uses_real_stdio_and_revokes_an_old_approval_after_edit(tmp_path, caplog):
    server = tmp_path / "server.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "from pathlib import Path\n"
        "import os\n"
        "assert os.environ['FORM_SECRET'] == 'fixture-private-stdio'\n"
        "Path('started').touch()\n"
        "app = FastMCP('document-helper')\n"
        "@app.tool()\n"
        "def echo(text: str) -> str:\n"
        "    Path('called').touch()\n"
        "    return text\n"
        "if Path('add-tool').exists():\n"
        "    @app.tool()\n"
        "    def word_count(text: str) -> int:\n"
        "        return len(text.split())\n"
        "app.run(transport='stdio')\n",
        encoding="utf-8",
    )
    working = tmp_path / "working"
    working.mkdir()
    body = {
        "name": "document-helper",
        "description": "Local document utility",
        "config": {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(server)],
            "cwd": str(working),
            "env": {"FORM_SECRET": "fixture-private-stdio"},
        },
    }
    app, model = make_app(tmp_path)
    with TestClient(app) as client:
        caplog.set_level("DEBUG", logger="mcp.client.stdio")
        test_url = BASE + "/connectors/mcp/test"
        assert client.post(test_url, json={"config": body["config"]}).status_code == 409
        assert not (working / "started").exists()
        result = client.post(test_url, json={"config": body["config"], "confirmed": True})
        assert result.json()["response_status"] == "tools_listed", result.text
        assert result.json()["tools"][0]["name"] == "echo"
        assert (working / "started").exists()
        assert client.get(BASE + "/connectors").json()["items"] == []
        (working / "started").unlink()
        created = client.post(BASE + "/connectors/mcp", json=body)
        assert created.status_code == 201, created.text
        item = created.json()
        path = f"{BASE}/connectors/{item['id']}"
        assert item["connection_status"] == "disabled"
        assert not (working / "started").exists()
        detail = client.get(path)
        assert detail.headers["cache-control"] == "no-store"
        assert detail.json()["mcp"]["env"] == {"FORM_SECRET": None}
        assert "fixture-private-stdio" not in detail.text
        assert client.post(BASE + "/connectors/mcp", json=body).status_code == 409
        saved_test = client.post(path + "/test", json={"confirmed": True})
        assert saved_test.json()["ok"], saved_test.text
        assert client.get(path).json()["item"]["tools"] == []
        refreshed = client.post(path + "/refresh", json={"confirmed": True})
        assert refreshed.json()["tool_count"] == 1, refreshed.text
        assert not refreshed.json()["connector"]["enabled"]
        assert client.get(path).json()["tools"][0]["input_schema"]["required"] == ["text"]
        enabled = client.patch(path, json={"enabled": True, "revision": item["revision"]})
        assert enabled.status_code == 200, enabled.text
        (old_tool,) = enabled.json()["tools"]
        model.responses[:] = [
            AIMessage(
                content="",
                tool_calls=[{"name": old_tool, "args": {"text": "a pending call"}, "id": "old"}],
            ),
            AIMessage(content="Done"),
        ]
        paused = client.post(BASE + "/chat", json={"mode": "work", "message": "Use the connector"})
        assert paused.status_code == 202, paused.text
        assert not (working / "called").exists()
        review = paused.json()
        edited = {
            **body,
            "enabled": True,
            "revision": item["revision"],
            "config": {
                **body["config"],
                "args": [str(server), "edited"],
                "env": {"FORM_SECRET": None},
            },
        }
        assert client.put(path + "/mcp", json=edited).status_code == 409
        edited["confirmed"] = True
        changed = client.put(path + "/mcp", json=edited)
        assert changed.status_code == 200, changed.text
        assert changed.json()["connection_status"] == "connected"
        assert changed.json()["tools"] != [old_tool]
        assert changed.json()["id"] == item["id"]
        assert client.put(path + "/mcp", json=edited).status_code == 409
        failed = client.put(
            path + "/mcp",
            json={
                **edited,
                "revision": changed.json()["revision"],
                "config": {**edited["config"], "command": "missing-slothy-mcp-executable"},
            },
        )
        assert failed.status_code == 502, failed.text
        assert failed.json()["code"] == "MCP_PROGRAM_MISSING"
        assert client.get(path).json()["item"]["tools"] == changed.json()["tools"]
        finished = client.post(
            f"{BASE}/threads/{review['thread_id']}/approval/{review['approval_id']}",
            json={"approved": True},
        )
        assert finished.status_code == 200, finished.text
        assert not (working / "called").exists()
        assert "fixture-private-stdio" not in caplog.text
        assert (
            "fixture-private-stdio" not in (tmp_path / "connectors" / "connectors.json").read_text()
        )
    restarted, _ = make_app(tmp_path)
    with TestClient(restarted) as client:
        item = client.get(path).json()["item"]
        assert item["enabled"] and item["connection_status"] == "unchecked"
        assert item["tools"] == changed.json()["tools"]
        assert client.post(path + "/test", json={"confirmed": True}).json()["ok"]
        (working / "add-tool").touch()
        refreshed = client.post(path + "/refresh", json={"confirmed": True})
        assert refreshed.json()["tool_count"] == 2, refreshed.text
        assert set(client.get(BASE + "/runtime").json()["work_tools"]) >= set(
            refreshed.json()["connector"]["tools"]
        )
        assert client.delete(path).status_code == 204


@pytest.mark.parametrize("transport,route", [("streamable-http", "mcp"), ("sse", "sse")])
def test_mcp_form_http_transports_report_real_auth_errors_and_retain_headers(
    tmp_path, caplog, transport, route
):
    import uvicorn
    from mcp.server.fastmcp import FastMCP
    from starlette.responses import PlainTextResponse

    service = FastMCP("form-web", stateless_http=True, json_response=True)

    @service.tool()
    def lookup(term: str) -> str:
        """Look up a term in the fixture."""
        return term

    endpoint = service.sse_app() if transport == "sse" else service.streamable_http_app()
    credential = b"Bearer fixture-private-header"

    async def authorized(scope, receive, send):
        if scope["type"] != "http" or dict(scope["headers"]).get(b"authorization") == credential:
            await endpoint(scope, receive, send)
        else:
            await PlainTextResponse("Private upstream text", status_code=401)(scope, receive, send)

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(authorized, log_level="critical", timeout_graceful_shutdown=2)
        )
        worker = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        worker.start()
        try:
            for _ in range(500):
                if server.started:
                    break
                time.sleep(0.01)
            assert server.started
            app, _ = make_app(tmp_path)
            config = {
                "url": f"http://127.0.0.1:{port}/{route}?token=fixture-private-query",
                "type": transport,
                "timeout": 5,
            }
            with TestClient(app) as client:
                caplog.set_level("INFO", logger="httpx")
                failed = client.post(
                    BASE + "/connectors/mcp/test",
                    json={
                        "config": config,
                        "confirmed": True,
                    },
                )
                assert failed.status_code == 200, failed.text
                assert failed.json()["http_status"] == 401, failed.text
                assert not failed.json()["ok"]
                assert "Private upstream text" not in failed.text
                config["headers"] = {"Authorization": credential.decode()}
                created = client.post(
                    BASE + "/connectors/mcp",
                    json={
                        "name": "web-helper",
                        "config": config,
                        "enabled": True,
                        "confirmed": True,
                    },
                )
                assert created.status_code == 201, created.text
                item = created.json()
                path = f"{BASE}/connectors/{item['id']}"
                detail = client.get(path)
                assert detail.json()["mcp"]["headers"] == {"Authorization": None}
                assert credential.decode() not in detail.text
                assert detail.json()["tools"][0]["description"] == "Look up a term in the fixture."
                test = client.post(
                    BASE + "/connectors/mcp/test",
                    json={
                        "confirmed": True,
                        "connector_id": item["id"],
                        "revision": item["revision"],
                        "config": detail.json()["mcp"],
                    },
                )
                assert test.json()["ok"], test.text
                assert test.json()["tools"][0]["input_schema"]["required"] == ["term"]
                assert "fixture-private-query" not in caplog.text
                credential = b"rotated-fixture-credential"
                refused = client.post(path + "/refresh", json={"confirmed": True}).json()
                assert not refused["ok"] and refused["http_status"] == 401
                after = client.get(path).json()["item"]
                assert after["enabled"] and after["connection_status"] == "failed"
                assert after["tools"] == item["tools"]
        finally:
            server.should_exit = True
            worker.join(10)
            assert not worker.is_alive()


def reference(name, text):
    return {"name": name, "data": base64.b64encode(text.encode()).decode()}


def test_skill_form_stages_yaml_and_references_edits_revokes_and_restores(tmp_path):
    marker = tmp_path / "never-run"
    body = {
        "name": "写作指引",
        "description": "Guide: bilingual\n---\n描述仍是 YAML 值",
        "instructions": "# Review\nUse the writing guide.",
        "files": [
            reference("style.txt", "Keep sentences short."),
            reference("example.py", f"from pathlib import Path\nPath({str(marker)!r}).touch()"),
        ],
    }
    app, model = make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(BASE + "/connectors/skills", json=body)
        assert created.status_code == 201, created.text
        item = created.json()
        path = f"{BASE}/connectors/{item['id']}"
        assert not item["enabled"] and item["reference_count"] == 2
        assert "read_skill_file" not in client.get(BASE + "/runtime").json()["work_tools"]
        detail = client.get(path).json()["skill"]
        header, _, instructions = detail["markdown"].removeprefix("---\n").partition("\n---\n")
        assert yaml.safe_load(header) == {key: body[key] for key in ("name", "description")}
        assert instructions.lstrip() == body["instructions"] + "\n"
        assert detail["references"] == ["references/example.py", "references/style.txt"]
        assert not marker.exists()
        record = app.state.connectors.records[item["id"]]
        assert (tmp_path / "connectors" / record["bundle"] / "SKILL.md").exists()
        edit = {
            **body,
            "revision": item["revision"],
            "instructions": "Use the updated writing guide.",
            "enabled": True,
            "remove_files": ["references/style.txt"],
            "files": [reference("style.txt", "Updated reference")],
        }
        illegal = client.put(
            path + "/skill", json={**edit, "files": [reference("../outside", "x")]}
        )
        assert illegal.status_code == 422, illegal.text
        assert client.get(path).json()["skill"] == detail
        updated = client.put(path + "/skill", json=edit)
        assert updated.status_code == 200, updated.text
        assert updated.json()["enabled"]
        assert client.put(path + "/skill", json=edit).status_code == 409
        assert not (tmp_path / "connectors" / record["bundle"]).exists()
        assert "read_skill_file" in client.get(BASE + "/runtime").json()["chat_tools"]
        model.responses[:] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_skill_file",
                        "id": "read",
                        "args": {"skill_id": item["id"], "path": "references/style.txt"},
                    }
                ],
            ),
            AIMessage(content="Done"),
        ]
        response = client.post(BASE + "/chat", json={"message": "Use the guide"})
        assert response.status_code == 200, response.text
        assert any(
            "Updated reference" in (row.get("result") or "") for row in response.json()["activity"]
        )
        assert any(
            message.name == "skill_reference" and edit["instructions"] in message.content
            for message in model.seen[0]
        )
        assert not marker.exists()
    restarted, _ = make_app(tmp_path)
    with TestClient(restarted) as client:
        detail = client.get(path).json()
        assert detail["item"]["enabled"] and detail["item"]["reference_count"] == 2
        assert detail["skill"]["instructions"].strip() == edit["instructions"]
        assert client.patch(path, json={"enabled": False}).status_code == 200
        assert "read_skill_file" not in client.get(BASE + "/runtime").json()["chat_tools"]
        assert client.delete(path).status_code == 204
        assert not marker.exists()


def test_imported_skill_can_be_edited_with_nested_paths_and_metadata(tmp_path):
    app, _ = make_app(tmp_path)
    with TestClient(app) as client:
        data = archive(
            {
                "notes.txt": "Outside the skill root, preserved on edit.",
                "writing/SKILL.md": "---\nname: writer\nlicense: MIT\n---\nOriginal instructions",
                "writing/references/style.md": "Original guide",
                "writing/examples/sample.md": "Original example",
            }
        )
        item = upload(client, "skill", data, "writing.zip").json()["items"][0]
        path = f"{BASE}/connectors/{item['id']}"
        assert client.get(path).json()["skill"]["references"] == [
            "examples/sample.md",
            "references/style.md",
        ]
        updated = client.put(
            path + "/skill",
            json={
                "name": "writer",
                "instructions": "Revised instructions",
                "revision": item["revision"],
                "remove_files": ["references/style.md"],
            },
        )
        assert updated.status_code == 200, updated.text
        detail = client.get(path).json()["skill"]
        assert "license: MIT" in detail["markdown"]
        assert detail["references"] == ["examples/sample.md"]
        assert upload(client, "skill", data, "writing.zip").status_code == 409


@pytest.mark.parametrize("name", ["CON.txt", "bad.txt:stream", "reference/../outside", r"a\file"])
def test_skill_form_rejects_invalid_upload_names(tmp_path, name):
    app, _ = make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            BASE + "/connectors/skills",
            json={
                "name": "portable",
                "instructions": "Read the references.",
                "files": [reference(name, "Contents")],
            },
        )
        assert response.status_code == 422, response.text
        assert not app.state.connectors.records


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
