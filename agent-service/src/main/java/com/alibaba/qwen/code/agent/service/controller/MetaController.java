package com.alibaba.qwen.code.agent.service.controller;

import com.alibaba.qwen.code.agent.bridge.ToolBridge;
import com.alibaba.qwen.code.agent.config.AgentConfig;
import com.alibaba.qwen.code.agent.service.core.AgentEngine;
import com.alibaba.qwen.code.agent.service.executor.ExecutorManager;
import com.alibaba.qwen.code.agent.service.session.SessionManager;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 元信息 API：服务健康、引擎配置、会话与执行器状态。
 */
@RestController
@RequestMapping("/api")
public class MetaController {

    private final AgentEngine engine;
    private final ExecutorManager executor;
    private final SessionManager sessions;

    public MetaController(AgentEngine engine, ExecutorManager executor, SessionManager sessions) {
        this.engine = engine;
        this.executor = executor;
        this.sessions = sessions;
    }

    @GetMapping("/config")
    public Map<String, Object> config() {
        AgentConfig c = engine.config();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", true);
        out.put("model", c.model());
        out.put("baseUrl", c.baseUrl());
        out.put("workspace", c.workspaceString());
        out.put("permission", c.permissionMode().name().toLowerCase());
        out.put("maxIterations", c.maxIterations());
        out.put("stream", c.stream());
        out.put("executor", executor.baseUrl());
        return out;
    }

    @GetMapping("/health")
    public Map<String, Object> health() {
        Map<String, Object> out = new LinkedHashMap<>();
        boolean executorOk;
        try {
            executorOk = executor.bridge().ping(3_000);
        } catch (Exception e) {
            executorOk = false;
        }
        out.put("ok", true);
        out.put("status", executorOk ? "UP" : "DEGRADED");
        out.put("executorOk", executorOk);
        out.put("sessions", sessions.size());
        return out;
    }

    @GetMapping("/sessions")
    public Map<String, Object> sessions() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", true);
        out.put("sessions", sessions.size());
        return out;
    }
}
