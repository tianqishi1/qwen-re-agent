package com.alibaba.qwen.code.agent.service.session;

import com.alibaba.qwen.code.agent.core.AgentLoop;
import com.alibaba.qwen.code.agent.tools.ToolRegistry;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 会话注册表：创建、查找、清理 Agent 会话（线程安全）。
 */
public final class SessionManager {

    private final Map<String, AgentSession> sessions = new ConcurrentHashMap<>();
    private final SessionFactory factory;

    /** 会话工厂：由 AgentEngine 提供（每会话独立 ToolRegistry + AgentLoop）。 */
    public interface SessionFactory {
        AgentSession create();
    }

    public SessionManager(SessionFactory factory) {
        this.factory = factory;
    }

    public AgentSession create() {
        AgentSession s = factory.create();
        sessions.put(s.id(), s);
        return s;
    }

    public AgentSession get(String id) {
        return id == null ? null : sessions.get(id);
    }

    public AgentSession getOrCreate(String id) {
        if (id != null && sessions.containsKey(id)) {
            return sessions.get(id);
        }
        return create();
    }

    public void remove(String id) {
        if (id != null) {
            sessions.remove(id);
        }
    }

    public List<AgentSession> list() {
        List<AgentSession> out = new ArrayList<>(sessions.values());
        Collections.sort(out, (a, b) -> a.id().compareTo(b.id()));
        return out;
    }

    public int size() {
        return sessions.size();
    }
}
