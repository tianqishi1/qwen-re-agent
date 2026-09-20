package com.alibaba.qwen.code.agent.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

/**
 * 会话：管理消息历史与上下文预算。
 */
public final class Session {

    /** 上下文预算上限（约 8k token 的消息字符量，简单近似）。 */
    private static final int MAX_CONTEXT_CHARS = 240_000;

    private final String id;
    private final List<Message> messages = new ArrayList<>();
    private int totalChars;

    public Session() {
        this.id = UUID.randomUUID().toString().substring(0, 8);
    }

    public String id() {
        return id;
    }

    public List<Message> messages() {
        return Collections.unmodifiableList(messages);
    }

    public synchronized void append(Message message) {
        messages.add(message);
        totalChars += estimateChars(message);
        trimIfNeeded();
    }

    public synchronized void appendAll(List<Message> msgs) {
        for (Message m : msgs) {
            append(m);
        }
    }

    /** 触发裁剪：从最早的 USER 消息开始丢弃，保留 system 与最近消息。 */
    private void trimIfNeeded() {
        while (totalChars > MAX_CONTEXT_CHARS && messages.size() > 8) {
            Message dropped = messages.remove(1); // 保留 index 0 的 system
            totalChars -= estimateChars(dropped);
        }
    }

    private static int estimateChars(Message m) {
        int n = m.content() == null ? 0 : m.content().length();
        for (ToolCall tc : m.toolCalls()) {
            n += tc.name().length() + (tc.arguments() == null ? 0 : tc.arguments().length());
        }
        return n;
    }

    public int size() {
        return messages.size();
    }

    public int totalChars() {
        return totalChars;
    }
}
