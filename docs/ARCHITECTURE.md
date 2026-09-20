# Qwencode 编码 Agent —— 架构设计文档

> 版本：0.3.0 ｜ 日期：2026-09-20 ｜ 状态：已实现并通过端到端验证

---

## 1. 背景与目标

**上游**：Qwen Code（`D:\agent\qwen-code`）是 TypeScript monorepo 编码 Agent。
其中 `packages/sdk-java/qwencode` 发布 `com.alibaba:qwencode-sdk`，仅提供
Java 客户端能力：连接 Qwen Code daemon（REST+SSE）或 legacy CLI 进程（stdio），
负责协议编解码、会话封装与传输，**不包含 agent 引擎**。

**本次重构目标**：将 qwencode 从一个"传输客户端"升级为**自驱动的编码 Agent**，
采用 **Java-Python 双栈架构**：

- **Java** 承担核心引擎：agent 循环、LLM 网关、工具注册与委派、权限决策、
  会话管理、CLI。选择 Java 的原因：类型安全、并发原语成熟（线程/锁/超时）、
  与上游 `qwencode-sdk` 的 Java 生态一脉相承、JDK 8 即可构建。
- **Python** 承担执行层：文件系统操作、代码检索、shell 执行。选择 Python 的
  原因：文本处理与文件系统 API 简洁、正则与 glob 语义完整、无需编译即可扩展
  新工具、与上游 `packages/sdk-python` 生态对齐。

**非目标**：不重写上游全部能力（桌面端、浏览器扩展、多 IM 渠道、MCP 等）。
聚焦"编码 Agent 最小闭环"：**读码 → 检索 → 修改 → 执行验证 → 汇报**。

---

## 2. 顶层架构

```
用户输入 (CLI / -p)
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ Java 核心引擎 (qwencode-agent.jar)                      │
│                                                        │
│  Main ──► AgentLoop ──► ChatClient (LLM 网关)          │
│              │  ▲          │                           │
│              ▼  │          ▼                           │
│          ToolRegistry  ChatResponse (content/tool_calls)│
│              │                                          │
│      ┌───────┼───────────────┐                          │
│      ▼       ▼               ▼                          │
│ Permission  PythonBridge  Session                       │
│ Manager     (子进程桥)       (上下文预算)                │
└──────────────┼─────────────────────────────────────────┘
               │ JSON-lines over stdio (UTF-8)
               ▼
┌────────────────────────────────────────────────────────┐
│ Python 执行层 (qwen_agent_runtime)                       │
│                                                        │
│  server.py (JSON-lines 分发)                            │
│    ├── security.py  路径沙箱 + 危险命令黑名单             │
│    └── tools/       file / search / shell / workspace   │
└────────────────────────────────────────────────────────┘
```

### 2.1 控制流（agent 循环）

```
1. 组装 history = session.messages + system prompt + user prompt
2. 调用 ChatClient.chat(history, toolDefinitions)
3. 解析响应：
   a. 无 tool_calls → 输出最终回答，结束
   b. 有 tool_calls → 逐个执行：
      - PermissionManager.check(tool, mutating, params)   // 权限决策
      - 放行 → ToolRegistry.invoke → PythonBridge.call → Python 工具执行
      - 结果作为 tool 消息回填 history 与 session
4. 回到 2，直到无工具调用或达到 maxIterations
```

### 2.2 进程模型

- Java 主进程通过 `ProcessBuilder` 启动 Python 子进程：
  `python -u -m qwen_agent_runtime.server --workspace <dir>`
- 通信：Java 写 stdin（每行一个 JSON 请求），Python 写 stdout（每行一个 JSON
  响应）；`PYTHONPATH` 注入 `python-runtime` 目录；`PYTHONIOENCODING=utf-8`。
- Java 侧 `PythonBridge` 维护：请求 id 自增、`ConcurrentHashMap<Long, PendingCall>`
  等待队列、单线程读循环、每调用独立超时（默认 120s）、关闭时向子进程发
  `{"shutdown": true}` 并强杀兜底。

---

## 3. 模块设计

