package com.alibaba.qwen.code.agent.tools;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.alibaba.qwen.code.agent.bridge.PythonBridge;
import com.alibaba.qwen.code.agent.llm.ToolDefinition;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 工具注册表：维护工具定义、解析模型调用、委派 Python 执行层。
 */
public final class ToolRegistry {

    private final Map<String, ToolEntry> tools = new LinkedHashMap<>();
    private final PythonBridge bridge;
    private final long defaultTimeoutMs;

    public ToolRegistry(PythonBridge bridge, long defaultTimeoutMs) {
        this.bridge = bridge;
        this.defaultTimeoutMs = defaultTimeoutMs;
        registerDefaults();
    }

    /** 注册内置工具。 */
    private void registerDefaults() {
        register(buildTool("list_dir",
                "List entries of a directory. Returns names, types (file/dir) and sizes.",
                false)
                .stringProperty("path", "Directory path relative to workspace, or '.' for workspace root.", true)
                .build());
        register(buildTool("read_file",
                "Read a file with optional line range. Content is truncated if too long.",
                false)
                .stringProperty("path", "File path relative to workspace.", true)
                .stringProperty("start_line", "1-based start line (inclusive).", false)
                .stringProperty("end_line", "1-based end line (inclusive).", false)
                .build());
        register(buildTool("write_file",
                "Write content to a file (overwrites). Creates parent directories.",
                true)
                .stringProperty("path", "File path relative to workspace.", true)
                .stringProperty("content", "Full file content to write.", true)
                .build());
        register(buildTool("edit_file",
                "Apply an exact-string replacement to a file. old_text must appear exactly once.",
                true)
                .stringProperty("path", "File path relative to workspace.", true)
                .stringProperty("old_text", "Exact text to find (must be unique in file).", true)
                .stringProperty("new_text", "Replacement text.", true)
                .build());
        register(buildTool("glob",
                "Find files matching a glob pattern (e.g. **/*.java).",
                false)
                .stringProperty("pattern", "Glob pattern.", true)
                .stringProperty("path", "Base directory, default workspace root.", false)
                .build());
        register(buildTool("grep",
                "Search file contents by regular expression. Returns matching lines with line numbers.",
                false)
                .stringProperty("pattern", "Regular expression to search.", true)
                .stringProperty("path", "Base directory, default workspace root.", false)
                .stringProperty("glob", "Optional file glob filter (e.g. *.java).", false)
                .build());
        register(buildTool("run_command",
                "Run a shell command in the workspace. Output truncated to 30000 chars.",
                true)
                .stringProperty("command", "Shell command to run.", true)
                .stringProperty("timeout_seconds", "Timeout in seconds (default 60, max 300).", false)
                .stringProperty("cwd", "Working directory relative to workspace.", false)
                .build());
        register(buildTool("get_workspace_info",
                "Return workspace root, OS type and current user. Cheap introspection.",
                false)
                .build());
        register(buildTool("todo_write",
                "Create/manage a concise user-visible task list for complex multi-step work. "
                        + "todos: array of {content,status,id?,blockedBy?}, status in pending/in_progress/completed. "
                        + "mode: 'replace' (default) or 'merge'.",
                true)
                .arrayProperty("todos", "Task list items.", true)
                .stringProperty("mode", "'replace' or 'merge'.", false)
                .build());
        register(buildTool("todo_list",
                "Read the current task list. Returns todos with status, totals.",
                false)
                .build());
    }

    private ToolDefinition.Builder buildTool(String name, String desc, boolean mutating) {
        return ToolDefinition.builder(name, desc).mutating(mutating);
    }

    public void register(ToolDefinition def) {
        tools.put(def.name(), new ToolEntry(def));
    }

    public List<ToolDefinition> definitions() {
        List<ToolDefinition> out = new ArrayList<>();
        for (ToolEntry e : tools.values()) {
            out.add(e.definition);
        }
        return Collections.unmodifiableList(out);
    }

    public boolean contains(String name) {
        return tools.containsKey(name);
    }

    public boolean isMutating(String name) {
        ToolEntry e = tools.get(name);
        return e != null && e.definition.mutating();
    }

    /**
     * 执行工具调用。
     *
     * @param name 工具名
     * @param argumentsJson 模型给出的 arguments JSON 字符串
     * @return 工具结果文本（作为 tool 消息回填）
     */
    public ToolResult invoke(String name, String argumentsJson) {
        ToolEntry entry = tools.get(name);
        if (entry == null) {
            return ToolResult.error("未知工具: " + name + "，可用工具: " + tools.keySet());
        }
        Map<String, Object> params;
        try {
            JSONObject parsed = JSON.parseObject(argumentsJson);
            params = parsed == null ? new LinkedHashMap<>() : parsed;
        } catch (Exception e) {
            return ToolResult.error("工具参数解析失败: " + e.getMessage());
        }
        try {
            JSONObject resp = bridge.call(name, params, defaultTimeoutMs);
            if (Boolean.TRUE.equals(resp.getBoolean("ok"))) {
                Object result = resp.get("result");
                return ToolResult.success(result == null ? "" : JSON.toJSONString(result));
            }
            String error = resp.getString("error");
            return ToolResult.error(error == null ? "未知错误" : error);
        } catch (PythonBridge.BridgeTimeoutException e) {
            return ToolResult.error("工具执行超时: " + e.getMessage());
        } catch (PythonBridge.BridgeException e) {
            return ToolResult.error("工具执行失败: " + e.getMessage());
        }
    }

    /** 工具执行结果。 */
    public static final class ToolResult {
        private final boolean success;
        private final String content;

        private ToolResult(boolean success, String content) {
            this.success = success;
            this.content = content;
        }

        public static ToolResult success(String content) {
            return new ToolResult(true, content);
        }

        public static ToolResult error(String content) {
            return new ToolResult(false, content);
        }

        public boolean success() {
            return success;
        }

        public String content() {
            return content;
        }

        public String render() {
            return success ? content : "工具执行失败: " + content;
        }
    }

    private static final class ToolEntry {
        final ToolDefinition definition;

        ToolEntry(ToolDefinition definition) {
            this.definition = definition;
        }
    }
}
