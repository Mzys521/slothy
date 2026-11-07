"""Independent processes with timeout and bounded output.
/ 带超时与输出上限的独立进程。"""

import asyncio
import codecs
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class ProcessRunner:
    """Execute a structured argv without invoking a command shell.
    / 执行结构化 argv，不经过命令 Shell。"""

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_chars: int,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run the argv and merge stdout and stderr.

        Only a small allowlist of environment variables is inherited, so provider keys and
        application secrets never reach the child. Output is decoded as UTF-8 with replacement
        and capped; exceeding the cap or the timeout kills the process.
        / 仅继承少量环境变量白名单，因此 Provider 密钥与应用机密不会进入子进程。输出按 UTF-8
        替换解码并受限；超出上限或超时会终止进程。

        Args:
            argv: Trusted executable path and validated arguments.
                / 可信可执行文件路径与已校验参数。
            cwd: Resolved working directory. / 已解析的工作目录。
            timeout_seconds: Maximum execution seconds. / 最长执行秒数。
            max_output_chars: Returned character cap. / 返回输出字符上限。
            environment: Explicit overrides; never used to inherit secrets.
                / 显式环境覆盖；不会用于继承密钥。

        Returns:
            Mapping with ok, return_code, bounded output and an error code when limited.
            / 含 ok、return_code、受限输出以及受限错误码的映射。
        """
        env = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "LANG", "LC_ALL"}
        }
        env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
        if environment:
            env.update(environment)
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        output = []
        output_length = 0
        error = None
        try:
            async with asyncio.timeout(timeout_seconds):
                while chunk := await process.stdout.read(4096):
                    text = decoder.decode(chunk)
                    output.append(text)
                    output_length += len(text)
                    if output_length > max_output_chars:
                        error = "OUTPUT_LIMIT"
                        break
                if error is None:
                    await process.wait()
        except TimeoutError:
            error = "TIMEOUT"
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    # English: The process may have just exited. / 中文：进程可能恰好自行退出。
                    pass
            # English: Drain pipes after termination so a full pipe cannot block wait.
            # 中文：终止后排空管道，避免满管道让 wait 卡住。
            await process.communicate()
        text = "".join(output) + decoder.decode(b"", final=True)
        if len(text) > max_output_chars:
            error = error or "OUTPUT_LIMIT"
        return {
            "ok": process.returncode == 0 and error is None,
            "return_code": process.returncode,
            "output": text[:max_output_chars],
            "error": error,
        }
