"""slothy's bundled backend. No development configuration is read here.

slothy 的打包后端；此处不读取开发配置。
"""

import json
import os
import socket
import sys
import threading
from pathlib import Path


def watch_desktop():
    """Exit if the desktop crashes; child document workers share this job's lifecycle.

    桌面进程崩溃时退出；子文档进程与本进程共享同一生命周期。
    """
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    parent = kernel.OpenProcess(0x00100000, False, int(os.environ["SLOTHY_PARENT_PID"]))
    if not parent:
        raise RuntimeError("The slothy desktop process is no longer available.")

    def wait():
        """Block until the desktop process exits, then terminate this process.
        / 阻塞等待桌面进程退出，然后终止本进程。
        """
        kernel.WaitForSingleObject(parent, 0xFFFFFFFF)
        kernel.CloseHandle(parent)
        os._exit(0)

    threading.Thread(target=wait, daemon=True).start()


def main():
    """Start the bundled backend or run the document worker in place.
    / 启动内置后端，或就地运行文档工作进程。

    Raises:
        RuntimeError: If required desktop environment variables are missing.
            / 缺少必需的桌面环境变量时抛出。
    """
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) > 1 and sys.argv[1] == "--document-worker":
        import runpy

        sys.argv = ["slothy-document-worker", *sys.argv[2:]]
        runpy.run_module("app.tools.python_worker", run_name="__main__")
        return

    data = os.environ.get("SLOTHY_DATA_DIR")
    token = os.environ.get("SLOTHY_API_TOKEN")
    if not data or not token:
        raise RuntimeError("Open slothy to start its local service.")
    if os.environ.get("SLOTHY_PARENT_PID"):
        watch_desktop()
    directory = Path(data).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "SLOTHY_DESKTOP": "1",
            "CHECKPOINT_SQLITE_PATH": str(directory / "checkpoints.sqlite"),
            "TOOLS_WORKSPACE_PATH": str(directory / "artifacts"),
            "LANGSMITH_TRACING": "false",
            "LANGCHAIN_TRACING_V2": "false",
        }
    )
    # Inherited development credentials must never configure a fresh installation.
    for name in tuple(os.environ):
        if name.upper().startswith(("LLM_", "OPENAI_", "DEEPSEEK_", "MIMO_", "ANTHROPIC_")):
            del os.environ[name]
    os.chdir(directory)

    import uvicorn

    from app.api import create_app

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.bind(("127.0.0.1", 0))
    server_socket.listen(128)
    port = server_socket.getsockname()[1]
    print(json.dumps({"event": "listening", "port": port}), flush=True)
    config = uvicorn.Config(
        create_app(),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        loop="asyncio",
        http="h11",
        ws="none",
    )
    uvicorn.Server(config).run(sockets=[server_socket])


if __name__ == "__main__":
    main()
