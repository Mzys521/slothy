"""Workspace delete tool safety and review metadata tests. / 工作区删除工具安全与审批元数据测试。"""

import pytest

from app.agent.graph.review import review_call
from app.agent.tools import ToolRegistry, create_builtin_tools
from app.config import ToolSettings
from app.tools.contracts import ToolOperationError
from app.tools.workspace import WorkspaceFiles


@pytest.mark.asyncio
async def test_workspace_delete_file_and_non_empty_directory(tmp_path):
    """Files and recursive directories follow the recursive rule.
    / 文件与目录按 recursive 规则删除。"""
    settings = ToolSettings(_env_file=None, workspace_path=tmp_path)
    workspace = WorkspaceFiles(settings)
    (tmp_path / "note.txt").write_text("x", encoding="utf-8")
    await workspace.delete("note.txt")
    assert not (tmp_path / "note.txt").exists()

    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "child.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ToolOperationError, match="not empty"):
        await workspace.delete("dir")
    await workspace.delete("dir", recursive=True)
    assert not (tmp_path / "dir").exists()


@pytest.mark.asyncio
async def test_workspace_delete_rejects_root_and_outside_paths(tmp_path):
    """Root and traversal targets are rejected. / 拒绝根目录与越界路径。"""
    workspace = WorkspaceFiles(ToolSettings(_env_file=None, workspace_path=tmp_path))
    with pytest.raises(ToolOperationError):
        await workspace.delete(".")
    with pytest.raises(ToolOperationError):
        await workspace.delete("../outside")


def test_delete_tool_is_high_risk_and_review_hint_names_target(tmp_path):
    """The delete tool is work-only, reviewed and names its target.
    / 删除工具仅在 Work 模式、需审批并标出目标。"""
    settings = ToolSettings(_env_file=None, workspace_path=tmp_path)
    tools = create_builtin_tools(settings)
    delete = next(tool for tool in tools if tool.name == "workspace_delete")
    assert str(delete.descriptor.risk) == "write"
    assert delete.descriptor.mode == {"chat", "work"}
    assert delete.descriptor.requires_approval
    registry = ToolRegistry.from_tools(tools)
    review = review_call(
        registry,
        {"name": "workspace_delete", "args": {"path": "reports", "recursive": True}},
    )
    assert "HIGH RISK" in review["review_hint"]
    assert "reports" in review["review_hint"]
    assert "Recursive" in review["review_hint"]