### 3.1 Java 核心引擎（`com.alibaba.qwen.code.agent`）

| 模块 | 类 | 职责 |
|------|-----|------|
| CLI | `Main` | 参数解析、UTF-8 stdio 包装、交互 REPL 与非交互 `-p` 模式 |
| config | `AgentConfig` | 配置装配（参数 > 环境变量 > 默认值），构建期校验 |
| core | `AgentLoop` | agent 主循环、工具结果回填、迭代上限 |
| core | `Session` | 消息历史、字符级上下文预算（240k）、LRU 式裁剪 |
| core | `Message` / `ToolCall` | OpenAI 兼容消息与工具调用模型 |
| llm | `ChatClient` | HTTP(S) 网关：非流式 + SSE 流式、工具调用增量聚合 |
| llm | `ToolDefinition` | 工具 schema 构造（properties/required/enum） |
| llm | `ChatResponse` | 响应封装（content/toolCalls/usage） |
| tools | `ToolRegistry` | 工具定义注册、arguments 解析、委派桥（ToolBridge）、结果渲染 |
| bridge | `ToolBridge` | 桥抽象接口（call/ping/newToolRegistry/pid/close） |
| bridge | `PythonBridge` | 子进程生命周期、JSON-lines 协议、超时与异常（CLI 模式） |
| bridge | `PythonHttpBridge` | HTTP 调用 executor-service（微服务模式） |
| permission | `PermissionManager` | 三种权限模式 + 危险命令黑名单（Java 侧第一道闸） |

**流式工具调用聚合**（`ChatClient.accumulateToolCalls`）：SSE 分片按
`index` 聚合（OpenAI 协议中同一 tool_call 分片共享 index，仅首帧带 id），
arguments 增量拼接；最终按 index 序组装 `ToolCall(id, name, arguments)`。

### 3.2 Python 执行层（`qwen_agent_runtime`）

| 模块 | 职责 |
|------|------|
| `server.py` | stdin/stdout JSON-lines 分发；`ping` 健康检查；异常兜底 |
| `security.WorkspaceSandbox` | 路径解析 + `resolve().relative_to(root)` 沙箱校验 |
| `security.detect_dangerous_command` | 17 条危险命令正则黑名单（第二道闸） |
| `tools/file_tools.py` | `list_dir` / `read_file`（行范围+截断）/ `write_file` / `edit_file`（唯一匹配） |
| `tools/search_tools.py` | `glob`（支持 `**` 递归）/ `grep`（正则、跳过二进制与噪音目录） |
| `tools/shell_tools.py` | `run_command`（危险拦截、超时 60~300s、输出 30k 截断、cwd 沙箱） |
| `tools/workspace_tools.py` | `get_workspace_info`（OS/Python 版本/工作区根） |

**双保险原则**：危险命令在 Java 侧（展示前）与 Python 侧（执行前）各拦截一次；
路径越界在 Python 侧唯一强制（执行点最近）。

---

## 4. 桥接协议

### 4.1 请求（Java → Python，每行一个 JSON）

```jsonc
{"id": 1, "tool": "read_file", "params": {"path": "src/a.java", "start_line": "1", "end_line": "50"}}
```

### 4.2 响应（Python → Java）

```jsonc
{"id": 1, "ok": true,  "result": {"total_lines": 42, "content": "..."}}
{"id": 1, "ok": false, "error": "路径越界（工作区外）: ..."}
```

### 4.3 启动与关闭

```jsonc
// 启动后 Python 先发 ready 事件
{"event": "ready", "workspace": "D:\\..."}
// Java 关闭时发送
{"shutdown": true}
```

---

## 5. 权限模型

```
                    ┌─────────────────────────────────────┐
                    │        PermissionManager            │
  工具调用           │                                     │
 ───────────►       │  1. run_command → 危险命令黑名单硬拦截 │
                    │  2. 模式判断：                       │
                    │     AUTO   → 放行                    │
                    │     PLAN   → 只读放行，修改类询问     │
                    │     APPROVE→ 全部询问                │
                    └─────────────────────────────────────┘
```

