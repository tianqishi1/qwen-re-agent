package com.alibaba.qwen.code.agent.llm;

import com.alibaba.qwen.code.agent.core.ToolCall;

import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * 模型响应：文本内容 + 工具调用 + 用量。
 */
public final class ChatResponse {

    private final String content;
    private final List<ToolCall> toolCalls;
    private final Map<String, Object> usage;

    private ChatResponse(String content, List<ToolCall> toolCalls, Map<String, Object> usage) {
        this.content = content;
        this.toolCalls = toolCalls == null ? Collections.emptyList() : toolCalls;
        this.usage = usage == null ? Collections.emptyMap() : usage;
    }

    public static ChatResponse of(String content, List<ToolCall> toolCalls,
                                  Map<String, Object> usage) {
        return new ChatResponse(content, toolCalls, usage);
    }

    public String content() {
        return content;
    }

    public List<ToolCall> toolCalls() {
        return toolCalls;
    }

    public boolean hasToolCalls() {
        return !toolCalls.isEmpty();
    }

    public Map<String, Object> usage() {
        return usage;
    }

    @Override
    public String toString() {
        return "ChatResponse{content=" + (content == null ? "null"
                : content.length() > 100 ? content.substring(0, 100) + "..." : content)
                + ", toolCalls=" + toolCalls.size() + '}';
    }
}
