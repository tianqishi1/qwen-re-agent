package com.alibaba.qwen.code.agent.llm;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 工具定义（OpenAI tools 协议），供模型发现与调用。
 */
public final class ToolDefinition {

    private final String name;
    private final String description;
    /** JSON Schema（properties / required）。 */
    private final Map<String, Object> parameters;
    /** 是否为修改类操作（用于权限控制）。 */
    private final boolean mutating;

    public ToolDefinition(String name, String description,
                          Map<String, Object> parameters, boolean mutating) {
        this.name = name;
        this.description = description;
        this.parameters = parameters;
        this.mutating = mutating;
    }

    public String name() {
        return name;
    }

    public String description() {
        return description;
    }

    public Map<String, Object> parameters() {
        return parameters;
    }

    public boolean mutating() {
        return mutating;
    }

    /** 转换为 OpenAI tools 协议对象。 */
    public Map<String, Object> toRequestMap() {
        Map<String, Object> fn = new java.util.LinkedHashMap<>();
        fn.put("name", name);
        fn.put("description", description);
        fn.put("parameters", parameters);

        Map<String, Object> m = new java.util.LinkedHashMap<>();
        m.put("type", "function");
        m.put("function", fn);
        return m;
    }

    public static Builder builder(String name, String description) {
        return new Builder(name, description);
    }

    public static final class Builder {
        private final String name;
        private final String description;
        private final Map<String, Object> properties = new java.util.LinkedHashMap<>();
        private final List<String> required = new ArrayList<>();
        private boolean mutating;

        private Builder(String name, String description) {
            this.name = name;
            this.description = description;
        }

        public Builder mutating(boolean v) {
            this.mutating = v;
            return this;
        }

        public Builder stringProperty(String prop, String desc) {
            return stringProperty(prop, desc, false);
        }

        public Builder stringProperty(String prop, String desc, boolean requiredProp) {
            Map<String, Object> s = new java.util.LinkedHashMap<>();
            s.put("type", "string");
            s.put("description", desc);
            properties.put(prop, s);
            if (requiredProp) {
                required.add(prop);
            }
            return this;
        }

        public Builder stringEnumProperty(String prop, String desc, boolean requiredProp,
                                          List<String> enums) {
            Map<String, Object> s = new java.util.LinkedHashMap<>();
            s.put("type", "string");
            s.put("description", desc);
            s.put("enum", enums);
            properties.put(prop, s);
            if (requiredProp) {
                required.add(prop);
            }
            return this;
        }

        public Builder booleanProperty(String prop, String desc) {
            Map<String, Object> s = new java.util.LinkedHashMap<>();
            s.put("type", "boolean");
            s.put("description", desc);
            properties.put(prop, s);
            return this;
        }

        public Builder arrayProperty(String prop, String desc, boolean requiredProp) {
            Map<String, Object> s = new java.util.LinkedHashMap<>();
            s.put("type", "array");
            s.put("description", desc);
            s.put("items", new java.util.LinkedHashMap<String, Object>());
            properties.put(prop, s);
            if (requiredProp) {
                required.add(prop);
            }
            return this;
        }

        public Builder objectProperty(String prop, String desc, boolean requiredProp) {
            Map<String, Object> s = new java.util.LinkedHashMap<>();
            s.put("type", "object");
            s.put("description", desc);
            s.put("additionalProperties", true);
            properties.put(prop, s);
            if (requiredProp) {
                required.add(prop);
            }
            return this;
        }

        public ToolDefinition build() {
            Map<String, Object> schema = new java.util.LinkedHashMap<>();
            schema.put("type", "object");
            schema.put("properties", properties);
            if (!required.isEmpty()) {
                schema.put("required", required);
            }
            return new ToolDefinition(name, description, schema, mutating);
        }
    }
}
