package com.alibaba.qwen.code.agent.workflow;

import com.alibaba.qwen.code.agent.config.AgentConfig;
import com.alibaba.qwen.code.agent.core.AgentLoop;
import com.alibaba.qwen.code.agent.core.Session;
import com.alibaba.qwen.code.agent.llm.ChatClient;
import com.alibaba.qwen.code.agent.permission.PermissionManager;
import com.alibaba.qwen.code.agent.tools.ToolRegistry;

import java.util.ArrayList;
import java.util.List;

/**
 * 编码工作流编排器：按阶段顺序执行 Skill，跨阶段传递上下文，汇总结果。
 *
 * <p>对齐 qwen-code 的 WorkflowOrchestrator 设计：</p>
 * <ul>
 *   <li>每阶段 = 一个子代理（独立 AgentLoop + 该 Skill 的 systemPrompt + 工具子集）；</li>
 *   <li>阶段间通过产出物文件传递上下文（需求文档 → 方案文档 → 代码 → 报告）；</li>
 *   <li>每阶段有独立 LoopGuard（死循环/停滞防护）与轮次上限；</li>
 *   <li>编排器维护全局预算（阶段数上限）与结果汇总。</li>
 * </ul>
 */
public final class WorkflowOrchestrator {

    private final AgentConfig config;
    private final ChatClient chatClient;
    private final PythonBridgeHandle bridge;
    private final int maxStages;

    /** Python 桥的窄接口，避免编排器直接依赖桥实现细节。 */
    public interface PythonBridgeHandle {
        ToolRegistry newToolRegistry();
    }

    public WorkflowOrchestrator(AgentConfig config, ChatClient chatClient,
                                PythonBridgeHandle bridge) {
        this(config, chatClient, bridge, 8);
    }

    public WorkflowOrchestrator(AgentConfig config, ChatClient chatClient,
                                PythonBridgeHandle bridge, int maxStages) {
        this.config = config;
        this.chatClient = chatClient;
        this.bridge = bridge;
        this.maxStages = maxStages;
    }

    /**
     * 运行完整流水线。
     *
     * @param idea 用户输入的想法/需求
     * @param stages 要执行的阶段名列表（null = 默认全流水线）
     * @return 编排结果（每阶段摘要 + 汇总）
     */
    public WorkflowResult run(String idea, List<String> stages) {
        List<String> stageNames = stages == null || stages.isEmpty()
                ? BuiltinSkills.PIPELINE : stages;
        if (stageNames.size() > maxStages) {
            throw new IllegalArgumentException(
                    "阶段数 " + stageNames.size() + " 超过上限 " + maxStages);
        }

        List<StageResult> results = new ArrayList<>();
        StringBuilder context = new StringBuilder();
        context.append("用户原始想法：").append(idea).append("\n");

        for (String stageName : stageNames) {
            Skill skill = BuiltinSkills.get(stageName);
            if (skill == null) {
                throw new IllegalArgumentException("未知阶段: " + stageName
                        + "，可用: " + BuiltinSkills.PIPELINE);
            }
            System.out.println();
            System.out.println("══════════════════════════════════════════");
            System.out.println("▶ 阶段 [" + skill.name() + "]  " + skill.description());
            System.out.println("══════════════════════════════════════════");

            StageResult result = executeStage(skill, context.toString());
            results.add(result);

            // 传递上下文给下一阶段
            context.append("\n[阶段 ").append(skill.name()).append(" 产出]\n");
            context.append(result.summary).append("\n");
            context.append("产出物: ").append(result.artifacts).append("\n");
            if (!result.success) {
                System.out.println("[编排] 阶段 " + skill.name() + " 未成功，终止流水线。");
                return WorkflowResult.failed(idea, results);
            }
        }

        return WorkflowResult.success(idea, results);
    }

    /** 执行单个阶段（子代理）。 */
    private StageResult executeStage(Skill skill, String context) {
        Session session = new Session();
        ToolRegistry tools = bridge.newToolRegistry();
        PermissionManager permissions = new PermissionManager(config);
        LoopGuard guard = new LoopGuard(skill.name(), 3, 4);

        String stagePrompt = buildStagePrompt(skill, context);
        AgentLoop loop = new AgentLoop(config, chatClient, tools, permissions, session,
                skill.systemPrompt(), skill.allowedTools(), skill.disallowedTools(),
                skill.maxTurns(), guard);

        long t0 = System.currentTimeMillis();
        String answer;
        boolean success = true;
        try {
            answer = loop.run(stagePrompt);
        } catch (Exception e) {
            answer = "阶段执行异常: " + e.getMessage();
            success = false;
        }
        long costSec = (System.currentTimeMillis() - t0) / 1000;

        List<String> artifacts = new ArrayList<>();
        for (String a : skill.artifacts()) {
            artifacts.add(a);
        }

        return new StageResult(skill.name(), success, answer, artifacts, costSec,
                guard.hasHints(), guard.isStalled());
    }

    private static String buildStagePrompt(Skill skill, String context) {
        StringBuilder sb = new StringBuilder();
        sb.append("以下是本工作流已积累的上下文：\n")
                .append("----------------------------------------\n")
                .append(context)
                .append("----------------------------------------\n\n")
                .append("现在执行阶段「").append(skill.name()).append("」。\n")
                .append("阶段要求：").append(skill.description()).append("\n");
        if (!skill.artifacts().isEmpty()) {
            sb.append("本阶段必须产出以下文件（写入工作区）：").append(skill.artifacts()).append("\n");
        }
        return sb.toString();
    }

    /** 单个阶段的执行结果。 */
    public static final class StageResult {
        public final String stage;
        public final boolean success;
        public final String summary;
        public final List<String> artifacts;
        public final long costSeconds;
        public final boolean loopProtected;
        public final boolean stalled;

        StageResult(String stage, boolean success, String summary,
                    List<String> artifacts, long costSeconds,
                    boolean loopProtected, boolean stalled) {
            this.stage = stage;
            this.success = success;
            this.summary = summary;
            this.artifacts = artifacts;
            this.costSeconds = costSeconds;
            this.loopProtected = loopProtected;
            this.stalled = stalled;
        }
    }

    /** 整体编排结果。 */
    public static final class WorkflowResult {
        public final boolean success;
        public final String idea;
        public final List<StageResult> stages;

        private WorkflowResult(boolean success, String idea, List<StageResult> stages) {
            this.success = success;
            this.idea = idea;
            this.stages = stages;
        }

        static WorkflowResult success(String idea, List<StageResult> stages) {
            return new WorkflowResult(true, idea, stages);
        }

        static WorkflowResult failed(String idea, List<StageResult> stages) {
            return new WorkflowResult(false, idea, stages);
        }
    }
}
