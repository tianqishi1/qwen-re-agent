package com.alibaba.qwen.code.agent.service.session;

import com.alibaba.qwen.code.agent.core.AgentEventListener;
import com.alibaba.qwen.code.agent.core.AgentLoop;
import com.alibaba.qwen.code.agent.core.Session;

import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 一个可交互的 Agent 会话：持有消息历史与执行循环。
 *
 * <p>会话内串行执行（同一会话同时只允许一个请求运行），跨会话可并行。</p>
 */
public final class AgentSession {

    private final String id;
    private final Session session = new Session();
    private final AgentLoop loop;
    private final AtomicBoolean busy = new AtomicBoolean(false);
    private volatile String lastReply = "";

    public AgentSession(AgentLoop loop) {
        this.id = UUID.randomUUID().toString().replace("-", "").substring(0, 12);
        this.loop = loop;
    }

    public String id() {
        return id;
    }

    public Session session() {
        return session;
    }

    public AgentLoop loop() {
        return loop;
    }

    public boolean tryAcquire() {
        return busy.compareAndSet(false, true);
    }

    public void release() {
        busy.set(false);
    }

    public boolean isBusy() {
        return busy.get();
    }

    public String lastReply() {
        return lastReply;
    }

    public void setLastReply(String reply) {
        this.lastReply = reply == null ? "" : reply;
    }

    /** 历史消息条数（不含 system）。 */
    public int historySize() {
        return session.messages().size();
    }
}
