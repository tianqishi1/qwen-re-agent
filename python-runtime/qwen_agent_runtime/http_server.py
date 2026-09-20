"""Qwencode Python 执行层 HTTP 微服务。

将原 stdio JSON-lines 服务器升级为独立 HTTP 微服务（executor-service），
供 agent-service（Spring Boot）通过 HTTP 调用，实现真正意义的微服务拆分。

协议：
  POST /tool   body: {"tool": "read_file", "params": {...}}
               resp: {"ok": true, "result": {...}}
                 或 {"ok": false, "error": "..."}
  GET  /health -> {"ok": true, "workspace": "..."}
  POST /shutdown -> {"ok": true}（优雅退出）

用法：python -u -m qwen_agent_runtime.http_server --workspace <dir> --port 8910
"""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .security import SecurityError, WorkspaceSandbox
from .server import _TOOL_TABLE, _safe_params, dispatch


class ExecutorHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    sandbox: WorkspaceSandbox | None = None  # 由 serve 注入

    # ------------------------------------------------------------------
    # HTTP 路由
    # ------------------------------------------------------------------

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            req = json.loads(body or b"{}")
        except json.JSONDecodeError:
            self._respond(400, {"ok": False, "error": "非法 JSON"})
            return

        if path == "/tool":
            self._handle_tool(req)
        elif path == "/shutdown":
            self._respond(200, {"ok": True, "result": "shutdown"})
            # 延迟到响应发出后退出
            import threading
            threading.Thread(target=sys.exit, args=(0,), daemon=True).start()
        else:
            self._respond(404, {"ok": False, "error": f"未知路径: {path}"})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            sb = self.sandbox
            self._respond(200, {
                "ok": True,
                "workspace": str(sb.root) if sb else None,
                "tools": sorted(_TOOL_TABLE),
            })
        else:
            self._respond(404, {"ok": False, "error": f"未知路径: {path}"})

    # ------------------------------------------------------------------
    # 工具分发
    # ------------------------------------------------------------------

    def _handle_tool(self, req: dict[str, Any]) -> None:
        tool = req.get("tool")
        params = _safe_params(req.get("params"))
        sb = self.sandbox
        if sb is None:
            self._respond(500, {"ok": False, "error": "执行层未初始化"})
            return
        try:
            result = dispatch(sb, tool, params)
            if isinstance(result, dict) and "error" in result:
                self._respond(200, {"ok": False, "error": str(result["error"])})
            else:
                self._respond(200, {"ok": True, "result": result})
        except SecurityError as e:
            self._respond(200, {"ok": False, "error": f"安全拦截: {e}"})
        except Exception as e:  # noqa: BLE001 - 服务器必须兜底所有异常
            import traceback
            traceback.print_exc(file=sys.stderr)
            self._respond(500, {"ok": False, "error": f"内部错误: {e}"})

    def _respond(self, status: int, obj: dict[str, Any]) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[executor] %s\n" % (fmt % args))


def serve(workspace: str, port: int, host: str = "127.0.0.1") -> int:
    sandbox = WorkspaceSandbox(workspace)
    # 类属性注入沙箱（每请求共享同一沙箱实例）
    ExecutorHandler.sandbox = sandbox
    server = ThreadingHTTPServer((host, port), ExecutorHandler)
    print(f"[executor] Python 执行层 HTTP 微服务: http://{host}:{port}"
          f"  工作区: {sandbox.root}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qwen_agent_runtime.http_server")
    parser.add_argument("--workspace", required=True,
                        help="工作区根目录（沙箱边界）")
    parser.add_argument("--port", type=int, default=8910,
                        help="监听端口（默认 8910）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址（默认 127.0.0.1，仅本机）")
    args = parser.parse_args(argv)
    return serve(args.workspace, args.port, args.host)


if __name__ == "__main__":
    sys.exit(main())