- **Java 侧黑名单**：`PermissionManager.DangerousCommands`（正则集合，覆盖
  `rm -rf /`、`format`、`curl|sh`、`sudo rm`、SSH 密钥删除等）。
- **Python 侧黑名单**：`security._DANGEROUS_PATTERNS`（与 Java 侧同源策略，
  17 条规则）。双端独立实现，防单点绕过。

---

## 6. 错误处理与边界

| 场景 | 行为 |
|------|------|
| LLM 网关 4xx/5xx | `LlmException`，CLI 打印错误并 exit 4 |
| LLM 网关连接失败 | `LlmException`，同上 |
| Python 启动失败 | exit 3，提示检查 `--python` 与 runtime 路径 |
| 工具执行超时（>120s） | `BridgeTimeoutException` → tool 消息回填"超时" |
| 工具返回业务错误 | `ok:false` → tool 消息回填 error 文本 |
| 用户拒绝工具（plan/approve） | tool 消息回填"用户拒绝"，模型需调整方案 |
| 上下文超预算 | `Session` 从最早 USER 消息裁剪（保留 system） |
| 命令输出过长 | Python 截断 30k 字符并标注 truncated |

---

## 7. 构建与运行

- 构建：`scripts\build.cmd`（portable Maven 3.9.9 + JDK 8，父 POM reactor：
  `java-core` shade 可执行 jar + `agent-service` Spring Boot fat jar）
- CLI 运行：`scripts\run.cmd [选项]`（`chcp 65001` + jar，stdio 子进程执行层）
- Web 运行：`scripts\start-service.cmd` / `scripts\stop-service.cmd`（见第 10 章）
- 测试：
  - Java：`mvn -f java-core/pom.xml test`（21 个：ConfigFile 5 + Core 8 + LoopGuard 7 + 桥联调 1）
  - Python：`python -m unittest discover -s tests`（13 个用例，含协议冒烟）
  - 端到端：`scripts\mock_llm.py` 本地 mock 网关 + 真实 agent 循环（读→写→改→总结）

---

## 8. 编码工作流（六阶段 Skill）

> 版本：0.3.0 ｜ 新增：2026-09-19

用户输入一个想法，`WorkflowOrchestrator` 自动驱动完整软件生命周期。
每个阶段 = 一个 Skill = 一个独立子代理。

### 8.1 阶段流水线

```
用户想法
   │
   ▼
┌────────────────────────────────────────────────────────────┐
│ WorkflowOrchestrator（com.alibaba.qwen.code.agent.workflow）│
│                                                            │
│ ① requirement-analysis  需求拆解 → docs/requirements.md     │
│ ② tech-design          技术方案 → docs/design.md            │
│ ③ coding               开发     → 业务代码                  │
│ ④ testing              测试     → docs/test-report.md       │
│ ⑤ deployment           上线     → docs/deploy.md            │
│ ⑥ operations           运维     → docs/ops.md               │
│                                                            │
│ 上下文跨阶段传递：上一阶段产出物 → 下一阶段输入               │
└────────────────────────────────────────────────────────────┘
```

### 8.2 关键类

| 类 | 职责 |
|----|------|
| `workflow/Skill.java` | 阶段定义：name/description/systemPrompt/allowedTools/disallowedTools/artifacts/maxTimeMinutes/maxTurns，builder 模式 |
| `workflow/BuiltinSkills.java` | 6 个内置阶段 skill 注册表 + 标准流水线顺序 `PIPELINE` |
| `workflow/LoopGuard.java` | 死循环/停滞防护（见 8.4） |
| `workflow/WorkflowOrchestrator.java` | 顺序执行、每阶段子代理实例化、上下文传递、结果汇总（StageResult/WorkflowResult） |
| `core/AgentLoop.java`（增强） | 阶段级 systemPrompt 覆盖、工具子集过滤、LoopGuard 接入、ToolCallTrace 输入输出记录 |
| `python-runtime/tools/todo_tools.py` | `todo_write` / `todo_list` 任务清单（对齐上游 todoWrite） |

### 8.3 阶段执行模型

