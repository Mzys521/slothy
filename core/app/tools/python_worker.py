"""独立文档执行进程，不导入应用或密钥配置。 / Standalone worker without application secrets."""

import ast
import builtins
import importlib
import os
import site
import sys
import sysconfig
from pathlib import Path

# 中文：允许文档生成常用标准库与可选文档库，不开放命令、网络和动态加载库。
# English: Document libraries only; no shells, networking or dynamic loading in generated code.
ALLOWED_IMPORTS = {
    "pathlib",
    "math",
    "decimal",
    "statistics",
    "datetime",
    "json",
    "csv",
    "io",
    "collections",
    "itertools",
    "functools",
    "re",
    "string",
    "textwrap",
    "docx",
    "openpyxl",
    "reportlab",
    "pptx",
}
FORBIDDEN_NAMES = {
    "eval",
    "exec",
    "compile",
    "getattr",
    "setattr",
    "delattr",
    "globals",
    "locals",
    "vars",
    "breakpoint",
    "input",
    "os",
    "sys",
    "subprocess",
    "shutil",
    "socket",
    "ctypes",
    "importlib",
    "builtins",
    "pickle",
    "marshal",
    "remove",
    "unlink",
    "rmdir",
    "rmtree",
    "system",
    "popen",
    "spawn",
    "fork",
    "symlink_to",
    "hardlink_to",
}


if __name__ == "__main__":
    source, output_dir, temp_dir, input_dir = [Path(value).resolve() for value in sys.argv[1:]]
    code = source.read_text(encoding="utf-8")
    tree = ast.parse(code, filename="generated_document.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] not in ALLOWED_IMPORTS for alias in node.names):
                raise ValueError("文档代码包含未允许的导入 / document import is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module or node.module.split(".")[0] not in ALLOWED_IMPORTS:
                raise ValueError("文档代码包含未允许的导入 / document import is not allowed")
            if any(
                alias.name in FORBIDDEN_NAMES or alias.name.startswith("_") for alias in node.names
            ):
                raise ValueError("不允许的导入成员 / imported member is not allowed")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES or (node.id.startswith("__") and node.id != "__name__"):
                raise ValueError("不允许的 Python 名称 / Python name is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in FORBIDDEN_NAMES:
                raise ValueError("不允许的 Python 属性 / Python attribute is not allowed")
    # 中文：预先初始化受信任的文档库，兼容 Windows 数值依赖的 DLL 加载。
    # English: Initialize trusted document libraries before enforcing generated-code policies.
    document_packages = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            document_packages.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            document_packages.add(node.module.split(".")[0])
    for package in sorted(document_packages & {"docx", "openpyxl", "reportlab", "pptx"}):
        importlib.import_module(package)

    read_roots = {input_dir, Path(sysconfig.get_path("stdlib")).resolve()}
    read_roots.update(Path(value).resolve() for value in site.getsitepackages())
    read_files = set()
    if getattr(sys, "frozen", False):
        read_roots.add(Path(sys._MEIPASS).resolve())
        # PyInstaller loads delayed modules from its executable's embedded PYZ archive.
        read_files.add(Path(sys.executable).resolve())

    def audit(event: str, args: tuple) -> None:
        """审计回调，不是安全沙箱。 / Audit callback, not a security sandbox."""
        if event.startswith(
            ("subprocess.", "socket.", "ctypes.", "os.exec", "os.spawn")
        ) or event in {
            "os.system",
            "os.fork",
            "os.link",
            "os.symlink",
        }:
            raise PermissionError(
                "文档进程不提供网络或命令执行 / no networking or command execution"
            )
        if event == "open":
            if isinstance(args[0], int):
                return
            path = Path(os.fsdecode(args[0])).resolve()
            if any(
                part.lower() in {".git", ".aws", ".codex"}
                or (part.lower().startswith(".env") and part.lower() != ".env.example")
                for part in path.parts
            ):
                raise PermissionError("私密配置不可读取 / private configuration cannot be read")
            mode, flags = args[1], args[2]
            writing = (isinstance(mode, str) and any(flag in mode for flag in "wax+")) or (
                isinstance(flags, int)
                and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            )
            if writing:
                if not path.is_relative_to(output_dir) and not path.is_relative_to(temp_dir):
                    raise PermissionError("仅允许写入本次产物目录 / writes must stay in this run")
            elif path not in read_files and not any(
                path.is_relative_to(root) for root in read_roots
            ):
                raise PermissionError("文件不在允许读取范围 / file is outside allowed read roots")
        elif event in {"os.remove", "os.rmdir", "shutil.rmtree"}:
            if not Path(os.fsdecode(args[0])).resolve().is_relative_to(temp_dir):
                raise PermissionError("不提供文件删除 / file deletion is not provided")
        elif event == "os.rename":
            for value in args[:2]:
                path = Path(os.fsdecode(value)).resolve()
                if not path.is_relative_to(output_dir) and not path.is_relative_to(temp_dir):
                    raise PermissionError("重命名必须在本次目录内 / renames must stay in this run")
        elif event in {"os.mkdir", "os.chdir"}:
            path = Path(os.fsdecode(args[0])).resolve()
            if not path.is_relative_to(output_dir) and not path.is_relative_to(temp_dir):
                raise PermissionError(
                    "目录操作必须在本次目录内 / directory operation is outside this run"
                )

    # 中文：审计函数必须在执行生成代码前注册；它是策略检查，不是 OS 隔离边界。
    # English: Register before generated code; these are policy checks, not OS-level isolation.
    sys.addaudithook(audit)
    allowed_builtins = {
        name: getattr(builtins, name)
        for name in (
            "abs",
            "all",
            "any",
            "bool",
            "dict",
            "enumerate",
            "float",
            "int",
            "len",
            "list",
            "max",
            "min",
            "print",
            "range",
            "reversed",
            "round",
            "set",
            "sorted",
            "str",
            "sum",
            "tuple",
            "zip",
            "open",
            "isinstance",
            "Exception",
            "ValueError",
            "TypeError",
            "RuntimeError",
            "__build_class__",
            "__import__",
        )
    }
    exec(
        compile(tree, "generated_document.py", "exec"),
        {
            "__builtins__": allowed_builtins,
            "__name__": "__main__",
            "workspace": output_dir,
            "input_dir": input_dir,
        },
    )
