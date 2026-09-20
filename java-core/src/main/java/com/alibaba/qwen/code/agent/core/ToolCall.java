package com.alibaba.qwen.code.agent.core;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 模型输出的工具调用（OpenAI function calling 格式）。
 */
public final class ToolCall {

    private final String id;
    private final String name;
    private final String arguments;

    public ToolCall(String id, String name, String arguments) {
        this.id = id;
        this.name = name;
        this.arguments = arguments;
    }

    public String id() {
        return id;
    }

    public String name() {
        return name;
    }

    public String arguments() {
        return arguments;
    }

    public Map<String, Object> toRequestMap() {
        Map<String, Object> fn = new LinkedHashMap<>();
        fn.put("name", name);
        fn.put("arguments", arguments == null ? "" : arguments);

        Map<String, Object> m = new LinkedHashMap<>();
        m.put("id", id);
        m.put("type", "function");
        m.put("function", fn);
        return m;
    }

    @Override
    public String toString() {
        return "ToolCall{id=" + id + ", name=" + name + ", arguments=" + arguments + '}';
    }
}
