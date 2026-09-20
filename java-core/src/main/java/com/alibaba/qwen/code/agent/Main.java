package com.alibaba.qwen.code.agent;

import com.alibaba.qwen.code.agent.bridge.PythonBridge;
import com.alibaba.qwen.code.agent.config.AgentConfig;
import com.alibaba.qwen.code.agent.config.ConfigFile;
import com.alibaba.qwen.code.agent.core.AgentLoop;
import com.alibaba.qwen.code.agent.core.Session;
import com.alibaba.qwen.code.agent.llm.ChatClient;
import com.alibaba.qwen.code.agent.permission.PermissionManager;
import com.alibaba.qwen.code.agent.tools.ToolRegistry;
import com.alibaba.qwen.code.agent.workflow.BuiltinSkills;
import com.alibaba.qwen.code.agent.workflow.Skill;
import com.alibaba.qwen.code.agent.workflow.WorkflowOrchestrator;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/**
 * Qwencode 编码 Agent CLI 入口。
 *
 * <p>用法：</p>
 * <pre>
 *   java -jar qwencode-agent.jar [options] [-p "prompt"]
 *
 *   --api-key KEY        LLM API Key（或环境变量 QWEN_API_KEY）
 *   --base-url URL       OpenAI 兼容网关地址（默认 DashScope 兼容端点）
 *   --model NAME         模型名（默认 qwen3-coder-plus）
 *   -c, --cwd DIR        工作目录（默认当前目录）
 *   --permission MODE    auto | plan | approve（默认 auto）
 *   --max-iterations N   最大迭代次数（默认 20）
 *   --no-stream          关闭流式输出
 *   --python CMD         Python 解释器命令（默认 python）
 *   -p, --prompt TEXT    非交互模式执行单次任务
 *   -h, --help           帮助
 * </pre>
 */
public final class Main {

    private Main() {
    }

    public static void main(String[] args) {
        forceUtf8Stdio();

        CliArgs parsed = CliArgs.parse(args);
        if (parsed.help) {
            printHelp();
            return;
        }

        AgentConfig config;
        try {
            config = parsed.buildConfig();
        } catch (IllegalArgumentException e) {
            System.err.println("配置错误: " + e.getMessage());
            System.exit(2);
            return;
        }

        System.out.println("Qwencode 编码 Agent (Java-Python 双栈重构版)");
        System.out.println("  工作目录 : " + config.workspaceString());
        System.out.println("  模型     : " + config.model());
        System.out.println("  权限模式 : " + config.permissionMode());
        try {
            String cfgSrc = ConfigFile.load(parsed.configPath).source();
            System.out.println("  配置来源 : " + cfgSrc);
        } catch (IllegalArgumentException e) {
            System.out.println("  配置来源 : " + e.getMessage());
        }

        // 启动 Python 执行层
        PythonBridge bridge = null;
        try {
            bridge = new PythonBridge(config.pythonCommand(),
                    config.pythonRuntimeDirString(), config.workspaceString());
            if (!bridge.ping(10_000)) {
                System.err.println("错误: Python 执行层启动失败。请确认 Python 可用且 "
                        + "qwen_agent_runtime 位于 " + config.pythonRuntimeDirString());
                System.exit(3);
                return;
            }
            System.out.println("Python 执行层已连接 (" + pythonInfo(bridge) + ")");

            ChatClient chat = new ChatClient(config);
            ToolRegistry tools = new ToolRegistry(bridge, 120_000);
            PermissionManager permissions = new PermissionManager(config);
            Session session = new Session();

            if (parsed.prompt != null) {
                if (parsed.workflow) {
                    runWorkflow(config, chat, bridge, parsed.prompt, parsed.stages);
                } else {
                    runOnce(config, chat, tools, permissions, session, parsed.prompt);
                }
            } else {
                repl(config, chat, tools, permissions, session, bridge);
            }
        } finally {
            if (bridge != null) {
                bridge.close();
            }
        }
    }

    private static void runOnce(AgentConfig config, ChatClient chat, ToolRegistry tools,
                                PermissionManager permissions, Session session, String prompt) {
        AgentLoop loop = new AgentLoop(config, chat, tools, permissions, session);
        try {
            String answer = loop.run(prompt);
            System.out.println();
            System.out.println("────────────────────────────────────────");
            System.out.println(answer);
        } catch (ChatClient.LlmException e) {
            System.err.println("LLM 调用失败: " + e.getMessage());
            System.exit(4);
        }
    }

