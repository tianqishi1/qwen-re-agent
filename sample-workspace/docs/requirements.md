# 需求文档：Python 命令行待办事项工具（todo-cli）

- 文档版本：v1.0
- 状态：待评审（requirement-analysis 阶段产出）
- 目标读者：编码实现者、测试者、评审者
- 上游输入：用户原话「写一个 Python 命令行待办事项工具，支持增删改查」

---

## 1. 背景与目标

### 1.1 背景
当前工作区（`D:\agent\qwen-re-agent\sample-workspace`）中不存在任何 Python 代码，仅有与本需求无关的 `snake/`（纯前端 HTML/JS 贪吃蛇）示例与 `sample.txt`/`summary.md` 演示文件。因此本项目为**全新（greenfield）项目**，不存在需要兼容的历史实现，也不允许改动 `snake/` 目录。

### 1.2 目标
交付一个**零第三方依赖、可离线运行、跨平台**的 Python 命令行待办事项工具，让用户在终端中以最短路径完成待办条目的**增、删、改、查**，数据本地持久化且可被人类肉眼阅读。

### 1.3 可度量的成功标准
| 编号 | 目标 | 度量方式 |
| --- | --- | --- |
| G1 | 一条命令完成一次新增 | `todo add "买牛奶"` 单条命令成功，退出码 0 |
| G2 | 一条命令完成一次查询 | `todo list` 输出包含全部未完成条目 |
| G3 | 一条命令完成一次修改 | `todo edit 1 --content "..."` 后 `todo show 1` 内容已变更 |
| G4 | 一条命令完成一次删除 | `todo rm 1` 后 `todo show 1` 退出码 1 |
| G5 | 安装成本为零 | 仅需 Python 3.9+，`pip install` 第三方包数 = 0 |
| G6 | 数据不丢失 | 进程被中断（Ctrl+C / kill）后数据文件仍可解析（见 NFR-3） |

### 1.4 非目标（明确不做）
- 不做 GUI、Web、TUI 界面；不做移动端。
- 不做多用户、账号、云端同步、协作。
- 不做定时提醒、系统通知、闹钟。
- 不做重复任务（recurring）、子任务树、看板视图。
- 不做自然语言解析（如「下周三提醒我」）。
- 不做数据库（SQLite/PostgreSQL）后端；存储仅使用 JSON 文件。

---

## 2. 用户场景

### 2.1 目标用户
| 角色 | 描述 | 核心诉求 |
| --- | --- | --- |
| 终端型开发者 | 长期在终端工作，不愿切换窗口 | 命令短、启动快、可脚本化 |
| 运维/脚本编写者 | 需要在 shell 脚本中读写待办 | 稳定的退出码与 `--json` 输出 |
| 普通办公用户（次要） | 只想记下/勾掉待办 | 输出可读、错误提示友好、会中文 |

### 2.2 关键场景（端到端）

**S1 快速记录（新增）**
1. 用户执行 `todo add "给张总回复邮件" -p high -d tomorrow -t work`
2. 工具分配自增 ID（如 `1`），写入数据文件
3. 终端输出：`已新增 #1 给张总回复邮件 [high] 截止 2026-02-11 标签:work`

**S2 查看清单（查询）**
1. 用户执行 `todo list`
2. 输出对齐的表格，含 ID / 状态 / 优先级 / 截止日期 / 内容 / 标签
3. 已逾期条目以醒目方式标注（如 `!逾期`），已完成条目显示 `✓`
4. 清单为空时输出 `暂无待办`（不得输出空白、不得报错）

**S3 勾选完成 / 取消完成（修改）**
1. `todo done 1 3` → 一次把 #1、#3 标记为完成，写入 `completed_at`
2. `todo undone 1` → 恢复为未完成，`completed_at` 置空

**S4 编辑内容与属性（修改）**
1. 用户发现写错字：`todo edit 2 --content "给张总回复邮件（含报价单）"`
2. 用户调整优先级与截止日：`todo edit 2 -p low --clear-due`
3. `todo edit` 不带任何修改参数时，必须以退出码 2 报错并提示至少提供一个修改项（防止误操作）

