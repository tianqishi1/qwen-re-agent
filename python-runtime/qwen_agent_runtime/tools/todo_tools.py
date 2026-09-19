"""任务清单工具：长流程/多阶段任务的状态跟踪。

对齐 qwen-code 的 todoWrite：维护一份用户可见的任务清单，
支持 pending / in_progress / completed 状态与 blockedBy 依赖。
清单持久化到工作区 .qwen/todos.json，跨阶段共享。
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ..security import WorkspaceSandbox

VALID_STATUS = {"pending", "in_progress", "completed"}


def _todos_path(sandbox: WorkspaceSandbox):
    qwen_dir = sandbox.root / ".qwen"
    qwen_dir.mkdir(parents=True, exist_ok=True)
    return qwen_dir / "todos.json"


def _load(sandbox: WorkspaceSandbox) -> list[dict[str, Any]]:
    p = _todos_path(sandbox)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save(sandbox: WorkspaceSandbox, todos: list[dict[str, Any]]) -> None:
    p = _todos_path(sandbox)
    p.write_text(json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize(items) -> tuple[list[dict[str, Any]], str | None]:
    if not isinstance(items, list):
        return [], "todos 必须是数组"
    out = []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            return [], f"第 {i + 1} 项不是对象"
        content = str(it.get("content", "")).strip()
        if not content:
            return [], f"第 {i + 1} 项缺少 content"
        status = str(it.get("status", "pending")).strip()
        if status not in VALID_STATUS:
            return [], f"第 {i + 1} 项 status 非法: {status}（可选 {sorted(VALID_STATUS)}）"
        out.append({
            "id": str(it.get("id", f"todo-{i + 1}")),
            "content": content,
            "status": status,
            "blockedBy": it.get("blockedBy") or [],
        })
    return out, None


def todo_write(sandbox: WorkspaceSandbox,
               todos: list[dict[str, Any]] | None = None,
               mode: str = "replace") -> dict[str, Any]:
    """写入任务清单。

    mode: replace（整体替换，默认）/ merge（合并，保留旧项+新增项）
    """
    if todos is None:
        return {"error": "缺少 todos 参数"}
    items, err = _normalize(todos)
    if err:
        return {"error": err}

    if mode == "merge":
        existing = _load(sandbox)
        by_id = {t.get("id"): t for t in existing}
        for it in items:
            by_id[it["id"]] = it  # 已存在 id 用新值覆盖（更新状态），新 id 追加
        items = list(by_id.values())
    elif mode != "replace":
        return {"error": f"mode 非法: {mode}（可选 replace/merge）"}

    _save(sandbox, items)
    active = sum(1 for t in items if t["status"] != "completed")
    return {
        "todos": items,
        "total": len(items),
        "active": active,
        "completed": len(items) - active,
        "message": f"任务清单已更新（共 {len(items)} 项，未完成 {active} 项）",
    }


def todo_list(sandbox: WorkspaceSandbox) -> dict[str, Any]:
    """读取当前任务清单。"""
    items = _load(sandbox)
    active = sum(1 for t in items if t["status"] != "completed")
    return {
        "todos": items,
        "total": len(items),
        "active": active,
        "completed": len(items) - active,
    }
