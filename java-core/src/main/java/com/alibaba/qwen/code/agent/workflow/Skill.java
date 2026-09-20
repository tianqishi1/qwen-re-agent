package com.alibaba.qwen.code.agent.workflow;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * 编码工作流阶段 Skill（对齐 qwen-code 的 Subagent/Skill 概念）。
 *
 * <p>每个 Skill 定义：阶段职责、专属 system prompt、可用工具子集、
 * 产出物（artifacts）路径模板、输入约定。运行时由 WorkflowOrchestrator
 * 实例化为一个子代理（独立 AgentLoop + 专属 prompt + 工具子集）。</p>
 */
public final class Skill {

    /** 阶段唯一名。 */
    private final String name;
    /** 阶段说明（供模型与编排器理解用途）。 */
    private final String description;
    /** 该阶段的系统提示词（子代理人格 + 阶段目标 + 输出要求）。 */
    private final String systemPrompt;
    /** 允许使用的工具白名单（null/空 = 继承全部工具）。 */
    private final List<String> allowedTools;
    /** 明确禁止的工具黑名单（如只读阶段禁止 write/edit）。 */
    private final List<String> disallowedTools;
    /** 该阶段产出的文件路径模板（相对工作区根，可含 {stage} 占位）。 */
    private final List<String> artifacts;
    /** 阶段执行超时（分钟），对齐 qwen-code max_time_minutes。 */
    private final int maxTimeMinutes;
    /** 阶段内最大轮次，对齐 qwen-code max_turns。 */
    private final int maxTurns;

    private Skill(Builder b) {
        this.name = b.name;
        this.description = b.description;
        this.systemPrompt = b.systemPrompt;
        this.allowedTools = Collections.unmodifiableList(new ArrayList<>(b.allowedTools));
        this.disallowedTools = Collections.unmodifiableList(new ArrayList<>(b.disallowedTools));
        this.artifacts = Collections.unmodifiableList(new ArrayList<>(b.artifacts));
        this.maxTimeMinutes = b.maxTimeMinutes;
        this.maxTurns = b.maxTurns;
    }

    public String name() {
        return name;
    }

    public String description() {
        return description;
    }

    public String systemPrompt() {
        return systemPrompt;
    }

    public List<String> allowedTools() {
        return allowedTools;
    }

    public List<String> disallowedTools() {
        return disallowedTools;
    }

    public List<String> artifacts() {
        return artifacts;
    }

    public int maxTimeMinutes() {
        return maxTimeMinutes;
    }

    public int maxTurns() {
        return maxTurns;
    }

    public boolean allows(String tool) {
        if (disallowedTools.contains(tool)) {
            return false;
        }
        return allowedTools.isEmpty() || allowedTools.contains(tool);
    }

    public static Builder builder(String name, String description) {
        return new Builder(name, description);
    }

    public static final class Builder {
        private final String name;
        private final String description;
        private String systemPrompt = "";
        private final List<String> allowedTools = new ArrayList<>();
        private final List<String> disallowedTools = new ArrayList<>();
        private final List<String> artifacts = new ArrayList<>();
        private int maxTimeMinutes = 10;
        private int maxTurns = 30;

        private Builder(String name, String description) {
            this.name = name;
            this.description = description;
        }

        public Builder systemPrompt(String v) {
            this.systemPrompt = v;
            return this;
        }

        public Builder allow(String... tools) {
            allowedTools.addAll(Arrays.asList(tools));
            return this;
        }

        public Builder disallow(String... tools) {
            disallowedTools.addAll(Arrays.asList(tools));
            return this;
        }

        public Builder artifacts(String... paths) {
            artifacts.addAll(Arrays.asList(paths));
            return this;
        }

        public Builder maxTimeMinutes(int v) {
            this.maxTimeMinutes = v;
            return this;
        }

        public Builder maxTurns(int v) {
            this.maxTurns = v;
            return this;
        }

        public Skill build() {
            if (name == null || name.trim().isEmpty()) {
                throw new IllegalArgumentException("Skill name 不能为空");
            }
            return new Skill(this);
        }
    }
}