**S5 删除（删除）**
1. `todo rm 2` → 删除前打印将被删除的条目摘要，删除后输出 `已删除 #2`
2. `todo rm 2 3 4 --force` → 跳过确认/摘要提示（用于脚本），批量删除
3. `todo rm 999` → stderr 输出 `错误：未找到待办 #999`，退出码 1

**S6 清理与批量查询**
1. `todo list --done --search 邮件` → 只列出已完成且内容含「邮件」的条目
2. `todo list --json` → 输出机器可解析 JSON，供脚本 `jq` 消费
3. `todo clear --done` → 一次性删除所有已完成条目，输出删除条数

**S7 自定义存储位置（多环境）**
1. 用户在项目内隔离数据：`todo --file ./my-todos.json list`
2. 或通过环境变量：`TODO_FILE=/tmp/t.json todo add "test"`
3. 优先级：`--file` > 环境变量 `TODO_FILE` > 默认 `~/.todo/todos.json`

**S8 首次使用（无数据文件）**
1. 用户第一次执行 `todo list`，数据文件不存在
2. 工具不得报错，输出 `暂无待办`，退出码 0（写操作时才真正创建文件与父目录）

---

## 3. 术语与约定

| 术语 | 含义 |
| --- | --- |
| Todo / 待办 | 一条待办事项记录，由 `id` 唯一标识 |
| 数据文件 | JSON 格式的本地存储文件，默认 `~/.todo/todos.json` |
| 完成态 | `done == true` 的待办 |
| 逾期 | `due` 早于「今天」且 `done == false` |
| 宽字符 | 东亚全角字符（中文等），显示宽度按 2 计 |

---

## 4. 数据模型与存储

### 4.1 JSON 文件结构（schema version 1）
```json
{
  "version": 1,
  "next_id": 3,
  "items": [
    {
      "id": 1,
      "content": "给张总回复邮件",
      "done": false,
      "priority": "high",
      "tags": ["work"],
      "due": "2026-02-11",
      "created_at": "2026-02-10T09:30:12+08:00",
      "updated_at": "2026-02-10T09:30:12+08:00",
      "completed_at": null
    },
    {
      "id": 2,
      "content": "买牛奶",
      "done": true,
      "priority": "medium",
      "tags": [],
      "due": null,
      "created_at": "2026-02-09T20:00:00+08:00",
      "updated_at": "2026-02-10T08:00:00+08:00",
      "completed_at": "2026-02-10T08:00:00+08:00"
    }
  ]
}
```

### 4.2 字段约束
| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `version` | int | 固定为 `1`；读取到更高版本时以退出码 3 报错并要求升级工具 |
| `next_id` | int | ≥ 1；**只增不减**，删除后 ID 不复用 |
| `items[].id` | int | ≥ 1，文件内唯一 |
| `items[].content` | str | strip 后长度 1–500 个字符；不得包含 `\n`、`\r`、`\t`、控制字符 |
| `items[].done` | bool | 默认 `false` |
| `items[].priority` | str | 枚举 `high` / `medium` / `low`，默认 `medium` |
| `items[].tags` | list[str] | 去重、保序、升序规范化；每个标签 strip 后 1–32 字符，仅允许 `A-Za-z0-9_-` 与中文 |
| `items[].due` | str \| null | `YYYY-MM-DD` 本地日期，或 `null` |
| `items[].created_at` | str | ISO 8601，含本地时区偏移，创建后不变 |
| `items[].updated_at` | str | ISO 8601；任何修改都刷新 |
| `items[].completed_at` | str \| null | `done=true` 时为完成时间；`done=false` 时须为 `null` |

