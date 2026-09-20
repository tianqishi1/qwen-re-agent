package com.alibaba.qwen.code.agent.core;

import com.alibaba.qwen.code.agent.config.AgentConfig;
import com.alibaba.qwen.code.agent.llm.ChatClient;
import com.alibaba.qwen.code.agent.llm.ChatResponse;
import com.alibaba.qwen.code.agent.llm.ToolDefinition;
import com.alibaba.qwen.code.agent.permission.PermissionManager;
import com.alibaba.qwen.code.agent.tools.ToolRegistry;
import com.alibaba.qwen.code.agent.workflow.LoopGuard;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 编码 Agent 主循环：LLM → 工具调用 → 执行 → 回填 → 继续，直至完成或达到迭代上限。
 *
 * <p>稳定性设计（对齐 qwen-code）：</p>
 * <ul>
 *   <li>轮次上限 {@code maxTurns}（默认取配置 maxIterations）；</li>
 *   <li>死循环防护 {@link LoopGuard}：重复 (tool,args) 调用触发回退提示注入；</li>
 *   <li>停滞防护：连续无进展轮次注入停滞提示；</li>
 *   <li>工具调用链路输入输出 trace（toolCalls 记录 name/args/result/耗时/成功与否）；</li>
 *   <li>工具子集过滤（工作流阶段专用）。</li>
 * </ul>
 */
public final class AgentLoop {

    private final AgentConfig config;
    private final ChatClient chatClient;
    private final ToolRegistry tools;
    private final PermissionManager permissions;
    private final Session session;

    /** 可选的阶段级覆盖：system prompt（null = 默认）。 */
    private final String stageSystemPrompt;
    /** 可选的工具白名单（null = 全部）。 */
    private final List<String> allowedTools;
    /** 可选的工具黑名单（null = 无）。 */
    private final List<String> disallowedTools;
    /** 轮次上限（<=0 = 用配置默认）。 */
    private final int maxTurns;
    /** 死循环/停滞防护（null = 默认实例）。 */
    private final LoopGuard guard;
    /** 执行过程事件监听器（null = 不推送，供 SSE 流式；可运行期替换）。 */
    private volatile AgentEventListener listener;

    /** 本轮工具调用 trace（输入 + 输出）。 */
    private final List<ToolCallTrace> toolTraces = new ArrayList<>();

    public AgentLoop(AgentConfig config, ChatClient chatClient,
                     ToolRegistry tools, PermissionManager permissions, Session session) {
        this(config, chatClient, tools, permissions, session,
                null, null, null, 0, new LoopGuard("main"), null);
    }

    public AgentLoop(AgentConfig config, ChatClient chatClient,
                     ToolRegistry tools, PermissionManager permissions, Session session,
                     String stageSystemPrompt, List<String> allowedTools,
                     List<String> disallowedTools, int maxTurns, LoopGuard guard) {
        this(config, chatClient, tools, permissions, session,
                stageSystemPrompt, allowedTools, disallowedTools, maxTurns, guard, null);
    }

    public AgentLoop(AgentConfig config, ChatClient chatClient,
                     ToolRegistry tools, PermissionManager permissions, Session session,
                     String stageSystemPrompt, List<String> allowedTools,
                     List<String> disallowedTools, int maxTurns, LoopGuard guard,
                     AgentEventListener listener) {
        this.config = config;
        this.chatClient = chatClient;
        this.tools = tools;
        this.permissions = permissions;
        this.session = session;
        this.stageSystemPrompt = stageSystemPrompt;
        this.allowedTools = allowedTools == null || allowedTools.isEmpty()
                ? null : Collections.unmodifiableList(new ArrayList<>(allowedTools));
        this.disallowedTools = disallowedTools == null || disallowedTools.isEmpty()
                ? null : Collections.unmodifiableList(new ArrayList<>(disallowedTools));
        this.maxTurns = maxTurns;
        this.guard = guard;
        this.listener = listener;
    }

    /** 运行期替换事件监听器（同一会话串行执行时安全）。 */
    public void setListener(AgentEventListener listener) {
        this.listener = listener;
    }

    /** 工具调用输入输出记录。 */
    public static final class ToolCallTrace {
        public final String name;
        public final String arguments;
        public final boolean success;
        public final String resultPreview;
        public final long costMs;

        ToolCallTrace(String name, String arguments, boolean success,
                      String resultPreview, long costMs) {
            this.name = name;
            this.arguments = arguments;
            this.success = success;
            this.resultPreview = resultPreview;
            this.costMs = costMs;
        }

        @Override
        public String toString() {
            return "{tool=" + name + ", args=" + truncate(arguments, 120)
                    + ", ok=" + success + ", result=" + truncate(resultPreview, 160)
                    + ", costMs=" + costMs + "}";
        }
    }

    public List<ToolCallTrace> toolTraces() {
        return Collections.unmodifiableList(toolTraces);
    }

