"""JSON-lines over stdio 服务器：Java 核心引擎的执行层。

协议：
  请求: {"id": 1, "tool": "read_file", "params": {"path": "x"}}
  响应: {"id": 1, "ok": true, "result": {...}}
    或 {"id": 1, "ok": false, "error": "..."}
  关闭: {"shutdown": true}

启动方式（由 Java 核心引擎拉起）：
  python -u -m qwen_agent_runtime.server --workspace <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Any, Callable

from .security import SecurityError, WorkspaceSandbox
from .tools import (
    edit_file,
    get_workspace_info,
    glob_files,
    grep_files,
    list_dir,
    read_file,
    run_command,
    write_file,
)
from .tools.todo_tools import todo_list, todo_write

# 工具名 -> (函数, 是否需要沙箱)
_TOOL_TABLE: dict[str, Callable[..., dict[str, Any]]] = {
    "list_dir": list_dir,
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "glob": glob_files,
    "grep": grep_files,
    "run_command": run_command,
    "get_workspace_info": get_workspace_info,
    "todo_write": todo_write,
    "todo_list": todo_list,
}


def _safe_params(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    return params


def dispatch(sandbox: WorkspaceSandbox, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    """分发工具调用，返回结果字典（内部约定：error 键表示业务失败）。"""
    if tool == "ping":
        return {"pong": True, "workspace": str(sandbox.root)}

    func = _TOOL_TABLE.get(tool)
    if func is None:
        return {"error": f"未知工具: {tool}，可用: {sorted(_TOOL_TABLE)}"}

    if tool in ("list_dir", "get_workspace_info"):
        result = func(sandbox)
    else:
        # 文件/检索/shell 工具统一第一参数为沙箱
        result = func(sandbox, **_safe_params(params))

    if not isinstance(result, dict):
        return {"result": result}
    return result


def _emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def serve(workspace: str) -> int:
    sandbox = WorkspaceSandbox(workspace)
    _emit({"event": "ready", "workspace": str(sandbox.root)})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            _emit({"ok": False, "error": "非法 JSON 请求"})
            continue

        if isinstance(req, dict) and req.get("shutdown"):
            _emit({"ok": True, "result": "shutdown"})
            return 0

        req_id = req.get("id") if isinstance(req, dict) else None
        tool = req.get("tool") if isinstance(req, dict) else None
        params = _safe_params(req.get("params") if isinstance(req, dict) else None)

        try:
            result = dispatch(sandbox, tool, params)
            if isinstance(result, dict) and "error" in result:
                _emit({"id": req_id, "ok": False, "error": str(result["error"])})
            else:
                _emit({"id": req_id, "ok": True, "result": result})
        except SecurityError as e:
            _emit({"id": req_id, "ok": False, "error": f"安全拦截: {e}"})
        except Exception as e:  # noqa: BLE001 - 服务器必须兜底所有异常
            _emit({"id": req_id, "ok": False, "error": f"内部错误: {e}"})
            traceback.print_exc(file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qwen_agent_runtime.server")
    parser.add_argument("--workspace", required=True,
                        help="工作区根目录（沙箱边界）")
    args = parser.parse_args(argv)
    return serve(args.workspace)


if __name__ == "__main__":
    sys.exit(main())