### 4.3 文件位置与创建
- 默认路径：`~/.todo/todos.json`（`~` 按 `pathlib.Path.home()` 展开；Windows 上即 `C:\Users\<user>\.todo\todos.json`）。
- 覆盖方式：`--file PATH` 全局选项，或环境变量 `TODO_FILE`。
- 读操作（`list`/`show`）：文件或父目录不存在时视为「空清单」，**不创建**任何文件。
- 写操作（`add`/`edit`/`done`/`undone`/`rm`/`clear`）：自动 `mkdir -p` 父目录并创建文件。
- 文件编码：UTF-8，**不加 BOM**；`ensure_ascii=False` 写入，保证中文可读。

### 4.4 写入原子性（硬性要求）
1. 写入同目录临时文件 `.<name>.tmp-<pid>`；
2. `flush()` + `os.fsync()`；
3. `os.replace(tmp, target)` 原子替换；
4. 任一步骤失败则删除临时文件并保持原文件不变；
5. POSIX 上创建文件权限为 `0600`（若平台支持）。
6. 数据文件损坏（JSON 解析失败 / schema 校验失败）时：**不得覆盖原文件**，向 stderr 输出文件路径与原因，退出码 3，并提示 `可用 --file 指定其他文件或手动修复`。

---

## 5. 功能清单

### 5.1 功能总览
| 编号 | 功能 | 命令 | 对应 CRUD | 优先级 |
| --- | --- | --- | --- | --- |
| F1 | 新增待办 | `todo add` | Create | P0 |
| F2 | 列出/筛选待办 | `todo list` | Read | P0 |
| F3 | 查看单条待办 | `todo show` | Read | P0 |
| F4 | 编辑待办 | `todo edit` | Update | P0 |
| F5 | 标记完成 / 取消完成 | `todo done` / `todo undone` | Update | P0 |
| F6 | 删除待办 | `todo rm` | Delete | P0 |
| F7 | 清理已完成 | `todo clear --done` | Delete | P1 |
| F8 | JSON 机器可读输出 | `--json`（`list`/`show`） | Read | P1 |
| F9 | 帮助与版本 | `todo -h` / `todo --version` | — | P1 |

### 5.2 F1 新增待办（`todo add`）
```
todo add <content> [-p high|medium|low] [-d DATE] [-t TAG]... [--json]
```
- 行为：校验 `content` → 分配 `id = next_id` → `next_id += 1` → 写入 → 输出 `已新增 #<id> <content>`。
- `-t` 可重复出现；`-p` 默认 `medium`；`-d` 支持 `YYYY-MM-DD`、`today`、`tomorrow`、`+Nd`（如 `+3d`）。
- `--json` 时输出新条目对象，退出码仍为 0。
- 边界与错误：
  - `content` 为空/全空白 → 退出码 2，消息 `错误：内容不能为空`。
  - `content` 超 500 字符 → 退出码 2，消息 `错误：内容最长 500 字符，当前 N 字符`。
  - `content` 含换行/制表符 → 退出码 2，消息 `错误：内容不能包含换行或制表符`。
  - 非法优先级 / 非法日期 → 退出码 2，消息含非法值与原样输入。

### 5.3 F2 列出待办（`todo list`）
```
todo list [--all] [--pending] [--done] [--tag TAG] [--priority P]
          [--search KW] [--sort id|priority|due|created] [--reverse] [--json]
```
- 默认（无筛选）：显示**全部**条目，未完成在前、已完成在后。
- 筛选器可组合（AND 语义）；`--pending` 与 `--done` 同时给出 → 退出码 2。
- `--search KW`：对 `content` 与 `tags` 做**大小写不敏感**子串匹配。
- `--sort priority` 顺序为 `high > medium > low`；`--sort due` 时 `due == null` 排在最后；`--sort created` 按 `created_at` 升序。同一排序键下按 `id` 升序稳定排序。
- 输出表格列顺序：`ID | 状态 | 优先级 | 截止 | 内容 | 标签`。
  - 状态：未完成 `[ ]`，已完成 `[x]`。
  - 逾期（未完成且 `due < 今天`）：截止列显示 `YYYY-MM-DD !逾期`。
  - 截止为 `null`：显示 `-`；标签为空：显示 `-`。
  - 对齐必须按**显示宽度**计算（东亚宽字符计 2、组合字符计 0），保证中英文混排表格竖线对齐。
