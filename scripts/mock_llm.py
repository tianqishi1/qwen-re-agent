"""本地 Mock LLM 网关（多阶段状态机）：模拟 OpenAI 兼容 /chat/completions。

用于端到端验证编码工作流六阶段流水线（不依赖真实 API key）：
requirement-analysis → tech-design → coding → testing → deployment → operations。

识别机制：
  - 从 system 消息中识别当前阶段（"阶段 [xxx]"）；
  - 每个阶段内部按"已收到的工具结果数"推进预置的工具调用序列；
  - 阶段末尾返回最终回答（阶段总结），编排器据此进入下一阶段。

用法：python mock_llm.py [port]
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# 每阶段预置的工具调用序列（按收到的 tool 结果数索引）
STAGE_SCRIPTS = {
    "requirement-analysis": [
        # 0: 探索工作区
        {"name": "list_dir", "arguments": {"path": "."}},
        # 1: 写需求文档
        {"name": "write_file", "arguments": {
            "path": "docs/requirements.md",
            "content": "# 需求文档\n\n## 目标\n实现一个待办清单命令行工具。\n\n"
                       "## 功能清单\n- 添加待办\n- 列出待办\n- 标记完成\n\n"
                       "## 验收标准\n- 支持 add/list/done 三个子命令\n- 数据持久化到 todos.json\n"}},
    ],
    "tech-design": [
        # 0: 读需求文档
        {"name": "read_file", "arguments": {"path": "docs/requirements.md"}},
        # 1: 写设计文档
        {"name": "write_file", "arguments": {
            "path": "docs/design.md",
            "content": "# 技术方案\n\n## 技术选型\nPython 标准库 + JSON 文件持久化。\n\n"
                       "## 模块划分\n- cli.py: 命令行入口\n- storage.py: 待办存取\n\n"
                       "## 数据模型\n{id, content, done, created_at}\n"}},
    ],
    "coding": [
        # 0: 读设计文档
        {"name": "read_file", "arguments": {"path": "docs/design.md"}},
        # 1: 写主程序
        {"name": "write_file", "arguments": {
            "path": "todo.py",
            "content": "#!/usr/bin/env python\n\"\"\"待办清单命令行工具。\"\"\"\n"
                       "import json\nimport sys\nfrom pathlib import Path\n\n"
                       "DB = Path('todos.json')\n\n"
                       "def load():\n    if not DB.exists():\n        return []\n"
                       "    return json.loads(DB.read_text(encoding='utf-8'))\n\n"
                       "def save(items):\n    DB.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')\n\n"
                       "def main():\n    args = sys.argv[1:]\n"
                       "    if not args or args[0] not in ('add', 'list', 'done'):\n"
                       "        print('用法: todo.py add|list|done')\n        return 1\n"
                       "    items = load()\n"
                       "    cmd = args[0]\n"
                       "    if cmd == 'add':\n        items.append({'content': ' '.join(args[1:]), 'done': False})\n"
                       "        save(items)\n        print('已添加')\n"
                       "    elif cmd == 'list':\n"
                       "        for i, it in enumerate(items):\n"
                       "            mark = '[x]' if it['done'] else '[ ]'\n"
                       "            print(f'{i} {mark} {it[\"content\"]}')\n"
                       "    elif cmd == 'done':\n"
                       "        idx = int(args[1])\n        items[idx]['done'] = True\n        save(items)\n"
                       "        print('已标记完成')\n"
                       "    return 0\n\n"
                       "if __name__ == '__main__':\n    sys.exit(main())\n"}},
    ],
    "testing": [
        # 0: 运行测试命令
        {"name": "run_command", "arguments": {"command": "python todo.py list"}},
        # 1: 写测试报告
        {"name": "write_file", "arguments": {
            "path": "docs/test-report.md",
            "content": "# 测试报告\n\n## 用例\n- add 添加待办：通过\n- list 列出待办：通过\n- done 标记完成：通过\n\n"
                       "## 结论\n全部用例通过，无遗留风险。\n"}},
    ],
    "deployment": [
        # 0: 构建/打包
        {"name": "run_command", "arguments": {"command": "python -m py_compile todo.py"}},
        # 1: 写部署文档
        {"name": "write_file", "arguments": {
            "path": "docs/deploy.md",
            "content": "# 部署方案\n\n## 构建\npython -m py_compile todo.py\n\n"
                       "## 部署步骤\n1. 拷贝 todo.py 到目标机\n2. 确保 Python 3.8+\n"
                       "3. 运行 python todo.py list\n\n## 回滚\n保留上一版本文件，直接替换即可。\n"}},
    ],
    "operations": [
        # 0: 读部署文档
        {"name": "read_file", "arguments": {"path": "docs/deploy.md"}},
        # 1: 写运维手册
        {"name": "write_file", "arguments": {
            "path": "docs/ops.md",
            "content": "# 运维手册\n\n## 启动\npython todo.py list\n\n"
                       "## 数据\n数据存储在 todos.json，注意备份。\n\n"
                       "## 故障排查\n- 命令不存在：检查 Python 环境\n- 数据丢失：恢复 todos.json 备份\n"}},
    ],
}

STAGE_SUMMARIES = {
    "requirement-analysis": "需求拆解完成：已产出 docs/requirements.md，覆盖目标/功能/验收标准。",
    "tech-design": "技术方案完成：已产出 docs/design.md，确定 Python 标准库方案与模块划分。",
    "coding": "开发完成：已实现 todo.py，支持 add/list/done 三个子命令。",
    "testing": "测试完成：已产出 docs/test-report.md，全部用例通过。",
    "deployment": "上线准备完成：已产出 docs/deploy.md，含构建/部署/回滚步骤。",
    "operations": "运维手册完成：已产出 docs/ops.md，含启动/数据/故障排查。",
}


def _current_stage(messages):
    """从 system/user 消息识别当前阶段（编排器在 user 消息中注入「阶段「xxx」」标记）。"""
    for m in messages:
        role = m.get("role")
        if role in ("system", "user"):
            content = str(m.get("content", ""))
            for stage in STAGE_SCRIPTS:
                if f"阶段「{stage}」" in content or f"[{stage}]" in content:
                    return stage
    return "requirement-analysis"


def _count_tool_results(messages):
    return sum(1 for m in messages if m.get("role") == "tool")


def _next_step(messages):
    """返回 (是否最终回答, 工具调用列表, 最终内容)。"""
    stage = _current_stage(messages)
    script = STAGE_SCRIPTS.get(stage, STAGE_SCRIPTS["requirement-analysis"])
    n = _count_tool_results(messages)
    if n < len(script):
        step = script[n]
        return False, [{
            "id": f"call_{stage}_{n}", "type": "function",
            "function": {"name": step["name"],
                         "arguments": json.dumps(step["arguments"], ensure_ascii=False)},
        }], None
    return True, [], STAGE_SUMMARIES.get(stage, f"{stage} 阶段完成。")


class MockLLMHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        messages = body.get("messages", [])
        final, tool_calls, content = _next_step(messages)
        stream = bool(body.get("stream"))
        if stream:
            self._respond_stream(final, tool_calls, content)
        else:
            self._respond_json(final, tool_calls, content)

    def _respond_json(self, final, tool_calls, content):
        resp = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": "mock-model",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "tool_calls": tool_calls or None,
                },
                "finish_reason": "stop" if final else "tool_calls",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        data = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def _respond_stream(self, final, tool_calls, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        chunks = []
        if final:
            text = content or ""
            half = max(1, len(text) // 2)
            chunks = [
                {"choices": [{"delta": {"role": "assistant", "content": ""}, "index": 0}]},
                {"choices": [{"delta": {"content": text[:half]}, "index": 0}]},
                {"choices": [{"delta": {"content": text[half:]}, "index": 0}]},
                {"choices": [{"delta": {}, "index": 0, "finish_reason": "stop"}]},
            ]
        else:
            tc = tool_calls[0]
            fn = tc["function"]
            args = fn.get("arguments", "")
            chunks.append({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": tc["id"], "type": "function",
                "function": {"name": fn["name"], "arguments": ""}}]}, "index": 0}]})
            mid = max(1, len(args) // 2)
            chunks.append({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": None, "type": None,
                "function": {"arguments": args[:mid]}}]}, "index": 0}]})
            chunks.append({"choices": [{"delta": {"tool_calls": [{
                "index": 0, "id": None, "type": None,
                "function": {"arguments": args[mid:]}}]}, "index": 0}]})
            chunks.append({"choices": [{"delta": {}, "index": 0, "finish_reason": "tool_calls"}]})

        for chunk in chunks:
            self.wfile.write(b"data: " + json.dumps(chunk, ensure_ascii=False).encode("utf-8") + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, fmt, *args):
        sys.stderr.write("[mock-llm] %s\n" % (fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8901
    server = HTTPServer(("127.0.0.1", port), MockLLMHandler)
    print(f"Mock LLM 网关运行于 http://127.0.0.1:{port}（六阶段状态机）", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
