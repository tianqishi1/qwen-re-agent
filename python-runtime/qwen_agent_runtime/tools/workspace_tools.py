"""工作区信息工具。"""

from __future__ import annotations

import os
import platform
import sys
from typing import Any

from ..security import WorkspaceSandbox


def get_workspace_info(sandbox: WorkspaceSandbox) -> dict[str, Any]:
    """返回工作区根、操作系统与运行时信息。"""
    return {
        "workspace": str(sandbox.root),
        "os": platform.system(),
        "os_release": platform.release(),
        "python": sys.version.split()[0],
        "cwd": os.getcwd(),
    }
