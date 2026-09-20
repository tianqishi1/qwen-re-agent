package com.alibaba.qwen.code.agent.service.config;

import com.alibaba.qwen.code.agent.bridge.ToolBridge;
import com.alibaba.qwen.code.agent.config.AgentConfig;
import com.alibaba.qwen.code.agent.config.ConfigFile;
import com.alibaba.qwen.code.agent.service.core.AgentEngine;
import com.alibaba.qwen.code.agent.service.executor.ExecutorManager;
import com.alibaba.qwen.code.agent.service.session.AgentSession;
import com.alibaba.qwen.code.agent.service.session.SessionManager;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.event.EventListener;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * 核心装配：AgentConfig → ExecutorManager → AgentEngine → SessionManager。
 *
 * <p>配置来源优先级：application.yml（qwencode.*）&gt; qwencode.properties &gt; 默认值。</p>
 */
@Configuration
public class AgentServiceConfig {

    private static final Logger log = LoggerFactory.getLogger(AgentServiceConfig.class);

    @Value("${qwencode.config-file:}")
    private String configFile;

    @Value("${qwencode.workspace:}")
    private String workspaceOverride;

    @Value("${qwencode.executor.host:127.0.0.1}")
    private String executorHost;

    @Value("${qwencode.executor.port:8910}")
    private int executorPort;

    @Value("${qwencode.executor.auto-start:true}")
    private boolean executorAutoStart;

    @Value("${qwencode.executor.startup-timeout-seconds:30}")
    private int executorStartupTimeout;

    @Value("${qwencode.worker-pool-size:4}")
    private int workerPoolSize;

    @Bean
    public AgentConfig agentConfig() {
        ConfigFile cf = ConfigFile.load(blankToNull(configFile));
        AgentConfig.Builder b = AgentConfig.builder();
        if (cf != null) {
            if (cf.has("api-key")) {
                b.apiKey(cf.get("api-key"));
            }
            if (cf.has("base-url")) {
                b.baseUrl(cf.get("base-url"));
            }
            if (cf.has("model")) {
                b.model(cf.get("model"));
            }
            if (cf.has("permission")) {
                String p = cf.get("permission").trim().toLowerCase();
                if ("plan".equals(p)) {
                    b.permissionMode(AgentConfig.PermissionMode.PLAN);
                } else if ("approve".equals(p)) {
                    b.permissionMode(AgentConfig.PermissionMode.APPROVE);
                } else {
                    b.permissionMode(AgentConfig.PermissionMode.AUTO);
                }
            }
            if (cf.has("max-iterations")) {
                try {
                    b.maxIterations(Integer.parseInt(cf.get("max-iterations").trim()));
                } catch (NumberFormatException ignore) {
                    // 使用默认
                }
            }
            if (cf.has("python")) {
                b.pythonCommand(cf.get("python"));
            }
            if (cf.has("workspace") && !cf.get("workspace").trim().isEmpty()) {
                b.workspace(cf.get("workspace").trim());
            }
            log.info("[配置] 加载 {}", cf.source());
        } else {
            log.warn("[配置] 未找到 qwencode.properties，使用默认值（需 API Key）");
        }
        // 优先级：application.yml qwencode.workspace > properties workspace > 默认样例工作区
        String ws = workspaceOverride != null ? workspaceOverride.trim() : "";
        if (ws.isEmpty() && cf != null && cf.has("workspace") && !cf.get("workspace").trim().isEmpty()) {
            ws = cf.get("workspace").trim();
        }
        if (!ws.isEmpty()) {
            b.workspace(ws);
        } else {
            b.workspace(defaultWorkspace());
        }
        AgentConfig config = b.build();
        ensureWorkspace(config.workspace());
        log.info("[配置] workspace={} model={} baseUrl={}", config.workspace(), config.model(), config.baseUrl());
        return config;
    }

    /** 默认工作区：优先已存在的 sample-workspace，否则 user.dir/agent-workspace（自动创建）。 */
    private static String defaultWorkspace() {
        Path userDir = Paths.get(System.getProperty("user.dir")).toAbsolutePath();
        Path sample = userDir.resolve("sample-workspace");
        if (Files.isDirectory(sample)) {
            return sample.toString();
        }
        return userDir.resolve("agent-workspace").toString();
    }

    @Bean
    public ExecutorManager executorManager(AgentConfig config) {
        return new ExecutorManager(executorHost, executorPort, executorAutoStart,
                executorStartupTimeout, config.pythonCommand(),
                config.pythonRuntimeDirString(), config.workspaceString());
    }

    @Bean
    public AgentEngine agentEngine(AgentConfig config, ExecutorManager executorManager)
            throws IOException, InterruptedException {
        executorManager.start();
        ToolBridge bridge = executorManager.bridge();
        return new AgentEngine(config, bridge);
    }

    @Bean
    public SessionManager sessionManager(AgentEngine engine) {
        return new SessionManager(() -> new AgentSession(
                new com.alibaba.qwen.code.agent.core.AgentLoop(
                        engine.config(), engine.chatClient(), engine.newToolRegistry(),
                        engine.newPermissionManager(), new com.alibaba.qwen.code.agent.core.Session())));
    }

    @Bean
    public ExecutorService agentWorkerPool() {
        return Executors.newFixedThreadPool(Math.max(1, workerPoolSize), r -> {
            Thread t = new Thread(r, "agent-worker");
            t.setDaemon(true);
            return t;
        });
    }

    @EventListener(ApplicationReadyEvent.class)
    public void onReady() {
        log.info("┌──────────────────────────────────────────────┐");
        log.info("│  Qwencode Agent 服务已就绪                    │");
        log.info("│  前端页面: http://127.0.0.1:{}               │", System.getProperty("server.port", "8800"));
        log.info("│  Executor: http://{}:{}                      │", executorHost, executorPort);
        log.info("└──────────────────────────────────────────────┘");
    }

    private static String blankToNull(String s) {
        return s == null || s.trim().isEmpty() ? null : s.trim();
    }

    private static void ensureWorkspace(Path ws) {
        if (!Files.exists(ws)) {
            try {
                Files.createDirectories(ws);
                log.info("[配置] 已创建工作区 {}", ws);
            } catch (IOException e) {
                throw new IllegalStateException("无法创建工作区: " + ws, e);
            }
        }
    }
}
