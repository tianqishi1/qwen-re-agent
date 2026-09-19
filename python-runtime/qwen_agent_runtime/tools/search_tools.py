"""检索工具：glob 与 grep。

glob 使用 Python pathlib 的通配语义；grep 使用标准库 re，
逐行扫描文本文件（跳过二进制文件与常见噪音目录）。
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Optional

from ..security import WorkspaceSandbox

# grep/glob 默认跳过的目录
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "target", "build",
             "dist", ".venv", "venv", "__pycache__", ".idea", ".vscode", ".tox"}
# grep 跳过的文件扩展名（二进制）
SKIP_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".pdf", ".zip",
             ".jar", ".class", ".exe", ".dll", ".so", ".dylib", ".bin", ".o",
             ".obj", ".pyc", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3"}
# 单次 glob/grep 结果上限
MAX_RESULTS = 1_000


def _walk_files(root: Path, include_hidden: bool = False):
    """遍历文件，跳过噪音目录。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if include_hidden or not d.startswith(".")]
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def _match_glob(rel_posix: str, name: str, pattern: str) -> bool:
    """glob 匹配：支持 ** 递归（任意深度）语义。

    - ``**`` 单独出现：匹配任意层级；
    - ``**/x``：x 出现在任意深度（含根）；
    - 其余使用 fnmatch 的路径通配语义。
    """
    if pattern == "**":
        return True
    if pattern.startswith("**/"):
        rest = pattern[3:]
        if fnmatch.fnmatch(name, rest):
            return True
        # rel_posix 任意深度下匹配 rest
        parts = rel_posix.split("/")
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            if suffix and fnmatch.fnmatch(suffix, rest):
                return True
        return False
    if "**" in pattern:
        # 一般化的 ** 递归：展开为逐段匹配
        segments = pattern.split("/")
        rel_segments = rel_posix.split("/")
        return _match_segments(rel_segments, segments)
    return fnmatch.fnmatch(rel_posix, pattern) or fnmatch.fnmatch(name, pattern)


def _match_segments(rel_segments: list[str], pattern_segments: list[str]) -> bool:
    """逐段 glob 匹配，** 可匹配零到多段。"""
    if not pattern_segments:
        return not rel_segments
    head = pattern_segments[0]
    if head == "**":
        # ** 匹配 0..n 段
        for skip in range(len(rel_segments) + 1):
            if _match_segments(rel_segments[skip:], pattern_segments[1:]):
                return True
        return False
    if not rel_segments:
        return False
    if fnmatch.fnmatch(rel_segments[0], head):
        return _match_segments(rel_segments[1:], pattern_segments[1:])
    return False


def glob_files(sandbox: WorkspaceSandbox, pattern: str,
               path: Optional[str] = None) -> dict[str, Any]:
    """按 glob 模式查找文件（支持 ** 递归，如 **/*.java）。"""
    base = sandbox.resolve(path)
    if not base.is_dir():
        return {"error": f"目录不存在: {path or '.'}"}
    results = []
    try:
        for f in _walk_files(base):
            rel = f.relative_to(base)
            if _match_glob(rel.as_posix(), f.name, pattern):
                results.append(sandbox.relative(f))
                if len(results) >= MAX_RESULTS:
                    break
    except PermissionError:
        pass
    return {"pattern": pattern, "matches": results, "count": len(results)}


def grep_files(sandbox: WorkspaceSandbox, pattern: str,
               path: Optional[str] = None,
               glob: Optional[str] = None) -> dict[str, Any]:
    """按正则搜索文件内容，返回匹配行。"""
    base = sandbox.resolve(path)
    if not base.is_dir():
        return {"error": f"目录不存在: {path or '.'}"}
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return {"error": f"正则表达式非法: {e}"}

    matches = []
    try:
        for f in _walk_files(base):
            if f.suffix.lower() in SKIP_EXTS:
                continue
            if glob and not (fnmatch.fnmatch(f.name, glob)
                             or fnmatch.fnmatch(f.relative_to(base).as_posix(), glob)):
                continue
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                    for line_no, line in enumerate(fh, start=1):
                        if rx.search(line.rstrip("\n")):
                            matches.append({
                                "path": sandbox.relative(f),
                                "line": line_no,
                                "text": line.rstrip("\n")[:500],
                            })
                            if len(matches) >= MAX_RESULTS:
                                return {"pattern": pattern, "matches": matches,
                                        "count": len(matches), "truncated": True}
            except OSError:
                continue
    except PermissionError:
        pass
    return {"pattern": pattern, "matches": matches, "count": len(matches)}