    /**
     * 工作流模式：以输入想法驱动六阶段编码流水线
     * （需求拆解 → 技术方案 → 开发 → 测试 → 上线 → 运维）。
     */
    private static void runWorkflow(AgentConfig config, ChatClient chat, PythonBridge bridge,
                                    String idea, List<String> stages) {
        System.out.println();
        System.out.println("◆ 编码工作流启动（阶段: "
                + (stages == null || stages.isEmpty() ? "默认全流水线" : String.join(", ", stages))
                + "）");
        System.out.println("◆ 输入想法: " + idea);
        System.out.println();

        WorkflowOrchestrator orch = new WorkflowOrchestrator(config, chat,
                bridge::newToolRegistry, 8);

        // 校验阶段名（提前失败，避免执行一半才发现）
        if (stages != null) {
            for (String s : stages) {
                if (BuiltinSkills.get(s) == null) {
                    System.err.println("未知阶段: " + s + "，可用: " + BuiltinSkills.PIPELINE);
                    System.exit(2);
                }
            }
        }

        WorkflowOrchestrator.WorkflowResult result;
        try {
            result = orch.run(idea, stages);
        } catch (ChatClient.LlmException e) {
            System.err.println("LLM 调用失败: " + e.getMessage());
            System.exit(4);
            return;
        } catch (IllegalArgumentException e) {
            System.err.println("工作流参数错误: " + e.getMessage());
            System.exit(2);
            return;
        }

        System.out.println();
        System.out.println("══════════════════════════════════════════");
        System.out.println("◆ 工作流执行汇总");
        System.out.println("══════════════════════════════════════════");
        for (WorkflowOrchestrator.StageResult sr : result.stages) {
            String flag = sr.success ? "✓" : "✗";
            System.out.println("  [" + flag + "] " + sr.stage
                    + "  耗时 " + sr.costSeconds + "s"
                    + (sr.loopProtected ? "  [循环防护触发]" : "")
                    + (sr.stalled ? "  [停滞防护触发]" : ""));
            if (sr.artifacts != null && !sr.artifacts.isEmpty()) {
                System.out.println("      产出物: " + String.join(", ", sr.artifacts));
            }
            String oneLine = sr.summary == null ? "" : sr.summary.replace('\n', ' ').trim();
            if (oneLine.length() > 220) {
                oneLine = oneLine.substring(0, 220) + "…";
            }
            System.out.println("      摘要: " + oneLine);
        }
        System.out.println();
        System.out.println("◆ 整体: " + (result.success ? "全部阶段完成" : "流水线中途终止"));
        System.out.println("  产物见工作区 docs/ 目录（requirements/design/test-report/deploy/ops）。");
    }

    private static void repl(AgentConfig config, ChatClient chat, ToolRegistry tools,
                             PermissionManager permissions, Session session, PythonBridge bridge) {
        BufferedReader reader = new BufferedReader(
                new InputStreamReader(System.in, StandardCharsets.UTF_8));
        AgentLoop loop = new AgentLoop(config, chat, tools, permissions, session);
        System.out.println("输入任务后回车执行；输入 /exit 或 /quit 退出。");
        System.out.println();
        while (true) {
            System.out.print("> ");
            System.out.flush();
            String line;
            try {
                line = reader.readLine();
            } catch (IOException e) {
                break;
            }
            if (line == null) {
                break;
            }
            String trimmed = line.trim();
            if (trimmed.isEmpty()) {
                continue;
            }
            if (trimmed.equals("/exit") || trimmed.equals("/quit") || trimmed.equals("/bye")) {
                break;
            }
            if (trimmed.equals("/help")) {
                printHelp();
                continue;
            }
            if (trimmed.startsWith("/workflow")) {
                // 用法: /workflow <想法>  （可选 | 阶段1,阶段2 指定阶段子集）
                String rest = trimmed.substring("/workflow".length()).trim();
                List<String> stages = null;
                String idea = rest;
                int bar = rest.lastIndexOf('|');
                if (bar >= 0) {
                    idea = rest.substring(0, bar).trim();
                    String stageList = rest.substring(bar + 1).trim();
                    if (!stageList.isEmpty()) {
                        stages = Arrays.asList(stageList.split(","));
                    }
                }
                if (idea.isEmpty()) {
                    System.out.println("用法: /workflow <想法> [| 阶段1,阶段2]  （阶段可选: "
                            + BuiltinSkills.PIPELINE + "）");
                    continue;
                }
                runWorkflow(config, chat, bridge, idea, stages);
                System.out.println();
                continue;
            }
            System.out.println("────────── 开始执行 ──────────");
            try {
                String answer = loop.run(trimmed);
                System.out.println();
                System.out.println("────────────────────────────────────────");
                System.out.println(answer);
            } catch (ChatClient.LlmException e) {
                System.err.println("LLM 调用失败: " + e.getMessage());
            } catch (Exception e) {
                System.err.println("执行异常: " + e.getMessage());
            }
            System.out.println();
        }
    }

