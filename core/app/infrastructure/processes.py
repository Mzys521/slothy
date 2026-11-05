"""带超时与输出上限的独立进程。 / Independent processes with timeout and bounded output."""

import asyncio
import codecs
import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class ProcessRunner:
    """执行结构化 argv，不执行 Shell 字符串。 / Execute argv without invoking a command shell."""

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
        max_output_chars: int,
        environment: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """执行并合并 stdout/stderr。 / Run and merge stdout/stderr.

        Args:
            argv: 可信程序路径与已校验参数。 / Trusted executable and validated arguments.
            cwd: 已解析的工作目录。 / Resolved working directory.
            timeout_seconds: 最长执行秒数。 / Execution timeout in seconds.
            max_output_chars: 返回输出字符上限。 / Returned character cap.
            environment: 显式环境覆盖，不继承密钥。 / Explicit overrides; no inherited secrets.
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
                    # 中文：进程可能恰好自行退出。 / English: The process may have just exited.
                    pass
            # 中文：终止后排空管道，避免满管道让 wait 卡住。
            # English: Drain pipes after termination so a full pipe cannot block wait.
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