- 空结果：
  - 无任何条目 → 输出 `暂无待办`；
  - 有条目但被筛选掉 → 输出 `没有符合条件的待办`。
- `--json`：输出 `{"count": N, "items": [...]}`；即使 0 条也输出合法 JSON。

### 5.4 F3 查看单条（`todo show`）
```
todo show <id> [--json]
```
- 成功：多行键值输出，含全部字段与人类可读时间。
- `id` 不存在 → stderr `错误：未找到待办 #<id>`，退出码 1。
- `id` 非正整数 → 退出码 2。
- `--json`：输出单个条目对象。

### 5.5 F4 编辑（`todo edit`）
```
todo edit <id> [--content TEXT] [-p P] [-d DATE|--clear-due]
               [-t TAG]... [--clear-tags] [--json]
```
- `-t` 语义：`--clear-tags` 先清空，随后按 `-t` 依次追加（去重）；只给 `-t` 时在原有标签上追加。
- `--clear-due` 与 `-d` 同时出现 → 退出码 2。
- 未提供任何修改参数 → 退出码 2，消息 `错误：请至少提供一个修改项（--content/-p/-d/-t/--clear-*）`。
- 成功后刷新 `updated_at`，输出 `已更新 #<id>`。
- `id` 不存在 → 退出码 1；
- 修改 `done` 状态**不走** `edit`，须使用 `done`/`undone`（`edit` 不接受 `--done`）。

### 5.6 F5 标记完成 / 取消完成（`todo done` / `todo undone`）
```
todo done <id>... [--json]
todo undone <id>...
```
- 支持一次传多个 ID。
- 幂等：对已完成的条目执行 `done` 不报错，输出 `#<id> 已是完成状态`（`--json` 时 `changed: false`）。
- 副作用：`done` → `done=true`、`completed_at=now`、刷新 `updated_at`；`undone` → `done=false`、`completed_at=null`、刷新 `updated_at`。
- 多 ID 中部分不存在：**先校验全部 ID 存在再整体执行**（避免半成功）；任一不存在 → 退出码 1，且不产生任何写入。
- 无参调用 → 退出码 2。

### 5.7 F6 删除（`todo rm`）
```
todo rm <id>... [--force] [--json]
```
- 默认：先打印每个将被删除条目的摘要（`#id 内容`），再删除，输出 `已删除 #<id>`（多个则逐行或汇总 `已删除 N 条`）。
- `--force`：省略摘要打印，直接删除（供脚本使用）。
- 同样遵循「先全部校验、后写入」；有 ID 不存在 → 退出码 1 且不删除任何条目。
- 删除后 `next_id` 不回退（不满足「不复用」即视为缺陷）。
- 无参调用 → 退出码 2。

### 5.8 F7 清理（`todo clear`）
```
todo clear --done
```
- 只允许 `--done`（缺少该标志 → 退出码 2，提示避免误删全部数据）。
- 删除全部 `done == true` 的条目，输出 `已清除 N 条已完成待办`；无匹配时输出 `没有可清除的已完成待办`，退出码 0。

### 5.9 F8 全局选项与帮助
| 选项 | 说明 |
| --- | --- |
| `--file PATH` | 指定数据文件（优先级最高） |
| `--json` | 机器可读输出（子命令亦支持） |
| `-h` / `--help` | 输出帮助，退出码 0 |
| `todo --version` | 输出 `todo-cli <版本号>`（语义化版本，如 `1.0.0`），退出码 0 |

- 无子命令直接运行 `todo` → 输出简短帮助到 stdout，退出码 0。
- 未知子命令 → argparse 用法错误，退出码 2。

