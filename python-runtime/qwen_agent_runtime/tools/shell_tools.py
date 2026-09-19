"""Shell 工具：在工作区内执行命令，带超时与输出截断。"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Optional

from ..security import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_OUTPUT_CHARS,
    MAX_TIMEOUT_SECONDS,
    WorkspaceSandbox,
    detect_dangerous_command,
)

# Windows 上通过 cmd /c 执行；其他平台通过 sh -c
_IS_WINDOWS = os.name == "nt"


def run_command(sandbox: WorkspaceSandbox, command: str,
                timeout_seconds: Optional[str] = None,
                cwd: Optional[str] = None) -> dict[str, Any]:
    """执行 shell 命令。

    安全约束：
    - 危险模式命令直接拒绝（如 rm -rf /）；
    - 工作目录限制在工作区（cwd 相对于工作区）；
    - 超时默认 60s，上限 300s；
    - 输出截断到 30000 字符。
    """
    if not command or not command.strip():
        return {"error": "命令为空"}

    reason = detect_dangerous_command(command)
    if reason:
        return {"error": f"危险命令被拒绝: {reason}"}

    timeout = DEFAULT_TIMEOUT_SECONDS
    if timeout_seconds:
        try:
            timeout = max(1, min(MAX_TIMEOUT_SECONDS, int(timeout_seconds)))
        except ValueError:
            return {"error": f"timeout_seconds 非法: {timeout_seconds}"}

    workdir = sandbox.resolve(cwd) if cwd else sandbox.root
    if not workdir.is_dir():
        return {"error": f"工作目录不存在: {cwd or '.'}"}

    shell_cmd = command if _IS_WINDOWS else command
    try:
        proc = subprocess.run(
            shell_cmd,
            shell=True,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        return {
            "error": f"命令超时（>{timeout}s）",
            "stdout": (e.stdout or "")[-MAX_OUTPUT_CHARS:],
            "stderr": (e.stderr or "")[-MAX_OUTPUT_CHARS:],
            "exit_code": None,
        }
    except OSError as e:
        return {"error": f"命令启动失败: {e}"}

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    out_truncated = len(stdout) > MAX_OUTPUT_CHARS
    err_truncated = len(stderr) > MAX_OUTPUT_CHARS
    return {
        "exit_code": proc.returncode,
        "stdout": stdout[:MAX_OUTPUT_CHARS],
        "stderr": stderr[:MAX_OUTPUT_CHARS],
        "stdout_truncated": out_truncated,
        "stderr_truncated": err_truncated,
        "cwd": sandbox.relative(workdir),
        "command": command,
    }
