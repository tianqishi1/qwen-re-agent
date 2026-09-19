package com.alibaba.qwen.code.agent.config;

import java.io.File;
import java.nio.file.Path;
import java.nio.file.Paths;

/**
 * 编码 Agent 运行配置。
 *
 * <p>来源优先级：命令行参数 &gt; 环境变量 &gt; 默认值。</p>
 */
public final class AgentConfig {

    /** LLM 网关 Base URL（OpenAI 兼容 /chat/completions）。 */
    private final String baseUrl;
    /** API Key。 */
    private final String apiKey;
    /** 模型名称。 */
    private final String model;
    /** 工作目录（agent 的操作根目录，同时是 Python 执行层的沙箱根）。 */
    private final Path workspace;
    /** 权限模式：auto（自动批准）/ plan（只读+询问）/ approve（每次询问）。 */
    private final PermissionMode permissionMode;
    /** 单轮任务最大工具调用迭代次数。 */
    private final int maxIterations;
    /** 是否流式输出。 */
    private final boolean stream;
    /** Python 解释器命令。 */
    private final String pythonCommand;
    /** Python 运行时模块根（qwen_agent_runtime 所在目录）。 */
    private final Path pythonRuntimeDir;

    private AgentConfig(Builder b) {
        this.baseUrl = normalizeBaseUrl(b.baseUrl);
        this.apiKey = b.apiKey;
        this.model = b.model;
        this.workspace = b.workspace.toAbsolutePath().normalize();
        this.permissionMode = b.permissionMode;
        this.maxIterations = b.maxIterations;
        this.stream = b.stream;
        this.pythonCommand = b.pythonCommand;
        this.pythonRuntimeDir = b.pythonRuntimeDir != null
                ? b.pythonRuntimeDir.toAbsolutePath().normalize()
                : defaultPythonRuntimeDir();
    }

    private static String normalizeBaseUrl(String url) {
        if (url == null || url.trim().isEmpty()) {
            return "https://dashscope.aliyuncs.com/compatible-mode/v1";
        }
        String u = url.trim();
        while (u.endsWith("/")) {
            u = u.substring(0, u.length() - 1);
        }
        return u;
    }

    private static Path defaultPythonRuntimeDir() {
        // 优先按 jar 位置推断：<root>/java-core/target/xxx.jar -> <root>/python-runtime
        try {
            java.security.CodeSource cs = AgentConfig.class.getProtectionDomain().getCodeSource();
            if (cs != null && cs.getLocation() != null) {
                java.net.URI uri = cs.getLocation().toURI();
                if ("file".equals(uri.getScheme())) {
                    Path loc = Paths.get(uri);
                    // loc 可能是 jar 文件（target/xxx.jar）或 classes 目录
                    Path dir = loc.toFile().isFile() ? loc.getParent() : loc;
                    // 向上找到 java-core 的上一级（仓库根）
                    Path p = dir;
                    while (p != null && p.getParent() != null
                            && !"java-core".equalsIgnoreCase(p.getFileName().toString())) {
                        p = p.getParent();
                    }
                    if (p != null && p.getParent() != null) {
                        Path candidate = p.getParent().resolve("python-runtime");
                        if (candidate.toFile().isDirectory()) {
                            return candidate.normalize();
                        }
                    }
                }
            }
        } catch (Exception ignore) {
            // 回退到 user.dir 推断
        }
        // 约定：java-core 与 python-runtime 同级（本仓库布局）
        Path coreDir = Paths.get(System.getProperty("user.dir")).toAbsolutePath();
        Path parent = coreDir.getParent();
        if (parent != null) {
            Path candidate = parent.resolve("python-runtime");
            if (candidate.toFile().isDirectory()) {
                return candidate.normalize();
            }
        }
        return coreDir.resolve("python-runtime").normalize();
    }

    public String baseUrl() {
        return baseUrl;
    }

    public String apiKey() {
        return apiKey;
    }

    public String model() {
        return model;
    }

    public Path workspace() {
        return workspace;
    }

    public String workspaceString() {
        return workspace.toString();
    }

    public PermissionMode permissionMode() {
        return permissionMode;
    }

    public int maxIterations() {
        return maxIterations;
    }

    public boolean stream() {
        return stream;
    }

    public String pythonCommand() {
        return pythonCommand;
    }

    public Path pythonRuntimeDir() {
        return pythonRuntimeDir;
    }

    public String pythonRuntimeDirString() {
        return pythonRuntimeDir.toString();
    }

    /** 权限模式。 */
    public enum PermissionMode {
        /** 自动批准所有安全工具调用。 */
        AUTO,
        /** 只读工具自动放行，修改类操作询问用户。 */
        PLAN,
        /** 每次工具调用都询问用户。 */
        APPROVE
    }

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private String baseUrl;
        private String apiKey;
        private String model = "qwen3-coder-plus";
        private Path workspace = Paths.get(System.getProperty("user.dir"));
        private PermissionMode permissionMode = PermissionMode.AUTO;
        private int maxIterations = 20;
        private boolean stream = true;
        private String pythonCommand = "python";
        private Path pythonRuntimeDir;

        public Builder baseUrl(String v) {
            this.baseUrl = v;
            return this;
        }

        public Builder apiKey(String v) {
            this.apiKey = v;
            return this;
        }

        public Builder model(String v) {
            this.model = v;
            return this;
        }

        public Builder workspace(String v) {
            if (v != null && !v.trim().isEmpty()) {
                this.workspace = Paths.get(v);
            }
            return this;
        }

        public Builder permissionMode(PermissionMode v) {
            this.permissionMode = v;
            return this;
        }

        public Builder maxIterations(int v) {
            this.maxIterations = v;
            return this;
        }

        public Builder stream(boolean v) {
            this.stream = v;
            return this;
        }

        public Builder pythonCommand(String v) {
            this.pythonCommand = v;
            return this;
        }

        public Builder pythonRuntimeDir(String v) {
            if (v != null && !v.trim().isEmpty()) {
                this.pythonRuntimeDir = Paths.get(v);
            }
            return this;
        }

        public AgentConfig build() {
            if (apiKey == null || apiKey.trim().isEmpty()) {
                throw new IllegalArgumentException(
                        "缺少 API Key：请通过 --api-key 参数或 QWEN_API_KEY 环境变量提供。");
            }
            if (model == null || model.trim().isEmpty()) {
                throw new IllegalArgumentException("模型名称不能为空。");
            }
            File ws = workspace.toFile();
            if (!ws.exists()) {
                throw new IllegalArgumentException("工作目录不存在: " + workspace);
            }
            if (!ws.isDirectory()) {
                throw new IllegalArgumentException("工作目录不是目录: " + workspace);
            }
            return new AgentConfig(this);
        }
    }
}
