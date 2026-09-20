package com.alibaba.qwen.code.agent.service.core;

import com.alibaba.qwen.code.agent.bridge.ToolBridge;
import com.alibaba.qwen.code.agent.config.AgentConfig;
import com.alibaba.qwen.code.agent.llm.ChatClient;
import com.alibaba.qwen.code.agent.permission.PermissionManager;
import com.alibaba.qwen.code.agent.tools.ToolRegistry;
import com.alibaba.qwen.code.agent.workflow.WorkflowOrchestrator;

/**
 * 核心引擎装配（供服务层使用）。
 *
 * <p>引擎与桥接口解耦：executor 既可是 stdio 子进程（CLI 模式）也可是 HTTP 微服务（本服务）。</p>
 */
public final class AgentEngine {

    private final AgentConfig config;
    private final ChatClient chatClient;
    private final ToolBridge bridge;
    private final WorkflowOrchestrator orchestrator;

    public AgentEngine(AgentConfig config, ToolBridge bridge) {
        this.config = config;
        this.chatClient = new ChatClient(config);
        this.bridge = bridge;
        this.orchestrator = new WorkflowOrchestrator(config, chatClient, bridge::newToolRegistry, 8);
    }

    public AgentConfig config() {
        return config;
    }

    public ChatClient chatClient() {
        return chatClient;
    }

    public ToolBridge bridge() {
        return bridge;
    }

    /** 带事件监听的编排器实例（每个请求独立，避免共享监听器状态）。 */
    public WorkflowOrchestrator newOrchestrator(
            com.alibaba.qwen.code.agent.core.AgentEventListener listener) {
        return new WorkflowOrchestrator(config, chatClient, bridge::newToolRegistry, 8, listener);
    }

    /** 会话级工具注册表（复用同一执行层桥）。 */
    public ToolRegistry newToolRegistry() {
        return bridge.newToolRegistry();
    }

    public PermissionManager newPermissionManager() {
        return new PermissionManager(config);
    }
}
