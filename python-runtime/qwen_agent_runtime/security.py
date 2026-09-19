"""安全层：路径沙箱与危险命令检测。

所有文件类工具都必须经过沙箱校验，防止越权读写工作区以外的路径；
shell 命令在执行前经过危险模式检测。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

# 危险命令黑名单（正则，大小写不敏感）。命中即拒绝执行。
_DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    (r"\brm\s+(-[a-z]*[rf][a-z]*\s+)*/(\s|$)", "删除根目录"),
    (r"\brm\s+(-[a-z]*[rf][a-z]*\s+)*~", "删除主目录"),
    (r"\brm\s+-rf\s+\*", "递归删除当前目录全部内容"),
    (r"\brm\s+-rf\s+[a-zA-Z]:\\", "删除 Windows 盘符根目录"),
    (r"\bformat\s+[a-zA-Z]:", "格式化磁盘"),
    (r"\bmkfs\b", "创建文件系统"),
    (r"\bdd\s+if=.*of=/dev/", "向设备直接写入"),
    (r"\bshutdown\b|\breboot\b|\bpoweroff\b|\binit\s+0\b", "关机/重启/断电"),
    (r"\bdel\s+/[fs]\b", "Windows 删除系统文件"),
    (r"\brmdir\s+/", "删除根目录"),
    (r"curl[^|&;]*\|\s*(sudo\s+)?(ba)?sh", "下载并直接执行"),
    (r"wget[^|&;]*\|\s*(sudo\s+)?(ba)?sh", "下载并直接执行"),
    (r"\bchmod\s+777\s+/", "根目录权限放开"),
    (r"\bsudo\s+rm\b", "sudo 删除"),
    (r"\brm\s+(-[a-z]*[rf][a-z]*\s+)*~?/?\.ssh", "删除 SSH 密钥"),
    (r"\brmdir\s+/s\s+/q\s+[a-zA-Z]:\\", "Windows 递归静默删除盘符根"),
    (r"\bdel\s+/q\s+[a-zA-Z]:\\", "Windows 静默删除盘符根"),
]

_DANGEROUS_COMPILED = [(re.compile(p, re.IGNORECASE), desc) for p, desc in _DANGEROUS_PATTERNS]

# 单条命令输出上限（字符）
MAX_OUTPUT_CHARS = 30_000
# 单条命令默认超时（秒）
DEFAULT_TIMEOUT_SECONDS = 60
MAX_TIMEOUT_SECONDS = 300


class SecurityError(Exception):
    """安全校验失败。"""


class WorkspaceSandbox:
    """工作区路径沙箱：所有相对/绝对路径都解析到工作区内部。"""

    def __init__(self, workspace: str) -> None:
        self.root = Path(workspace).resolve()
        if not self.root.is_dir():
            raise SecurityError(f"工作区不是目录: {self.root}")

    def resolve(self, path: str | None) -> Path:
        """将用户提供的路径解析为工作区内的绝对路径。"""
        if path is None or str(path).strip() == "":
            return self.root
        candidate = Path(str(path))
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise SecurityError(
                f"路径越界（工作区外）: {path} -> {resolved}，工作区: {self.root}"
            ) from None
        return resolved

    def relative(self, path: Path) -> str:
        """将绝对路径转换为相对工作区的展示路径。"""
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()


def detect_dangerous_command(command: str) -> Optional[str]:
    """检测命令是否危险，命中返回原因描述，否则返回 None。"""
    if not command or not command.strip():
        return None
    for pattern, desc in _DANGEROUS_COMPILED:
        if pattern.search(command):
            return desc
    return None
