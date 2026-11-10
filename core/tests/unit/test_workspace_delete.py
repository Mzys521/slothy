"""Workspace delete tool safety and review metadata tests. / 工作区删除工具安全与审批元数据测试。"""

import pytest

from app.agent.graph.review import review_call
from app.agent.tools import ToolRegistry, create_builtin_tools
from app.agent.tools.descriptor import Risk
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
    """Delete is classified separately, reviewed in both modes and names its target.
    / 删除独立分类，两种模式均需审批并标出目标。"""
    settings = ToolSettings(_env_file=None, workspace_path=tmp_path)
    tools = create_builtin_tools(settings)
    delete = next(tool for tool in tools if tool.name == "workspace_delete")
    assert str(delete.descriptor.risk) == "delete"
    assert delete.descriptor.mode == {"chat", "work"}
    assert delete.descriptor.requires_approval
    registry = ToolRegistry.from_tools(tools)
    assert registry.groups("work")["delete"] == ["workspace_delete"]
    assert "workspace_delete" not in registry.groups("work")["write"]
    review = review_call(
        registry,
        {"name": "workspace_delete", "args": {"path": "reports", "recursive": True}},
    )
    assert "HIGH RISK" in review["review_hint"]
    assert "reports" in review["review_hint"]
    assert "Recursive" in review["review_hint"]
    assert review["risk"] == "delete" and review["irreversible"] is True


@pytest.mark.parametrize("overwrite, expected", [(True, True), (False, False), (None, False)])
def test_only_overwriting_writes_get_irreversible_review_metadata(tmp_path, overwrite, expected):
    registry = ToolRegistry.from_tools(
        create_builtin_tools(ToolSettings(_env_file=None, workspace_path=tmp_path))
    )
    arguments = {"action": "write_file", "path": "note.txt", "content": "updated"}
    if overwrite is not None:
        arguments["overwrite"] = overwrite
    review = review_call(registry, {"name": "workspace_write", "args": arguments})
    assert review["risk"] == "write" and review["irreversible"] is expected
    assert ("HIGH RISK" in review["review_hint"]) is expected
    assert registry.descriptor("workspace_write").approval_for(arguments)


def test_explicit_irreversible_metadata_can_warn_for_other_reviewed_tools(tmp_path):
    tools = create_builtin_tools(ToolSettings(_env_file=None, workspace_path=tmp_path))
    custom = next(tool for tool in tools if tool.name == "run_python")
    custom.descriptor = custom.descriptor.model_copy(update={"irreversible": True})
    registry = ToolRegistry.from_tools(tools)
    assert custom.descriptor.risk == Risk.EXECUTE
    review = review_call(registry, {"name": "run_python", "args": {"code": "print(1)"}})
    assert review["risk"] == "execute" and review["irreversible"] is True