    /**
     * 运行一轮完整任务。
     *
     * @return 最终回复文本
     */
    public String run(String userPrompt) {
        List<Message> history = new ArrayList<>(session.messages());
        history.add(Message.system(systemPrompt()));
        history.add(Message.user(userPrompt));

        int limit = maxTurns > 0 ? maxTurns : config.maxIterations();
        String finalAnswer = null;
        for (int i = 0; i < limit; i++) {
            List<ToolDefinition> defs = visibleTools();
            ChatResponse resp = chatClient.chat(history, defs);

            // 记录 assistant 消息（含工具调用）
            List<ToolCall> calls = resp.toolCalls();
            Message assistantMsg = calls.isEmpty()
                    ? Message.assistant(resp.content())
                    : Message.assistantWithToolCalls(resp.content(), calls);
            history.add(assistantMsg);
            session.append(assistantMsg);

            if (!resp.hasToolCalls()) {
                finalAnswer = resp.content();
                if (listener != null && finalAnswer != null && !finalAnswer.isEmpty()) {
                    listener.onText(finalAnswer);
                }
                guard.recordTurn(true, false);
                break;
            }
            guard.recordTurn(resp.content() != null && !resp.content().isEmpty(), true);

            // 执行工具调用
            boolean anyExecuted = false;
            for (ToolCall call : calls) {
                String name = call.name();
                String args = call.arguments();

                // 工具子集过滤
                if (!toolVisible(name)) {
                    Message hidden = Message.toolResult(call.id(), name,
                            "工具 " + name + " 在当前阶段不可用。请改用可用工具或直接给结论。");
                    history.add(hidden);
                    session.append(hidden);
                    continue;
                }

                boolean mutating = tools.isMutating(name);
                if (!permissions.check(name, mutating, args)) {
                    Message denied = Message.toolResult(call.id(), name,
                            "用户拒绝了工具调用 " + name + "。请调整方案或直接给出结论。");
                    history.add(denied);
                    session.append(denied);
                    continue;
                }

                // 死循环防护：重复调用检测
                if (!guard.recordCall(name, args, false)) {
                    Message loop = Message.toolResult(call.id(), name,
                            guard.fallbackHint());
                    history.add(loop);
                    session.append(loop);
                    System.out.println("[防护] 检测到重复调用 " + name + "，已注入回退提示。");
                    continue;
                }

                System.out.println("[工具] " + name + " " + truncate(args, 200));
                if (listener != null) {
                    listener.onToolCall(name, truncate(args, 2000));
                }
                long t0 = System.currentTimeMillis();
                ToolRegistry.ToolResult result = tools.invoke(name, args);
                long cost = System.currentTimeMillis() - t0;
                System.out.println("[工具] 完成 " + name + " (" + cost + " ms, "
                        + (result.success() ? "成功" : "失败") + ")");
                if (listener != null) {
                    listener.onToolResult(name, result.success(), cost,
                            truncate(result.content(), 400));
                }

                // 记录输入输出 trace
                toolTraces.add(new ToolCallTrace(name, args, result.success(),
                        result.content(), cost));
                anyExecuted = true;

                String content = result.render();
                Message toolMsg = Message.toolResult(call.id(), name, content);
                history.add(toolMsg);
                session.append(toolMsg);
            }

            // 停滞防护：本轮没有任何工具被允许执行
            if (!anyExecuted && guard.isStalled()) {
                Message stall = Message.user(guard.stallHint());
                history.add(stall);
                session.append(stall);
                System.out.println("[防护] 检测到停滞，已注入停滞提示。");
            }
        }

        if (finalAnswer == null) {
            finalAnswer = "已达到最大轮次上限 (" + limit
                    + ")，任务未在预算内完成。请缩小任务范围后重试。";
        }
        return finalAnswer;
    }

    private List<ToolDefinition> visibleTools() {
        if (allowedTools == null) {
            return tools.definitions();
        }
        List<ToolDefinition> out = new ArrayList<>();
        for (ToolDefinition d : tools.definitions()) {
            if (allowedTools.contains(d.name())) {
                out.add(d);
            }
        }
        return out;
    }

    private boolean toolVisible(String name) {
        if (disallowedTools != null && disallowedTools.contains(name)) {
            return false;
        }
        return allowedTools == null || allowedTools.contains(name);
    }

    private String systemPrompt() {
        if (stageSystemPrompt != null && !stageSystemPrompt.trim().isEmpty()) {
            return stageSystemPrompt;
        }
        return DEFAULT_SYSTEM_PROMPT;
    }

    private static final String DEFAULT_SYSTEM_PROMPT =
            "You are Qwencode, a terminal coding agent working inside the user's workspace. "
            + "You operate autonomously: inspect files, search code, run commands, and edit files "
            + "to accomplish the user's request.\n"
            + "Rules:\n"
            + "1. Always explore before editing. Read the relevant files first.\n"
            + "2. Prefer targeted edits over full rewrites; preserve existing formatting and comments.\n"
            + "3. After making changes, verify them (run the build / tests when reasonable).\n"
            + "4. Report concisely in the user's language: what you did, what changed, and any risks.\n"
            + "5. Do not invent file paths or command outputs. If a tool fails, adapt.\n"
            + "6. Paths are relative to the workspace root unless stated otherwise.";

    private static String truncate(String s, int max) {
        if (s == null) {
            return "";
        }
        return s.length() > max ? s.substring(0, max) + "..." : s;
    }
}
