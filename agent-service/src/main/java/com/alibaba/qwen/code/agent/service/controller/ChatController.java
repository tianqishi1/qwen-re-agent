package com.alibaba.qwen.code.agent.service.controller;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.alibaba.qwen.code.agent.core.AgentEventListener;
import com.alibaba.qwen.code.agent.core.AgentLoop;
import com.alibaba.qwen.code.agent.service.session.AgentSession;
import com.alibaba.qwen.code.agent.service.session.SessionManager;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ExecutorService;

/**
 * 聊天 API：非流式（POST /api/chat）与 SSE 流式（POST /api/chat/stream）。
 */
@RestController
@RequestMapping("/api")
public class ChatController {

    private static final Logger log = LoggerFactory.getLogger(ChatController.class);

    private final SessionManager sessions;
    private final ExecutorService workerPool;

    public ChatController(SessionManager sessions, ExecutorService workerPool) {
        this.sessions = sessions;
        this.workerPool = workerPool;
    }

    /** 请求体。 */
    public static final class ChatRequest {
        public String message;
        public String sessionId;

        public String getMessage() {
            return message;
        }

        public void setMessage(String message) {
            this.message = message;
        }

        public String getSessionId() {
            return sessionId;
        }

        public void setSessionId(String sessionId) {
            this.sessionId = sessionId;
        }
    }

    /** 非流式单轮回复（适合简单请求；复杂任务建议用 /api/chat/stream）。 */
    @PostMapping("/chat")
    public Map<String, Object> chat(@RequestBody ChatRequest req) {
        String message = req == null || req.message == null ? "" : req.message.trim();
        if (message.isEmpty()) {
            return error("消息不能为空");
        }
        AgentSession s = sessions.getOrCreate(req == null ? null : req.sessionId);
        if (!s.tryAcquire()) {
            return error("会话 " + s.id() + " 正在执行中，请稍候");
        }
        try {
            String reply = s.loop().run(message);
            s.setLastReply(reply);
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("ok", true);
            out.put("sessionId", s.id());
            out.put("reply", reply);
            out.put("toolCalls", tracesToJson(s.loop()));
            return out;
        } catch (Exception e) {
            log.error("[chat] 执行异常", e);
            return error("执行异常: " + e.getMessage());
        } finally {
            s.release();
        }
    }

    /** SSE 流式：实时推送工具调用 / 结果 / 文本，最后推送 done（含完整回复与 trace）。 */
    @PostMapping(value = "/chat/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter chatStream(@RequestBody ChatRequest req) {
        String message = req == null || req.message == null ? "" : req.message.trim();
        SseEmitter emitter = new SseEmitter(0L);
        if (message.isEmpty()) {
            send(emitter, "error", JSON.toJSONString(error("消息不能为空")));
            emitter.complete();
            return emitter;
        }
        AgentSession s = sessions.getOrCreate(req == null ? null : req.sessionId);
        if (!s.tryAcquire()) {
            send(emitter, "error", JSON.toJSONString(error("会话 " + s.id() + " 正在执行中，请稍候")));
            emitter.complete();
            return emitter;
        }

        AgentEventListener listener = new AgentEventListener() {
            @Override
            public void onToolCall(String tool, String args) {
                JSONObject ev = new JSONObject();
                ev.put("tool", tool);
                ev.put("args", args);
                send(emitter, "tool_call", ev.toJSONString());
            }

            @Override
            public void onToolResult(String tool, boolean success, long costMs, String preview) {
                JSONObject ev = new JSONObject();
                ev.put("tool", tool);
                ev.put("success", success);
                ev.put("costMs", costMs);
                ev.put("preview", preview);
                send(emitter, "tool_result", ev.toJSONString());
            }

            @Override
            public void onText(String text) {
                send(emitter, "text", text);
            }
        };

        AgentLoop loop = s.loop();
        loop.setListener(listener);
        workerPool.submit(() -> {
            try {
                String reply = loop.run(message);
                s.setLastReply(reply);
                JSONObject done = new JSONObject();
                done.put("ok", true);
                done.put("sessionId", s.id());
                done.put("reply", reply);
                done.put("toolCalls", tracesToJson(loop));
                send(emitter, "done", done.toJSONString());
            } catch (Exception e) {
                log.error("[chat/stream] 执行异常", e);
                send(emitter, "error", JSON.toJSONString(error("执行异常: " + e.getMessage())));
            } finally {
                loop.setListener(null);
                s.release();
                emitter.complete();
            }
        });

        emitter.onCompletion(() -> loop.setListener(null));
        emitter.onTimeout(() -> loop.setListener(null));
        emitter.onError(e -> loop.setListener(null));
        return emitter;
    }

    // ------------------------------------------------------------------

    private static JSONArray tracesToJson(AgentLoop loop) {
        JSONArray arr = new JSONArray();
        for (AgentLoop.ToolCallTrace t : loop.toolTraces()) {
            JSONObject o = new JSONObject();
            o.put("name", t.name);
            o.put("arguments", t.arguments);
            o.put("success", t.success);
            o.put("resultPreview", t.resultPreview);
            o.put("costMs", t.costMs);
            arr.add(o);
        }
        return arr;
    }

    private static void send(SseEmitter emitter, String name, String data) {
        try {
            emitter.send(SseEmitter.event().name(name).data(data));
        } catch (Exception ignore) {
            // 客户端断开，忽略
        }
    }

    private static Map<String, Object> error(String msg) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", false);
        out.put("error", msg);
        return out;
    }
}