### 5.10 CLI 契约汇总
| 项 | 约定 |
| --- | --- |
| 入口 | `python -m todo ...`，并在 `pyproject.toml` 提供控制台脚本 `todo = "todo.cli:main"` |
| 退出码 | `0` 成功；`1` 业务错误（未找到等）；`2` 用法/参数校验错误；`3` 数据文件损坏或版本不兼容 |
| 输出流 | 正常结果 → stdout；所有错误 → stderr |
| 错误前缀 | 统一为 `错误：`，后接可读原因 |
| 颜色 | 非 TTY 或设置 `NO_COLOR` 或 `--json` 时不输出 ANSI 转义序列 |
| 时间 | 输出使用本地时区；持久化使用带偏移的 ISO 8601 |

---

## 6. 非功能约束

### NFR-1 依赖与运行环境
- 仅使用 Python 标准库（`argparse`、`json`、`dataclasses`、`pathlib`、`datetime`、`os`、`sys`、`tempfile`、`unicodedata`、`unittest`）。
- 最低支持 Python 3.9；不使用 3.10+ 专属语法（如 `match`）与 3.12+ 专属特性。当前环境 Python 3.14.7 必须可运行。
- 支持 Windows 10/11、Linux、macOS；不可依赖 POSIX 专有命令；平台差异须优雅降级（如 `fcntl` 不可用时不崩溃）。

### NFR-2 性能
| 指标 | 目标 |
| --- | --- |
| 单次命令进程启动+执行（100 条数据） | ≤ 300 ms |
| `todo add`（10,000 条数据） | ≤ 500 ms |
| `todo list`（10,000 条数据，人类可读输出） | ≤ 800 ms |
| 内存占用（10,000 条数据） | ≤ 100 MB |
- 说明：数据量 ≤ 20,000 条时使用「整文件读入内存 — 修改 — 原子写回」策略即可；**不要求**索引或增量写。

### NFR-3 可靠性与数据安全
- 写入原子（见 4.4）；任何异常路径（Ctrl+C、未捕获异常、磁盘写入失败）都必须保证数据文件可被下一次读取解析。
- 读到损坏文件时**只读不写**，退出码 3，绝不静默重置数据。
- 保存前对内存结构做 schema 自校验（类型与约束），校验失败即中止写入。
- 允许重复写入同一内容，不做去重（用户显式操作应被尊重）。

### NFR-4 安全
- 禁止 `eval`/`exec`/`pickle` 反序列化用户数据；仅使用 `json.loads`。
- 输出到终端的内容须转义/剥离 ANSI 转义序列与控制字符，防止终端注入。
- 数据文件路径按用户显式输入解析；`--file` 相对路径解析为 `cwd` 下的绝对路径，不做隐式 `~` 展开以外的目录穿越。
- 不对数据文件设置全局可写权限（POSIX 下 `0600`）。
- 不发起任何网络请求；不读取数据文件与用户输入之外的文件。

### NFR-5 可用性
- 所有错误信息须**可自我修复**：指出错误输入、期望格式、可尝试的正确命令。
- 中文输出为默认语言；`--json` 时不输出中文提示（仅结构化数据），便于脚本解析。
- 帮助文本（`-h`）须包含每个子命令的一行示例。

### NFR-6 兼容与国际化
- 数据文件 UTF-8 无 BOM；支持中文、emoji 内容不截断（按字符数而非字节数校验）。
- Windows 终端默认 GBK 场景下不得因编码问题抛 `UnicodeEncodeError`（必要时对 stdout 使用 `errors="replace"` 兜底）。

### NFR-7 可维护性与质量
- 建议模块划分（实现者可微调，但需保持「存储层与 CLI 层解耦」）：
  ```
  todo/__init__.py       版本号
  todo/__main__.py       python -m todo 入口
  todo/models.py         Todo 数据类、校验、序列化
  todo/store.py          加载/保存/原子写/ID 分配
  todo/cli.py            argparse 解析、命令分发、输出格式化
  tests/test_models.py
  tests/test_store.py
  tests/test_cli.py
  pyproject.toml
  README.md
  ```
- 存储层（`store.py`）不得依赖 `argparse`/`print`，以便单元测试直接调用。
- 代码须通过 `python -m compileall todo`；公开函数带类型注解与 docstring。

