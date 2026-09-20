# Qwencode 编码 Agent（Java-Python 双栈 · 微服务版）

将 **qwencode**（原 Qwen Code `packages/sdk-java/qwencode`，仅 Java 传输客户端）
重构为一个**真正自驱动的编码 Agent**，并拆分为 **Spring Boot 微服务 + 前端客户端**：

- **Java 核心引擎**（agent-core）：agent 循环、LLM 网关、工具编排、权限、六阶段工作流、稳定性防护；
- **Python 执行层**（executor-service）：文件 / 检索 / shell / todo 工具，作为独立 HTTP 微服务部署；
- **Web 服务**（agent-service，Spring Boot）：REST + SSE API，内置前端页面（聊天 / 工作流 / 文件浏览）。

> 参考上游：[QwenLM/qwen-code](https://github.com/QwenLM/qwen-code)（源码位于 `D:\agent\qwen-code`）
> 本仓库是独立实现，复用上游的 agent 循环、工具、权限、工作流、子代理、稳定性（循环防护 / 停滞看门狗）等设计思想，
> 代码全部以 Java 8 + Python 3 重写，不依赖 Node.js。

---

## 架构总览（微服务）

```
┌──────────────────────────────────────────────────────────────────┐
│  浏览器（前端页面：聊天 / 工作流 / 文件浏览）                       │
│  http://127.0.0.1:8800/                                           │
└───────────────┬──────────────────────────────────────────────────┘
                │ REST + SSE
                ▼
┌──────────────────────────────────────────────────────────────────┐
│  agent-service  (Spring Boot 2.7.5 · 端口 8800 · JDK 8)           │
│                                                                  │
│  ChatController      /api/chat        /api/chat/stream   (SSE)   │
│  WorkflowController  /api/workflow/stages /api/workflow/stream   │
│  FileController      /api/files       /api/files/content         │
│  MetaController      /api/config      /api/health                │
│                                                                  │
│  SessionManager     会话注册表（每会话独立 AgentLoop + 历史）      │
│  AgentEngine        装配 ChatClient / ToolRegistry / 编排器       │
│  ExecutorManager    生命周期：自动拉起 / 健康检查 / 优雅停止       │
└───────────────┬──────────────────────────────────────────────────┘
                │ HTTP (POST /tool, GET /health)  —— 微服务间调用
                ▼
┌──────────────────────────────────────────────────────────────────┐
│  executor-service  (Python HTTP 微服务 · 端口 8910)               │
│  http_server.py    ThreadingHTTPServer 分发                       │
│  security.py       路径沙箱 + 危险命令黑名单                       │
│  tools/            file / search / shell / workspace / todo      │
└──────────────────────────────────────────────────────────────────┘
```

**核心引擎库**（agent-core = `java-core/`，Maven 模块，无 Spring 依赖）：

```
java-core/src/main/java/com/alibaba/qwen/code/agent/
├── Main.java             CLI 入口（REPL / -p / -w / --stage）
├── config/               AgentConfig / ConfigFile
├── core/                 AgentLoop / Session / Message / ToolCall / AgentEventListener
├── llm/                  ChatClient（OpenAI 兼容，流式 + function calling）
├── tools/                ToolRegistry（10 个工具）
├── bridge/               ToolBridge 接口
│   ├── PythonBridge      JSON-lines over stdio（CLI 模式）
│   └── PythonHttpBridge  HTTP 调用 executor-service（微服务模式）
├── permission/           PermissionManager
└── workflow/             WorkflowOrchestrator / Skill / BuiltinSkills / LoopGuard
```

**职责划分**

| 层               | 语言       | 职责                                            |
| --------------- | -------- | --------------------------------------------- |
| agent-core      | Java 8   | agent 循环、LLM 网关、工具调用协议、权限决策、会话、工作流编排、稳定性防护 |
| executor-service | Python 3 | 文件系统、代码检索、shell 执行、路径沙箱、危险命令防护、任务清单持久化 |
| agent-service   | Java 8 + Spring Boot | REST/SSE API、会话管理、executor 生命周期、前端页面托管 |

**双桥模式**：`ToolBridge` 接口抽象了执行层连接方式——
CLI 用 `PythonBridge`（stdio 子进程），微服务用 `PythonHttpBridge`（HTTP），
`ToolRegistry` 只依赖接口，两者可互换。

---

## 快速开始（Web 服务，推荐）

### 1. 构建

```
cd D:\agent\qwen-re-agent
scripts\build.cmd
```

或手动：

```
set JAVA_HOME=C:\Program Files\Java\jdk-1.8
tools\apache-maven-3.9.9\bin\mvn.cmd -f pom.xml package
```

产物：
- `java-core\target\qwencode-agent-0.2.0-alpha.jar`（引擎库）
- `agent-service\target\qwencode-agent-service-0.3.0.jar`（可执行 Spring Boot 服务）

### 2. 配置 API

编辑 `D:\agent\qwen-re-agent\qwencode.properties`：

```
api-key=sk-xxxxxxxx                # LLM API Key（必填）
base-url=https://api.deepseek.com  # OpenAI 兼容网关
model=deepseek-flash               # 模型名
permission=auto                    # auto | plan | approve
workspace=                         # 留空则使用 sample-workspace
max-iterations=20
python=python                      # Python 解释器命令
```

### 3. 启动服务

```
scripts\start-service.cmd
```

服务自动拉起 Python executor（端口 8910），等待数秒后：

```
scripts\stop-service.cmd           # 停止 8800 + 8910
```

打开浏览器访问 **http://127.0.0.1:8800/**：

| 面板 | 能力 |
| ---- | ---- |
| 💬 聊天 | 输入想法 → Agent 自主调用工具 → SSE 实时显示工具执行过程与回复（多轮会话） |
| 🔧 工作流 | 输入想法 → 勾选阶段 → 自动跑完需求/方案/开发/测试/上线/运维，实时阶段进度 + 产出物列表 |
| 📁 文件 | 浏览工作区目录树、查看文件内容（工作流产出物可直接跳转查看） |

### 4. REST / SSE API

| 方法 | 路径 | 说明 |
| ---- | ---- | ---- |
| POST | `/api/chat` | 非流式单轮回复 `{message, sessionId?}` → `{reply, sessionId, toolCalls}` |
| POST | `/api/chat/stream` | SSE 流式：`tool_call` / `tool_result` / `text` / `done` 事件 |
| GET  | `/api/workflow/stages` | 内置六阶段清单 |
| POST | `/api/workflow/stream` | SSE 工作流：`stage` / `tool_call` / `tool_result` / `done` 事件 |
| GET  | `/api/files?path=` | 列出目录 |
| GET  | `/api/files/content?path=` | 读取文件内容（沙箱内，越界拒绝） |
| GET  | `/api/config` | 引擎配置（模型 / 工作区 / 执行层地址） |
| GET  | `/api/health` | 健康检查（服务 + 执行层） |

SSE 示例（Python）：

```python
import urllib.request, json
req = urllib.request.Request("http://127.0.0.1:8800/api/chat/stream",
    data=json.dumps({"message": "读 sample.txt", "sessionId": None}).encode(),
    headers={"Content-Type": "application/json"}, method="POST")
print(urllib.request.urlopen(req, timeout=120).read().decode())
```

---

## 快速开始（CLI 模式）

CLI 不需要 Web 服务，直接驱动 stdio 子进程执行层：

```
# 非交互
scripts\run.cmd -c D:\agent\qwen-re-agent\sample-workspace -p "分析 sample.txt 并生成 summary.md"

# 交互 REPL
scripts\run.cmd -c D:\agent\qwen-re-agent\sample-workspace

# 工作流（输入想法 → 全流程）
scripts\run.cmd -c D:\agent\qwen-re-agent\sample-workspace -w -p "做一个待办清单命令行工具"

# 工作流部分阶段
scripts\run.cmd -c D:\agent\qwen-re-agent\sample-workspace -p "做一个待办清单命令行工具" --stage requirement-analysis,tech-design,coding
```

交互模式内可用 `/workflow` 命令：

```
> /workflow 做一个待办清单命令行工具
> /workflow 修复登录 bug | coding,testing
```

完整参数见 `java -jar qwencode-agent.jar -h`。配置查找顺序：
`--config PATH` > 环境变量 `QWEN_CONFIG` > 当前目录 `qwencode.properties` > jar 同目录 > `~/.qwencode.properties`。

---

## 编码工作流（六阶段 Skill）

用户输入一个想法，Agent 自动驱动完整软件生命周期。每个阶段 = 一个 Skill =
一个独立子代理（专属 system prompt + 工具子集 + 产出物 + 防护参数）：

```
用户想法
   │
   ▼
┌────────────────────────────────────────────────────────┐
│ WorkflowOrchestrator（Java）                            │
│                                                        │
│ ① requirement-analysis  需求拆解 → docs/requirements.md │
│ ② tech-design          技术方案 → docs/design.md        │
│ ③ coding               开发     → 业务代码              │
│ ④ testing              测试     → docs/test-report.md   │
│ ⑤ deployment           上线     → docs/deploy.md        │
│ ⑥ operations           运维     → docs/ops.md           │
│                                                        │
│ 每阶段：独立 AgentLoop + Skill prompt + 工具子集        │
│ 上下文跨阶段传递：上一阶段产出物 → 下一阶段输入           │
└────────────────────────────────────────────────────────┘
```

### 各阶段 Skill 定义

| 阶段 | 工具子集 | 产出物 | 轮次上限 | 超时 |
| ---- | ------- | ------ | ------- | ---- |
| requirement-analysis | 只读 + write_file（仅 docs/） | docs/requirements.md | 15 | 5 min |
| tech-design | 只读 + write_file（仅 docs/） | docs/design.md | 15 | 5 min |
| coding | 全部文件工具 | 业务代码 | 40 | 10 min |
| testing | 文件工具 + run_command | docs/test-report.md | 40 | 10 min |
| deployment | 文件工具 + run_command | docs/deploy.md | 30 | 10 min |
| operations | 文件工具 + run_command | docs/ops.md | 30 | 10 min |

只读阶段（需求/方案）语义对齐 qwen-code Explore 子代理：**只读 = 不改业务代码**，
但允许写自己的产出文档，禁止 edit_file 与 run_command。

---

## 稳定性设计（对齐 qwen-code）

| qwen-code 机制 | 本实现 | 说明 |
| -------------- | ------ | ---- |
| `tool-call-repeat-key.ts`（sha256 规范化指纹） | `workflow/LoopGuard.java#repeatKey` | 参数对象键排序、数组保序，字段顺序不同但语义相同的调用命中同一指纹 |
| 重复调用检测 | `LoopGuard#recordCall` | 同一阶段同一 (tool,args) 指纹 ≥3 次 → 拦截并注入回退提示 |
| 停滞看门狗（workflow-stall） | `LoopGuard#recordTurn` | 连续 4 轮无文本、无工具结果 → 注入停滞提示 |
| maxTurns（子代理轮次上限） | `Skill.maxTurns` | 每阶段独立上限（15~40） |
| max_time_minutes | `Skill.maxTimeMinutes` | 阶段超时（5~10 min） |
| SubagentConfig（prompt+工具子集） | `Skill` + `BuiltinSkills` | 每阶段独立 systemPrompt + allowed/disallowedTools |
| 工具调用输入输出 trace | `AgentLoop#ToolCallTrace` | 记录每次调用 name/args/result/耗时/成功与否 |
| 工具子集过滤 | `AgentLoop#toolVisible` | 阶段外工具调用返回"不可用"提示，不执行 |

### 工具调用链路（输入 → 思考 → 输出）

```
LLM 决策 → 工具调用（输入: name + args）
  → 权限检查（PermissionManager）
  → 重复检测（LoopGuard）
  → 执行（Python 执行层，带超时）
  → 结果回填（输出: success/error + 内容 + 耗时）
  → 记录 trace → 回到 LLM 继续
```

Web 端通过 `AgentEventListener`（`onToolCall` / `onToolResult` / `onText` / `onStage`）
把这条链路实时推送为 SSE 事件，前端逐条渲染。

---

## 权限模型

| 模式     | 行为                                            |
| ------ | --------------------------------------------- |
| `auto` | 只读与修改工具全部自动放行；危险命令（如 `rm -rf /`）仍被硬拦截       |
| `plan` | 只读工具自动放行；修改类工具（write/edit/run_command）询问用户 |
| `approve` | 每次工具调用都询问用户                               |

危险命令黑名单（Java 侧与 Python 侧双重防护）覆盖：删除根目录 / 主目录、格式化磁盘、
`curl|sh` 下载执行、权限提升、SSH 密钥删除等。Python 侧路径沙箱：所有文件操作解析后
必须位于工作区内部，越界即拒绝。Web 服务仅监听 `127.0.0.1`，文件浏览 API 同样受限沙箱。

---

## 内置工具

| 工具                   | 说明                        | 修改类 |
| -------------------- | ------------------------- | --- |
| `list_dir`           | 列举目录条目（名称 / 类型 / 大小）      | 否   |
| `read_file`          | 读取文件（可选行范围，长文件截断）         | 否   |
| `write_file`         | 写入 / 覆盖文件，自动建父目录          | 是   |
| `edit_file`          | 精确字符串替换（要求唯一匹配）           | 是   |
| `glob`               | 按 glob 模式查找文件（支持 `**`）    | 否   |
| `grep`               | 按正则搜索文件内容                 | 否   |
| `run_command`        | 在工作区内执行 shell 命令（超时 / 截断） | 是   |
| `get_workspace_info` | 工作区与运行时信息                 | 否   |
| `todo_write`         | 写入任务清单（replace/merge，对齐 qwen-code todoWrite） | 是   |
| `todo_list`          | 读取任务清单（持久化于 .qwen/todos.json） | 否   |

---

## 测试

```
rem Java 单元测试 + Java⇄Python 桥联调测试（当前 21 个）
cd D:\agent\qwen-re-agent\java-core
..\tools\apache-maven-3.9.9\bin\mvn.cmd test

rem Python 执行层测试（当前 13 个）
cd D:\agent\qwen-re-agent\python-runtime
python -m unittest discover -s tests -v
```

**端到端验证（不依赖真实 API key，用本地 mock LLM 网关）**：

```
start python -u scripts\mock_llm.py 8901
scripts\run.cmd --api-key sk-mock --base-url http://127.0.0.1:8901/v1 ^
  --model mock-model -w -p "做一个待办清单命令行工具"
```

**Web 服务端到端验证（真实 API key）**：

```
scripts\start-service.cmd
curl http://127.0.0.1:8800/api/health                       # UP + executorOk
curl "http://127.0.0.1:8800/api/files"                      # 工作区列表
curl -X POST http://127.0.0.1:8800/api/chat -H "Content-Type: application/json" ^
  -d "{\"message\":\"你好\",\"sessionId\":null}"            # 真实 LLM 回复
```

---

## 目录结构

```
D:\agent\qwen-re-agent\
├── pom.xml                        Maven 父工程（聚合 java-core + agent-service）
├── java-core/                     agent-core：核心引擎库（Maven 模块，无 Spring 依赖）
│   ├── pom.xml
│   └── src/main/java/com/alibaba/qwen/code/agent/
│       ├── Main.java              CLI 入口
│       ├── config/  core/  llm/  tools/  permission/  workflow/
│       └── bridge/                ToolBridge / PythonBridge / PythonHttpBridge
├── agent-service/                 agent-service：Spring Boot 微服务 + 前端
│   ├── pom.xml
│   └── src/main/
│       ├── java/.../service/
│       │   ├── QwenAgentApplication.java
│       │   ├── config/            AgentServiceConfig（装配）
│       │   ├── controller/        Chat / Workflow / File / Meta
│       │   ├── service/core/      AgentEngine
│       │   ├── service/session/   SessionManager / AgentSession
│       │   └── service/executor/  ExecutorManager（executor 生命周期）
│       └── resources/
│           ├── application.yml    （端口 8800 / executor 8910 / 工作区）
│           └── static/            前端页面 index.html / app.js / style.css
├── python-runtime/                executor-service：Python 执行层
│   ├── qwen_agent_runtime/
│   │   ├── server.py              JSON-lines 分发（CLI 模式）
│   │   ├── http_server.py         HTTP 微服务入口（executor-service）
│   │   ├── security.py            路径沙箱 + 危险命令黑名单
│   │   └── tools/                 file / search / shell / workspace / todo
│   └── tests/                     单元 + 协议冒烟测试
├── scripts/
│   ├── build.cmd                  构建脚本（父 POM reactor）
│   ├── run.cmd                    CLI 运行入口
│   ├── start-service.cmd          启动 Web 服务（8800，自动拉起 8910）
│   ├── stop-service.cmd           停止 8800 + 8910
│   └── mock_llm.py                本地 mock LLM 网关（六阶段状态机）
├── qwencode.properties            配置文件（api-key / base-url / model）
├── sample-workspace/              示例工作区
├── docs/ARCHITECTURE.md           架构设计文档
└── tools/apache-maven-3.9.9/      portable Maven（随项目分发）
```

---

## 与上游 qwen-code 的映射

| 上游（TypeScript）                       | 本仓库（Java/Python）                                                                     |
| ------------------------------------ | ------------------------------------------------------------------------------------ |
| `packages/core/src/agents` agent 循环  | `java-core/.../core/AgentLoop.java`                                                  |
| `packages/core/src/providers` LLM 网关 | `java-core/.../llm/ChatClient.java`                                                  |
| `packages/core/src/tools` 工具系统       | `java-core/.../tools/ToolRegistry.java` + `python-runtime/.../tools/`                |
| `packages/core/src/permissions` 权限   | `java-core/.../permission/PermissionManager.java` + `python-runtime/.../security.py` |
| `packages/core/src/agents/runtime/workflow-orchestrator.ts` | `java-core/.../workflow/WorkflowOrchestrator.java`                        |
| `packages/core/src/subagents/types.ts`（SubagentConfig） | `java-core/.../workflow/Skill.java` + `BuiltinSkills.java`                |
| `packages/core/src/tools/tool-call-repeat-key.ts` | `java-core/.../workflow/LoopGuard.java#repeatKey`                            |
| `packages/core/src/tools/todoWrite.ts`  | `python-runtime/.../tools/todo_tools.py` + `ToolRegistry` 注册                    |
| `packages/core/src/agents/runtime/workflow-stall.ts` | `LoopGuard#recordTurn`（停滞检测）                                      |
| `packages/sdk-java/qwencode` 传输客户端   | `java-core/.../bridge/PythonBridge.java` / `PythonHttpBridge.java`                     |
| `packages/cli` CLI                   | `java-core/.../Main.java`（交互 REPL + `-p` 非交互 + `-w` 工作流）                        |
| （无对应）Web 服务                      | `agent-service/`（Spring Boot REST + SSE + 前端页面）                                   |

---

## License

Apache 2.0（与上游 qwen-code 一致）。