    /**
     * 强制 stdout/stderr 使用 UTF-8 输出。
     *
     * <p>JDK 8 在中文 Windows 上默认按 GBK 编码输出，而现代终端（含代码页 65001）
     * 按 UTF-8 解码，导致中文乱码。此处显式包装为 UTF-8 输出流。</p>
     */
    private static void forceUtf8Stdio() {
        try {
            java.io.PrintStream utf8Out = new java.io.PrintStream(
                    new java.io.FileOutputStream(java.io.FileDescriptor.out), true, "UTF-8");
            java.io.PrintStream utf8Err = new java.io.PrintStream(
                    new java.io.FileOutputStream(java.io.FileDescriptor.err), true, "UTF-8");
            System.setOut(utf8Out);
            System.setErr(utf8Err);
        } catch (java.io.UnsupportedEncodingException e) {
            // UTF-8 必然受支持；忽略
        }
    }

    private static String pythonInfo(PythonBridge bridge) {
        try {
            com.alibaba.fastjson2.JSONObject info = bridge.call(
                    "get_workspace_info", new java.util.LinkedHashMap<>(), 10_000);
            if (Boolean.TRUE.equals(info.getBoolean("ok"))) {
                com.alibaba.fastjson2.JSONObject r = info.getJSONObject("result");
                return "Python " + r.getString("python") + ", " + r.getString("os");
            }
        } catch (Exception ignore) {
            // 降级显示
        }
        return "已连接";
    }

    private static void printHelp() {
        System.out.println(
                "用法: java -jar qwencode-agent.jar [选项] [-p \"任务描述\"]\n"
                + "\n"
                + "选项:\n"
                + "  --api-key KEY        LLM API Key（或设置环境变量 QWEN_API_KEY）\n"
                + "  --base-url URL       OpenAI 兼容网关地址（默认 DashScope 兼容端点）\n"
                + "  --model NAME         模型名（默认 qwen3-coder-plus）\n"
                + "  -c, --cwd DIR        工作目录（默认当前目录）\n"
                + "  --permission MODE    auto | plan | approve（默认 auto）\n"
                + "  --max-iterations N   最大工具迭代次数（默认 20）\n"
                + "  --no-stream          关闭流式输出\n"
                + "  --python CMD         Python 解释器命令（默认 python）\n"
                + "  --config PATH        配置文件路径（默认查找 qwencode.properties）\n"
                + "  -p, --prompt TEXT    非交互模式，执行单次任务后退出\n"
                + "  -w, --workflow       以工作流模式运行（配合 -p 使用，驱动完整编码流水线）\n"
                + "  --stage 阶段1,阶段2   只运行指定阶段（隐式开启工作流；可选:\n"
                + "                       requirement-analysis,tech-design,coding,testing,deployment,operations）\n"
                + "  -h, --help           显示帮助\n"
                + "\n"
                + "环境变量: QWEN_API_KEY, QWEN_BASE_URL, QWEN_MODEL, QWEN_PERMISSION, QWEN_CONFIG\n"
                + "\n"
                + "配置文件（qwencode.properties，与 jar 同目录）：\n"
                + "  api-key=sk-xxx\n"
                + "  base-url=https://api.deepseek.com\n"
                + "  model=deepseek-flash\n"
                + "  permission=auto\n"
                + "  workspace=D:/path/to/project\n"
                + "  max-iterations=20\n"
                + "  python=python\n"
                + "\n"
                + "优先级: 命令行参数 > 环境变量 > 配置文件 > 默认值\n");
    }

    /** 命令行参数解析。 */
    static final class CliArgs {
        boolean help;
        String apiKey;
        String baseUrl;
        String model;
        String cwd;
        String permission;
        Integer maxIterations;
        Boolean stream = Boolean.TRUE;
        String python;
        String configPath;
        String prompt;
        boolean workflow;
        List<String> stages;