---

## 7. 验收标准（Acceptance Criteria）

以下每条均须可被人工或自动化测试验证；建议以 `python -m unittest discover -s tests -v` 全覆盖。

### 7.1 新增（AC-1）
1. `todo add "买牛奶"` → 退出码 0，stdout 含 `#1`，数据文件 `items[0].content == "买牛奶"`、`priority == "medium"`、`done == false`、`created_at` 非空。
2. 连续新增 3 条 → ID 依次为 1、2、3；删除 #2 后再新增 → 新 ID 为 4（不复用）。
3. `todo add ""` 与 `todo add "   "` → 退出码 2，stderr 含 `内容不能为空`，数据文件未被创建/未变化。
4. `todo add "a" * 501` → 退出码 2，stderr 含 `500`。
5. `todo add "第一行\n第二行"` → 退出码 2，stderr 含 `换行`。
6. `todo add "x" -p urgent` → 退出码 2，stderr 含 `urgent`。
7. `todo add "x" -d tomorrow` → `due` 为明天日期（`YYYY-MM-DD`）；`-d +3d` → 今天 +3 天。
8. `todo add "会议" -t work -t work -t 生活` → `tags == ["work", "生活"]`（去重）。

### 7.2 查询（AC-2）
9. 空数据文件不存在时 `todo list` → 退出码 0，stdout 为 `暂无待办`，且未创建数据文件。
10. 存在 2 条数据时 `todo list` → 输出包含两条内容的表格；列标题为 `ID 状态 优先级 截止 内容 标签`。
11. 中文混排表格对齐：构造 `content` 分别为 `ab` 与 `中文内容` 的两条 → 各行分隔符（如 `|`）在终端显示宽度上对齐（按宽字符计 2 的规则断言）。
12. 逾期标注：`due` 为昨天且未完成 → 该行含 `!逾期`；已完成条目即使 `due` 为过去也**不含** `!逾期`。
13. `todo list --done` 只返回已完成；`--pending` 只返回未完成；`--done --pending` → 退出码 2。
14. `todo list --search 邮件` 大小写不敏感，命中 `content` 或 `tags`；无命中 → 输出 `没有符合条件的待办`，退出码 0。
15. `todo list --sort priority` → `high` 在 `medium` 前，`medium` 在 `low` 前。
16. `todo list --sort due` → `due == null` 的条目排在最后。
17. `todo list --json` → stdout 可被 `json.loads` 解析，结构为 `{"count": N, "items": [...]}`；0 条时 `count == 0`。
18. `todo show 1` → 输出含 `id`、`content`、`done`、`priority`、`tags`、`due`、`created_at`、`updated_at`；`todo show 999` → 退出码 1，stderr 含 `未找到`；`todo show abc` → 退出码 2。

### 7.3 修改（AC-3）
19. `todo edit 1 --content "新内容"` → `content` 变更、`updated_at` 变大、`id` 与 `created_at` 不变，stdout 含 `已更新 #1`。
20. `todo edit 1`（无修改参数）→ 退出码 2，stderr 含 `至少提供一个修改项`。
21. `todo edit 1 --clear-due` → `due == null`；`todo edit 1 -d 2026-03-01` → `due == "2026-03-01"`；两者同时给出 → 退出码 2。
22. `todo edit 1 -t new1 -t new2` 在原有 `["work"]` 上追加 → `["work","new1","new2"]`；加 `--clear-tags` 后 → 仅含新标签。
23. `todo done 1` → `done == true`、`completed_at` 非空；重复执行 → 退出码 0 且 stdout 含 `已是完成状态`。
24. `todo undone 1` → `done == false`、`completed_at == null`。
25. `todo done 1 2 3` 时若 #3 不存在 → 退出码 1，且 #1、#2 状态**未变化**（无部分写入）。
26. `todo done`（无参）→ 退出码 2。