```
executeStage(skill, context):
  1. 新建 Session + ToolRegistry + LoopGuard(stage)
  2. 组装阶段 prompt = skill.systemPrompt()
     + 上下文（用户想法 + 此前各阶段产出摘要）
     + "现在执行阶段「xxx」" 指令 + 产出物要求
  3. AgentLoop.run(stagePrompt)  // 独立子代理，maxTurns = skill.maxTurns()
  4. 汇总 StageResult{stage, success, summary, artifacts, costSeconds,
                     loopProtected, stalled}
  5. 若失败 → 终止流水线；否则 context += 本阶段产出，进入下一阶段
```

工具子集过滤：`AgentLoop.visibleTools()` 只向 LLM 暴露 allowedTools；
阶段外工具调用（如需求阶段调用 run_command）在 `toolVisible` 处拦截，
回填"该工具在当前阶段不可用"提示，不执行。

### 8.4 稳定性设计（对齐 qwen-code）

| qwen-code 上游 | 本实现 | 语义 |
| -------------- | ------ | ---- |
| `tools/tool-call-repeat-key.ts` | `LoopGuard#repeatKey` | (工具名, 规范化参数) → sha256 指纹。规范化：对象键排序、数组保序、数字整数化，防字段重排绕过 |
| 重复调用检测 | `LoopGuard#recordCall` | 同一阶段内相同指纹 ≥3 次 → 拦截 + 注入 `fallbackHint()`（"请停止重复，改用不同策略"） |
| `agents/runtime/workflow-stall.ts` | `LoopGuard#recordTurn` | 连续 4 轮无文本且无工具执行 → 注入 `stallHint()` |
| `subagents/types.ts` maxTurns | `Skill.maxTurns` | 每阶段轮次上限（15~40） |
| SubagentConfig max_time_minutes | `Skill.maxTimeMinutes` | 阶段超时（5~10 min） |
| `tools/todoWrite.ts` | `todo_write` / `todo_list` | 任务清单持久化 .qwen/todos.json |

`repeatKey` 关键细节（对齐上游注释语义）：
- 对象键排序 → 字段顺序不同但语义相同 → 同一指纹（防重排绕过）；
- 数组保序 → 顺序有语义时不被误合并；
- 数字整数化（60 与 60.0）→ 数值等价不产生额外指纹；
- 大 payload（如 write_file content）以定长摘要参与指纹，不存原文。

工具调用链路（输入 → 输出 trace）：

```
LLM 决策 → 工具调用（name + args）
  → 权限检查（PermissionManager）
  → 重复检测（LoopGuard.recordCall）
  → Python 执行（PythonBridge.call，超时 120s）
  → 结果回填 tool 消息（success/error + 内容）
  → ToolCallTrace{name, args, success, resultPreview, costMs}
```

---

## 9. 已知限制与后续方向

1. **上下文预算**：当前为字符级近似裁剪，未做真实 token 计费；大文件读取靠
   Python 侧行数截断兜底。工作流 token 预算（对齐 workflow-budget.ts）未实现。
2. **工具集**：未实现 MCP、LSP、web 搜索等上游高级能力；工具以 Python 单文件扩展，
   新增工具只需注册到 `_TOOL_TABLE`。
3. **并发**：单会话串行；`PythonBridge` 已支持并发 pending 表，未来可开多会话；
   工作流并发窗口（对齐 workflow-orchestrator 的 parallel/pipeline fan-out）未实现。
4. **模型兼容**：已验证 OpenAI 兼容协议（含流式工具调用）；Anthropic/Gemini
   协议未实现（上游通过 contentGenerator 适配，本仓库预留扩展点）。
5. **会话持久化**：当前内存会话；上游支持会话恢复/快照，后续可序列化 `Session`。
6. **工作流人工确认点**：当前全自动执行；后续可引入 plan mode 语义
   （enterPlanMode/exitPlanMode），在方案/开发阶段前暂停等待用户确认。

---

## 10. 微服务化拆分（agent-service + executor-service）

> 版本：0.3.0 ｜ 新增：2026-09-20

第 2 章的单进程模型（CLI + stdio 子进程）演化为三组件微服务拓扑：