        static CliArgs parse(String[] args) {
            CliArgs a = new CliArgs();
            for (int i = 0; i < args.length; i++) {
                String arg = args[i];
                switch (arg) {
                    case "-h":
                    case "--help":
                        a.help = true;
                        break;
                    case "--api-key":
                        a.apiKey = value(args, ++i, arg);
                        break;
                    case "--base-url":
                        a.baseUrl = value(args, ++i, arg);
                        break;
                    case "--model":
                        a.model = value(args, ++i, arg);
                        break;
                    case "-c":
                    case "--cwd":
                        a.cwd = value(args, ++i, arg);
                        break;
                    case "--permission":
                        a.permission = value(args, ++i, arg);
                        break;
                    case "--max-iterations":
                        a.maxIterations = Integer.parseInt(value(args, ++i, arg));
                        break;
                    case "--no-stream":
                        a.stream = Boolean.FALSE;
                        break;
                    case "--python":
                        a.python = value(args, ++i, arg);
                        break;
                    case "--config":
                        a.configPath = value(args, ++i, arg);
                        break;
                    case "-p":
                    case "--prompt":
                        a.prompt = value(args, ++i, arg);
                        break;
                    case "-w":
                    case "--workflow":
                        a.workflow = true;
                        break;
                    case "--stage":
                        String stageList = value(args, ++i, arg);
                        a.stages = Arrays.asList(stageList.split(","));
                        a.workflow = true;
                        break;
                    default:
                        System.err.println("未知参数: " + arg);
                        printHelpExit();
                }
            }
            return a;
        }

        private static String value(String[] args, int i, String flag) {
            if (i >= args.length) {
                System.err.println("参数 " + flag + " 缺少值");
                printHelpExit();
            }
            return args[i];
        }

        private static void printHelpExit() {
            System.exit(2);
        }

        AgentConfig buildConfig() {
            // 优先级：命令行 > 环境变量 > 配置文件 > 默认值
            ConfigFile file = ConfigFile.load(configPath);
            AgentConfig.Builder b = AgentConfig.builder();

            String key = firstNonNull(apiKey, env("QWEN_API_KEY"), file.get("api-key"));
            String url = firstNonNull(baseUrl, env("QWEN_BASE_URL"), file.get("base-url"));
            String modelName = firstNonNull(model, env("QWEN_MODEL"), file.get("model"));
            String cwdValue = firstNonNull(cwd, file.get("workspace"));
            String permValue = firstNonNull(permission, env("QWEN_PERMISSION"),
                    file.get("permission"));
            String pythonValue = firstNonNull(python, file.get("python"));
            String iterValue = file.get("max-iterations");

            b.apiKey(key);
            if (url != null) {
                b.baseUrl(url);
            }
            if (modelName != null) {
                b.model(modelName);
            }
            if (cwdValue != null) {
                b.workspace(cwdValue);
            }
            if (permValue != null) {
                switch (permValue.toLowerCase()) {
                    case "auto":
                        b.permissionMode(AgentConfig.PermissionMode.AUTO);
                        break;
                    case "plan":
                        b.permissionMode(AgentConfig.PermissionMode.PLAN);
                        break;
                    case "approve":
                        b.permissionMode(AgentConfig.PermissionMode.APPROVE);
                        break;
                    default:
                        throw new IllegalArgumentException("未知权限模式: " + permValue
                                + "（可选 auto/plan/approve）");
                }
            }
            if (maxIterations != null) {
                b.maxIterations(maxIterations);
            } else if (iterValue != null) {
                try {
                    b.maxIterations(Integer.parseInt(iterValue.trim()));
                } catch (NumberFormatException e) {
                    throw new IllegalArgumentException("配置 max-iterations 非法: " + iterValue);
                }
            }
            if (stream != null) {
                b.stream(stream);
            }
            if (pythonValue != null) {
                b.pythonCommand(pythonValue);
            }
            return b.build();
        }

        @SafeVarargs
        private static String firstNonNull(String... values) {
            for (String v : values) {
                if (v != null && !v.trim().isEmpty()) {
                    return v;
                }
            }
            return null;
        }

        private static String env(String name) {
            String v = System.getenv(name);
            return (v == null || v.trim().isEmpty()) ? null : v;
        }
    }
}
