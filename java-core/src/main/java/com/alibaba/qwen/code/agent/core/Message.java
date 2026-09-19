package com.alibaba.qwen.code.agent.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * 会话消息模型（OpenAI Chat Completions 兼容）。
 */
public final class Message {

    /** 消息角色。 */
    public enum Role {
        SYSTEM,
        USER,
        ASSISTANT,
        TOOL
    }

    private final Role role;
    private final String content;
    /** assistant 消息中的工具调用（tool_calls）。 */
    private final List<ToolCall> toolCalls;
    /** 工具调用 id（仅 TOOL 角色）。 */
    private final String toolCallId;
    /** 工具名称（仅 TOOL 角色，冗余便于排查）。 */
    private final String toolName;

    private Message(Role role, String content, List<ToolCall> toolCalls,
                    String toolCallId, String toolName) {
        this.role = role;
        this.content = content;
        this.toolCalls = toolCalls == null ? Collections.emptyList() : toolCalls;
        this.toolCallId = toolCallId;
        this.toolName = toolName;
    }

    public static Message system(String content) {
        return new Message(Role.SYSTEM, content, null, null, null);
    }

    public static Message user(String content) {
        return new Message(Role.USER, content, null, null, null);
    }

    public static Message assistant(String content) {
        return new Message(Role.ASSISTANT, content, null, null, null);
    }

    public static Message assistantWithToolCalls(String content, List<ToolCall> calls) {
        return new Message(Role.ASSISTANT, content, calls, null, null);
    }

    public static Message toolResult(String toolCallId, String toolName, String content) {
        return new Message(Role.TOOL, content, null, toolCallId, toolName);
    }

    public Role role() {
        return role;
    }

    public String content() {
        return content;
    }

    public List<ToolCall> toolCalls() {
        return toolCalls;
    }

    public String toolCallId() {
        return toolCallId;
    }

    public String toolName() {
        return toolName;
    }

    public boolean hasToolCalls() {
        return !toolCalls.isEmpty();
    }

    /** 转换为 OpenAI 协议 JSON（供请求体使用）。 */
    public Map<String, Object> toRequestMap() {
        Map<String, Object> m = new java.util.LinkedHashMap<>();
        switch (role) {
            case SYSTEM:
                m.put("role", "system");
                m.put("content", content == null ? "" : content);
                break;
            case USER:
                m.put("role", "user");
                m.put("content", content == null ? "" : content);
                break;
            case ASSISTANT:
                m.put("role", "assistant");
                if (content != null) {
                    m.put("content", content);
                } else {
                    m.put("content", "");
                }
                if (!toolCalls.isEmpty()) {
                    List<Map<String, Object>> calls = new ArrayList<>();
                    for (ToolCall tc : toolCalls) {
                        calls.add(tc.toRequestMap());
                    }
                    m.put("tool_calls", calls);
                }
                break;
            case TOOL:
                m.put("role", "tool");
                m.put("tool_call_id", toolCallId);
                m.put("content", content == null ? "" : content);
                break;
            default:
                throw new IllegalStateException("未知角色: " + role);
        }
        return m;
    }

    @Override
    public String toString() {
        return "Message{role=" + role
                + ", content=" + (content == null ? "null" : content.length() > 120
                        ? content.substring(0, 120) + "..." : content)
                + ", toolCalls=" + toolCalls.size()
                + (toolCallId != null ? ", toolCallId=" + toolCallId : "")
                + '}';
    }
}