```
浏览器（前端页面 index.html / app.js / style.css，http://127.0.0.1:8800/）
   │  REST + SSE（fetch / ReadableStream 解析）
   ▼
agent-service (Spring Boot 2.7.5, JDK 8, 端口 8800, 仅监听 127.0.0.1)
   ├─ controller/   Chat / Workflow / File / Meta
   ├─ service/core/ AgentEngine（装配 ChatClient + ToolRegistry + 编排器）
   ├─ service/session/ SessionManager（会话注册表，会话内串行）
   ├─ service/executor/ ExecutorManager（executor 生命周期管理）
   └─ AgentEventListener → SSE 事件流
   │  HTTP（POST /tool · GET /health · POST /shutdown）
   ▼
executor-service (python-runtime/qwen_agent_runtime/http_server.py, 端口 8910)
   └─ ThreadingHTTPServer → dispatch(sandbox, tool, params)（复用 stdio 模式全部工具）
```

### 10.1 关键设计

| 关注点 | 设计 | 说明 |
| ------ | ---- | ---- |
| 执行层解耦 | `bridge/ToolBridge` 接口 | `PythonBridge`（stdio 子进程，CLI）与 `PythonHttpBridge`（HTTP，微服务）可互换；`ToolRegistry` 只依赖接口 |
| 会话模型 | `SessionManager` + `AgentSession` | 每会话独立 `Session` + `AgentLoop` + `ToolRegistry`（共享同一 HTTP 桥）；会话内 `tryAcquire` 串行，跨会话并行 |
| 流式推送 | `core/AgentEventListener` | `AgentLoop` / `WorkflowOrchestrator` 在工具调用、阶段切换处回调；agent-service 转发为 SSE 事件（`tool_call` / `tool_result` / `text` / `stage` / `done`） |
| executor 生命周期 | `ExecutorManager` | auto-start 模式：拉起 `python -m qwen_agent_runtime.http_server` → 轮询 `/health` 就绪 → 关闭时 `POST /shutdown` + destroy；外部模式：仅连接 |
| 安全边界 | 双沙箱 | Web 服务仅监听 127.0.0.1；文件浏览 API 路径限制在工作区内（禁 `..`、绝对路径）；executor 沿用路径沙箱 + 危险命令黑名单 |
| 并发 | 线程池 | agent-service 用固定线程池执行 AgentLoop（默认 4），SSE emitter 非阻塞 |

### 10.2 SSE 事件契约

```
POST /api/chat/stream {message, sessionId?}
  event: tool_call      data: {"tool":"read_file","args":"{...}"}
  event: tool_result    data: {"tool":"read_file","success":true,"costMs":14,"preview":"..."}
  event: text           data: "最终回复文本"
  event: done           data: {"ok":true,"sessionId":"...","reply":"...","toolCalls":[...]}

POST /api/workflow/stream {idea, stages?}
  event: stage          data: {"stage":"requirement-analysis","status":"stage_start|stage_end|stage_failed","detail":"..."}
  event: tool_call / tool_result   （同上）
  event: done           data: {"ok":true,"stages":[{"stage":"...","success":true,"artifacts":[...],"costSeconds":N}]}
```

### 10.3 与 CLI 模式的关系

- 两者共享同一个 `java-core` 引擎与 `python-runtime` 工具集；
- CLI：`PythonBridge`（stdio），单进程即可运行；
- Web：`PythonHttpBridge`（HTTP），`ExecutorManager` 自动拉起 executor；
- 测试基线共用：`mvn -f java-core/pom.xml test`（21）+ `python -m unittest discover -s tests`（13）。

### 10.4 启动与验证

```
scripts\build.cmd                 # 父 POM reactor：java-core + agent-service
scripts\start-service.cmd         # 启动 8800（自动拉起 8910）
scripts\stop-service.cmd          # 停止 8800 + 8910
curl http://127.0.0.1:8800/api/health
curl -X POST http://127.0.0.1:8800/api/chat -H "Content-Type: application/json" -d "{\"message\":\"hi\",\"sessionId\":null}"
```
