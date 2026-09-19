# Qwencode 编码 Agent（Java-Python 双栈重构版）

将 **qwencode**（原 Qwen Code `packages/sdk-java/qwencode`，仅 Java 传输客户端）
重构为一个**真正自驱动的编码 Agent**：Java 核心引擎 + Python 执行层，
通过 JSON-lines over stdio 进程桥接，形成完整 agent 闭环。

> 参考上游：[QwenLM/qwen-code](https://github.com/QwenLM/qwen-code)（源码位于 `D:\agent\qwen-code`）
> 本仓库是独立实现，复用上游的 agent 循环、工具、权限、工作流、子代理等设计思想，
> 但代码全部以 Java 8 + Python 3 重写，不依赖 Node.js。

---

## 架构总览

```
┌──────────────────────────────────────────────────────────────┐
│  Java 核心引擎  (java-core, JDK 8)                           │
│                                                              │
│  Main (CLI: 交互 REPL / -p 非交互 / -w 工作流)                │
│    ├── AgentLoop          agent 主循环（LLM→工具→回填）        │
│    ├── ChatClient         LLM 网关（OpenAI 兼容/流式）         │
│    ├── ToolRegistry       工具注册与委派（10 个工具）          │
│    ├── PermissionManager  权限控制（auto/plan/approve）       │
│    ├── Session            会话与上下文预算                     │
│    ├── workflow/          WorkflowOrchestrator 六阶段编排     │
│    │   ├── LoopGuard      死循环/停滞防护（sha256 指纹）       │
│    │   ├── Skill          阶段定义（prompt+工具子集+产出物）    │
│    │   └── BuiltinSkills  6 个内置阶段 skill                  │
│    └── Session            会话                                 │
└───────────────┬──────────────────────────────────────────────┘
                │ JSON-lines over stdio（子进程）
                ▼
┌──────────────────────────────────────────────────────────────┐
│  Python 执行层  (python-runtime, Python 3)                    │
│                                                              │
│  server.py         JSON-lines 分发服务器                      │
│  security.py       路径沙箱 + 危险命令黑名单                   │
│  tools/                                                      │
│    file_tools.py    list_dir / read_file / write_file        │
│                     / edit_file                              │
│    search_tools.py  glob / grep                              │
│    shell_tools.py   run_command（超时/截断/沙箱）             │
│    workspace_tools.py  get_workspace_info                    │
│    todo_tools.py    todo_write / todo_list（任务清单）        │
└──────────────────────────────────────────────────────────────┘
```

**职责划分**

| 层          | 语言       | 职责                                     |
| ---------- | -------- | -------------------------------------- |
| Java 核心引擎  | Java 8   | agent 循环编排、LLM 网关、工具调用协议、权限决策、会话管理、CLI、工作流编排 |
| Python 执行层 | Python 3 | 文件系统操作、代码检索、shell 执行、路径沙箱、危险命令防护、任务清单持久化 |

**桥接协议**（每行一个 JSON）：

```
// 请求（Java → Python）
{"id": 1, "tool": "read_file", "params": {"path": "src/a.java"}}

// 响应（Python → Java）
{"id": 1, "ok": true, "result": {"total_lines": 42, "content": "..."}}
{"id": 1, "ok": false, "error": "路径越界（工作区外）..."}
```

---

## 快速开始

### 1. 构建

```
cd D:\agent\qwen编码agent
scripts\build.cmd
```

构建产物：`java-core\target\qwencode-agent-0.2.0-alpha.jar`

### 2. 配置 API

**推荐方式：编辑配置文件** `D:\agent\qwen编码agent\qwencode.properties`
（使用 DeepSeek 时只需填入 api-key）：

```
# LLM API Key（必填。DeepSeek 开放平台申请：https://platform.deepseek.com）
api-key=sk-xxxxxxxx

# OpenAI 兼容网关地址（DeepSeek 官方端点，无需 /v1 后缀）
base-url=https://api.deepseek.com

# 模型名称（DeepSeek: deepseek-flash / deepseek-v4-pro）
model=deepseek-flash

# 权限模式: auto | plan | approve
permission=auto

# 默认工作目录（留空则使用启动目录）
workspace=

# 最大工具迭代次数
max-iterations=20

# Python 解释器命令
python=python
```

配置文件查找顺序：`--config PATH` 参数 > 环境变量 `QWEN_CONFIG` > 当前目录 `qwencode.properties` > jar 同目录 > 用户主目录 `~/.qwencode.properties`。

也可以临时用环境变量或命令行参数覆盖：

```
set QWEN_API_KEY=sk-xxxxxxxx
rem 可选：set QWEN_BASE_URL=https://api.deepseek.com
rem 可选：set QWEN_MODEL=deepseek-flash
```

**优先级：命令行参数 > 环境变量 > 配置文件 > 默认值**

### 3. 运行

**非交互模式**（单次任务）：

```
scripts\run.cmd -c D:\agent\qwen编码agent\sample-workspace -p "分析 sample.txt 并生成 summary.md"
```

**交互模式**（多轮对话，REPL）：

```
scripts\run.cmd -c D:\agent\qwen编码agent\sample-workspace
```

**工作流模式**（输入想法 → 自动跑完需求/方案/开发/测试/上线/运维）：

```
scripts\run.cmd -c D:\agent\qwen编码agent\sample-workspace -w -p "做一个待办清单命令行工具"
```

工作流模式也可只跑部分阶段：

```
scripts\run.cmd -c D:\agent\qwen编码agent\sample-workspace -p "做一个待办清单命令行工具" --stage requirement-analysis,tech-design,coding
```

交互模式内可用 `/workflow` 命令：

```
> /workflow 做一个待办清单命令行工具
> /workflow 修复登录 bug | coding,testing        (只跑指定阶段)
```

### 4. 完整参数

```
用法: java -jar qwencode-agent.jar [选项] [-p "任务描述"]

  --api-key KEY        LLM API Key（或环境变量 QWEN_API_KEY / 配置文件 api-key）
  --base-url URL       OpenAI 兼容网关地址（默认 DashScope 兼容端点）
  --model NAME         模型名（默认 qwen3-coder-plus）
  -c, --cwd DIR        工作目录（默认当前目录）
  --permission MODE    auto | plan | approve（默认 auto）
  --max-iterations N   最大工具迭代次数（默认 20）
  --no-stream          关闭流式输出
  --python CMD         Python 解释器命令（默认 python）
  --config PATH        配置文件路径（默认自动查找 qwencode.properties）
  -p, --prompt TEXT    非交互模式，执行单次任务后退出
  -w, --workflow       以工作流模式运行（配合 -p 使用）
  --stage 阶段1,阶段2   只运行指定阶段（隐式开启工作流）
  -h, --help           显示帮助

环境变量: QWEN_API_KEY, QWEN_BASE_URL, QWEN_MODEL, QWEN_PERMISSION, QWEN_CONFIG
```

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

只读阶段（需求/方案）的语义对齐 qwen-code Explore 子代理：**只读 = 不改业务代码**，
但允许写自己的产出文档，禁止 edit_file 与 run_command。

---

## 稳定性设计（对齐 qwen-code）

| qwen-code 机制 | 本实现 | 说明 |
| -------------- | ------ | ---- |
| `tool-call-repeat-key.ts`（sha256 规范化指纹） | `workflow/LoopGuard.java#repeatKey` | 参数对象键排序、数组保序，字段顺序不同但语义相同的调用命中同一指纹，防模型重排参数绕过 |
| 重复调用检测 | `LoopGuard#recordCall` | 同一阶段同一 (tool,args) 指纹 ≥3 次 → 拦截并注入回退提示（"请停止重复，改用不同策略"） |
| 停滞看门狗（workflow-stall） | `LoopGuard#recordTurn` | 连续 4 轮无文本、无工具结果 → 注入停滞提示 |
| maxTurns（子代理轮次上限） | `Skill.maxTurns` | 每阶段独立上限（15~40） |
| max_time_minutes | `Skill.maxTimeMinutes` | 阶段超时（5~10 min） |
| SubagentConfig（prompt+工具子集） | `Skill` + `BuiltinSkills` | 每阶段独立 systemPrompt + allowed/disallowedTools |
| 工具调用输入输出 trace | `AgentLoop#ToolCallTrace` | 记录每次调用 name/args/result/耗时/成功与否 |
| 工具子集过滤 | `AgentLoop#toolVisible` | 阶段外工具调用返回"不可用"提示，不执行 |

### 工具调用链路（输入 → 思考 → 输出）

每个阶段内，AgentLoop 对每次工具调用执行完整闭环：

```
LLM 决策 → 工具调用（输入: name + args）
  → 权限检查（PermissionManager）
  → 重复检测（LoopGuard）
  → 执行（Python 执行层，带超时）
  → 结果回填（输出: success/error + 内容 + 耗时）
  → 记录 trace → 回到 LLM 继续
```

防护注入时机：
- 重复调用被拦截：注入 `LoopGuard#fallbackHint()` 作为 tool 消息回填；
- 停滞检测命中：注入 `stallHint()` 作为 user 消息。

---

## 权限模型

| 模式        | 行为                                          |
| --------- | ------------------------------------------- |
| `auto`    | 只读与修改工具全部自动放行；危险命令（如 `rm -rf /`）仍被硬拦截       |
| `plan`    | 只读工具自动放行；修改类工具（write/edit/run_command）询问用户 |
| `approve` | 每次工具调用都询问用户                                 |

危险命令黑名单（Java 侧与 Python 侧双重防护）覆盖：删除根目录 / 主目录、
格式化磁盘、`curl|sh` 下载执行、权限提升、SSH 密钥删除等。

Python 侧路径沙箱：所有文件操作解析后必须位于工作区内部，越界即拒绝。

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
rem Java 单元测试 + Java⇄Python 桥联调测试
cd D:\agent\qwen编码agent\java-core
..\tools\apache-maven-3.9.9\bin\mvn.cmd test

rem Python 执行层测试
cd D:\agent\qwen编码agent\python-runtime
python -m unittest discover -s tests -v
```

**端到端验证（不依赖真实 API key）**：

```
rem 启动本地 mock LLM 网关（六阶段状态机，模拟完整工作流工具调用链）
start python -u scripts\mock_llm.py 8901

rem 运行完整工作流（需求→方案→开发→测试→上线→运维）
scripts\run.cmd --api-key sk-mock --base-url http://127.0.0.1:8901/v1 ^
  --model mock-model -w -p "做一个待办清单命令行工具"
```

mock 网关按阶段（识别 system/user 消息中的「阶段「xxx」」标记）返回预置的
工具调用序列，产出的 docs/ 文档与业务代码真实落盘，可检查 `.e2e-test/` 目录。

---

## 目录结构

```
D:\agent\qwen编码agent\
├── java-core/                    Java 核心引擎（Maven 工程，JDK 8）
│   ├── pom.xml
│   └── src/main/java/com/alibaba/qwen/code/agent/
│       ├── Main.java             CLI 入口（REPL / -p / -w 工作流 / /workflow）
│       ├── config/               AgentConfig（参数/环境变量解析）
│       ├── core/                 AgentLoop / Session / Message / ToolCall
│       ├── llm/                  ChatClient（流式 SSE）/ ToolDefinition / ChatResponse
│       ├── tools/                ToolRegistry（10 个工具）
│       ├── bridge/               PythonBridge（JSON-lines stdio）
│       ├── permission/           PermissionManager（权限 + 危险命令）
│       └── workflow/             WorkflowOrchestrator / Skill / BuiltinSkills / LoopGuard
├── python-runtime/               Python 执行层
│   ├── qwen_agent_runtime/
│   │   ├── server.py             JSON-lines 分发服务器
│   │   ├── security.py           路径沙箱 + 危险命令黑名单
│   │   └── tools/                file / search / shell / workspace / todo 工具
│   └── tests/                    单元 + 协议冒烟测试
├── scripts/
│   ├── build.cmd                 构建脚本
│   ├── run.cmd                   运行入口（自动加载 qwencode.properties）
│   └── mock_llm.py               本地 mock LLM 网关（六阶段状态机）
├── qwencode.properties           配置文件（在此修改 api-key / base-url / model）
├── sample-workspace/             示例工作区
├── docs/ARCHITECTURE.md          架构设计文档
└── tools/apache-maven-3.9.9/     portable Maven（随项目分发）
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
| `packages/sdk-java/qwencode` 传输客户端   | `java-core/.../bridge/PythonBridge.java`（由 "连接外部 daemon" 升级为 "驱动自建 Python 执行层"）      |
| `packages/cli` CLI                   | `java-core/.../Main.java`（交互 REPL + `-p` 非交互 + `-w` 工作流）                        |

---

## License

Apache 2.0（与上游 qwen-code 一致）。