### 7.4 删除（AC-4）
27. `todo rm 1` → 退出码 0，`todo show 1` → 退出码 1，`items` 数量减 1。
28. `todo rm 1 2 --force` → 一次删除两条，`next_id` 不回退。
29. `todo rm 999` → 退出码 1，stderr 含 `未找到待办 #999`，数据文件内容不变。
30. `todo rm 1 999` → 退出码 1 且 #1 仍存在（整批失败）。
31. `todo clear --done` 有 2 条已完成 → 输出 `已清除 2 条`，未完成条目保留；无已完成 → 输出 `没有可清除的已完成待办`；`todo clear`（无 `--done`）→ 退出码 2。

### 7.5 存储与健壮性（AC-5）
32. 数据文件为非法 JSON（如写入 `{oops`）时执行 `todo list` → 退出码 3，stderr 含文件路径；执行 `todo add` → 退出码 3 且**原文件字节内容未被覆盖**。
33. `version: 2` 的数据文件 → 退出码 3，提示版本不兼容。
34. 模拟写入中断（保存过程中注入异常）→ 数据文件仍为合法 JSON，且等于修改前内容（原子写验证）。
35. `--file ./x/y.json add "a"` 在 `x/` 不存在时 → 自动创建目录并成功写入。
36. 并发/多进程写：连续两次独立进程 `add` → 最终数据文件合法且 `items` 长度为 2（允许极端竞态下丢更新的已知限制需写入 README，但不得出现文件损坏）。
37. 环境变量优先：`TODO_FILE=/tmp/t.json todo add "x"` 后 `/tmp/t.json` 存在；同时给 `--file other.json` 时以 `--file` 为准。
38. `todo list --json | python -c "import sys,json;json.load(sys.stdin)"` → 无异常（stdout 无中文提示污染）。

### 7.6 CLI 与工程质量（AC-6）
39. `todo --help`、`todo add --help`、`todo -h` 均退出码 0 且包含用法与示例。
40. `todo --version` → 输出含语义化版本号，退出码 0；`todo bogus` → 退出码 2。
41. `python -m compileall todo` 无语法错误；`python -m unittest discover -s tests` 全部通过。
42. 在 Windows 11（当前环境，Python 3.14.7）上完成上述全部人工冒烟命令；无 `UnicodeEncodeError`、无 traceback 泄漏到 stdout。
43. 导入检查：`python -c "import todo, todo.store, todo.cli"` 成功且 `pip list` 中无新增第三方依赖。
44. README.md 含安装（无需安装）、10 条常用命令示例、数据文件位置说明与 JSON schema 示例。

---

## 8. 交付物清单
| 交付物 | 说明 |
| --- | --- |
| `todo/` 包 | CLI 与存储实现（见 NFR-7 结构） |
| `tests/` | 覆盖 AC 的单元测试（含 CLI 端到端子进程测试） |
| `pyproject.toml` | 项目元数据、`todo` 控制台脚本、无第三方依赖 |
| `README.md` | 使用说明与示例 |
| `docs/requirements.md` | 本文档（本阶段产出） |

---

## 9. 风险与开放问题
| 编号 | 风险/问题 | 建议处置 |
| --- | --- | --- |
| R1 | 多进程并发写存在「最后写入者胜」的丢更新窗口 | v1 接受并在 README 声明；后续可用文件锁或 SQLite 解决 |
| R2 | 表格宽字符对齐在部分终端（emoji、组合字符）仍可能偏移 | 使用 `unicodedata.east_asian_width` 计算宽度，emoji 退化处理并记录已知限制 |
| R3 | 截止日期仅支持本地日期，无时区/时间粒度 | v1 明确限制为 `YYYY-MM-DD`；时间粒度列为后续候选 |
| R4 | 是否需要 `todo open`/交互式勾选界面 | 未确认，默认不做；如需请评审时提出 |
| R5 | 数据文件默认放在 `~/.todo/`，Windows 用户可能偏好 `%APPDATA%` | v1 统一用 `~/.todo/` 以保证跨平台一致，可被 `TODO_FILE` 覆盖 |
