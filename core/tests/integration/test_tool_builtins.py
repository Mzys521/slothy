import base64
import os
import subprocess

import pytest
from docx import Document

from app.agent.tools import ToolRegistry, create_builtin_tools
from app.config import ToolSettings
from tests.support.tool_calls import call, execute_calls


def registry(tmp_path, **settings):
    return ToolRegistry.from_tools(
        create_builtin_tools(ToolSettings(_env_file=None, workspace_path=tmp_path, **settings))
    )


async def test_workspace_pages_literal_search_and_default_no_overwrite(tmp_path):
    (tmp_path / "readme.txt").write_text("one\nneedle.*\nthree", encoding="utf-8")
    tools = registry(tmp_path)
    results, _ = await execute_calls(
        tools,
        [
            call("workspace_read", {"action": "read", "path": "readme.txt", "limit": 4}, "page"),
            call("workspace_read", {"action": "search", "pattern": "needle.*"}, "search"),
            call("workspace_read", {"action": "search", "pattern": "need.*"}, "literal"),
            call(
                "workspace_write",
                {"action": "write_file", "path": "new.txt", "content": "你好"},
                "write",
            ),
            call(
                "workspace_write",
                {"action": "write_file", "path": "new.txt", "content": "replace"},
                "existing",
            ),
        ],
    )
    assert results[0].data["text"] == "one\n" and results[0].next_offset == 4
    assert results[1].data["matches"][0]["line"] == 2
    assert results[2].data["matches"] == []
    assert results[3].artifacts[0].bytes == 6
    assert results[4].error.code == "DENIED"
    assert (tmp_path / "new.txt").read_text(encoding="utf-8") == "你好"


@pytest.mark.parametrize(
    "path",
    [
        "../outside.txt",
        ".git/config",
        ".env",
        ".aws/credentials",
        ".codex/settings",
        ".slothy/artifacts/runs/journal",
    ],
)
async def test_workspace_private_and_escape_paths_are_blocked(tmp_path, path):
    tools = registry(tmp_path)
    results, _ = await execute_calls(
        tools,
        [
            call("workspace_read", {"action": "read", "path": path}, "read"),
            call(
                "workspace_write", {"action": "write_file", "path": path, "content": "no"}, "write"
            ),
        ],
    )
    assert all(
        not item.ok and item.error.code in {"DENIED", "OUT_OF_WORKSPACE"} for item in results
    )


async def test_workspace_rejects_link_escape_and_binary_text(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("secret")
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("This account cannot create symbolic links")
        link = str(root / "linked").replace("'", "''")
        destination = str(outside).replace("'", "''")
        script = (f"New-Item -ItemType Junction -Path '{link}' -Target '{destination}' "
                  "-ErrorAction Stop | Out-Null")
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-EncodedCommand",
                base64.b64encode(script.encode("utf-16-le")).decode(),
            ],
            capture_output=True,
        )
        if result.returncode:
            pytest.skip("This account cannot create links or NTFS junctions")
    results, _ = await execute_calls(
        registry(root),
        [
            call("workspace_read", {"action": "read", "path": "linked/private.txt"}),
        ],
    )
    assert results[0].error.code == "OUT_OF_WORKSPACE"
    assert "secret" not in results[0].wire()


async def test_workspace_listing_and_read_byte_limits(tmp_path):
    for index in range(210):
        (tmp_path / f"file-{index}.txt").write_text("text")
    (tmp_path / "large.txt").write_bytes(b"x" * 1025)
    results, _ = await execute_calls(
        registry(tmp_path, max_workspace_read_bytes=1024),
        [
            call("workspace_read", {"action": "list"}, "list"),
            call("workspace_read", {"action": "read", "path": "large.txt"}, "large"),
        ],
    )
    assert len(results[0].data["entries"]) <= 200 and results[0].data["truncated"]
    assert results[1].error.code == "OUTPUT_TOO_LARGE"


@pytest.mark.parametrize("format", ["md", "txt", "csv", "json", "docx"])
async def test_template_exports_real_formats_with_safe_content(tmp_path, format):
    results, _ = await execute_calls(
        registry(tmp_path),
        [
            call(
                "export_document",
                {
                    "format": format,
                    "filename": "report." + format,
                    "title": "Report",
                    "sections": [
                        {
                            "heading": "Totals",
                            "paragraphs": ["import os is just text"],
                            "items": ["First"],
                            "table": {"columns": ["Amount"], "rows": [["0.30"]]},
                        }
                    ],
                },
            )
        ],
    )
    result = results[0]
    assert result.ok, result
    artifact = result.artifacts[0]
    path = tmp_path / artifact.rel_path
    assert path.is_file() and path.stat().st_size == artifact.bytes
    if format == "docx":
        document = Document(path)
        assert document.tables[0].rows[1].cells[0].text == "0.30"
    else:
        assert "0.30" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("filename", ["../escape.md", "C:\\escape.md", "con.md", "report.txt"])
async def test_template_filenames_are_validated(tmp_path, filename):
    result, _ = await execute_calls(
        registry(tmp_path),
        [
            call("export_document", {"format": "md", "filename": filename}),
        ],
    )
    assert result[0].error.code == "INVALID_ARGS"


async def test_datetime_zone_conversion_and_invalid_zone(tmp_path):
    results, _ = await execute_calls(
        registry(tmp_path),
        [
            call(
                "get_datetime",
                {"timezone": "Asia/Shanghai", "datetime_iso": "2026-11-06T00:00:00Z"},
                "zone",
            ),
            call("get_datetime", {"timezone": "Invalid/Zone"}, "invalid"),
            call(
                "get_datetime", {"timezone": "UTC", "datetime_iso": "2026-11-06T00:00:00"}, "naive"
            ),
        ],
    )
    assert results[0].data["iso"] == "2026-11-06T08:00:00+08:00"
    assert results[0].data["weekday"] == "Friday"
    assert results[1].error.code == results[2].error.code == "INVALID_ARGS"
