"""文件工具：目录列举、文件读取、写入、精确替换编辑。

所有路径都经过工作区沙箱校验。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from ..security import WorkspaceSandbox

# 单文件读取行数上限（防止超大文件撑爆上下文）
MAX_READ_LINES = 2_000
# 单行最大长度
MAX_LINE_LEN = 8_000


def _rel_path(sandbox: WorkspaceSandbox, p: Path) -> str:
    return sandbox.relative(p)


def list_dir(sandbox: WorkspaceSandbox, path: str | None = None) -> dict[str, Any]:
    """列举目录条目，返回 name/type/size 列表。"""
    target = sandbox.resolve(path)
    if not target.exists():
        return {"error": f"路径不存在: {path or '.'}"}
    if not target.is_dir():
        return {"error": f"不是目录: {path or '.'}"}
    entries = []
    try:
        names = sorted(os.listdir(target), key=lambda s: s.lower())
    except PermissionError as e:
        return {"error": f"无权限读取目录: {e}"}
    for name in names:
        child = target / name
        try:
            st = child.stat()
            entries.append({
                "name": name,
                "type": "dir" if child.is_dir() else "file",
                "size": st.st_size if child.is_file() else None,
                "path": _rel_path(sandbox, child),
            })
        except OSError:
            entries.append({"name": name, "type": "unknown", "size": None,
                            "path": _rel_path(sandbox, child)})
    return {"path": _rel_path(sandbox, target), "entries": entries}


def read_file(sandbox: WorkspaceSandbox, path: str,
              start_line: Optional[str] = None,
              end_line: Optional[str] = None) -> dict[str, Any]:
    """读取文件，可选行范围；内容过长时截断。"""
    target = sandbox.resolve(path)
    if not target.exists():
        return {"error": f"文件不存在: {path}"}
    if not target.is_file():
        return {"error": f"不是文件: {path}"}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except UnicodeDecodeError:
        return {"error": f"文件不是 UTF-8 文本（可能为二进制）: {path}"}
    except PermissionError as e:
        return {"error": f"无权限读取: {e}"}

    lines = text.splitlines()
    total = len(lines)
    start = 1
    end = total
    if start_line:
        try:
            start = max(1, int(start_line))
        except ValueError:
            return {"error": f"start_line 非法: {start_line}"}
    if end_line:
        try:
            end = min(total, int(end_line))
        except ValueError:
            return {"error": f"end_line 非法: {end_line}"}
    if start > end or start > total:
        return {"error": f"行范围无效: {start}-{end}（文件共 {total} 行）"}

    selected = lines[start - 1:end]
    truncated = False
    if len(selected) > MAX_READ_LINES:
        selected = selected[:MAX_READ_LINES]
        truncated = True
    # 行级内容截断
    cleaned = [ln if len(ln) <= MAX_LINE_LEN else ln[:MAX_LINE_LEN] + "...[截断]" for ln in selected]

    numbered = []
    for i, ln in enumerate(cleaned, start=start):
        numbered.append(f"{i:6d} | {ln}")

    return {
        "path": _rel_path(sandbox, target),
        "total_lines": total,
        "start_line": start,
        "end_line": min(end, start + len(selected) - 1),
        "truncated": truncated,
        "content": "\n".join(numbered),
    }


def write_file(sandbox: WorkspaceSandbox, path: str, content: str) -> dict[str, Any]:
    """写入文件（覆盖）。自动创建父目录。"""
    target = sandbox.resolve(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content or "", encoding="utf-8")
    except PermissionError as e:
        return {"error": f"无权限写入: {e}"}
    except OSError as e:
        return {"error": f"写入失败: {e}"}
    return {"path": _rel_path(sandbox, target), "bytes": len(content or ""),
            "message": "写入成功"}


def edit_file(sandbox: WorkspaceSandbox, path: str,
              old_text: str, new_text: str) -> dict[str, Any]:
    """精确字符串替换。old_text 必须恰好出现一次。"""
    target = sandbox.resolve(path)
    if not target.exists():
        return {"error": f"文件不存在: {path}"}
    try:
        text = target.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return {"error": f"文件不是有效 UTF-8: {path}"}
    except PermissionError as e:
        return {"error": f"无权限读取: {e}"}

    count = text.count(old_text)
    if count == 0:
        return {"error": "old_text 在文件中未找到，请核对内容（注意精确匹配含空白）。"}
    if count > 1:
        return {"error": f"old_text 出现 {count} 次，不是唯一匹配。请扩大上下文使匹配唯一。"}

    new_text = new_text if new_text is not None else ""
    updated = text.replace(old_text, new_text, 1)
    try:
        target.write_text(updated, encoding="utf-8")
    except PermissionError as e:
        return {"error": f"无权限写入: {e}"}
    except OSError as e:
        return {"error": f"写入失败: {e}"}
    return {
        "path": _rel_path(sandbox, target),
        "replaced_chars": len(old_text),
        "message": f"替换成功（{len(old_text)} 字符 -> {len(new_text)} 字符）",
    }
