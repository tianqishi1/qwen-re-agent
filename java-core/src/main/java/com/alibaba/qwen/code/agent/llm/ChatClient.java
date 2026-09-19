package com.alibaba.qwen.code.agent.llm;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.alibaba.qwen.code.agent.config.AgentConfig;
import com.alibaba.qwen.code.agent.core.Message;
import com.alibaba.qwen.code.agent.core.ToolCall;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * LLM 网关：OpenAI 兼容 /chat/completions 客户端。
 *
 * <p>支持流式（SSE）与非流式，支持 function calling。</p>
 */
public final class ChatClient {

    private static final AtomicInteger CALL_ID = new AtomicInteger(0);
    private static final int CONNECT_TIMEOUT_MS = 15_000;
    private static final int READ_TIMEOUT_MS = 300_000;

    private final AgentConfig config;

    public ChatClient(AgentConfig config) {
        this.config = config;
    }

    /**
     * 单轮模型调用（非流式或流式）。
     *
     * @return 模型响应；若包含工具调用，则 content 可能为空。
     */
    public ChatResponse chat(List<Message> messages, List<ToolDefinition> tools) {
        Map<String, Object> body = buildBody(messages, tools, config.stream());
        if (config.stream()) {
            return streamChat(body);
        }
        return nonStreamChat(body);
    }

    private Map<String, Object> buildBody(List<Message> messages,
                                          List<ToolDefinition> tools, boolean stream) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("model", config.model());
        body.put("stream", stream);
        body.put("messages", toRequestMessages(messages));
        if (tools != null && !tools.isEmpty()) {
            List<Map<String, Object>> t = new ArrayList<>();
            for (ToolDefinition td : tools) {
                t.add(td.toRequestMap());
            }
            body.put("tools", t);
        }
        return body;
    }

    private static List<Map<String, Object>> toRequestMessages(List<Message> messages) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (Message m : messages) {
            out.add(m.toRequestMap());
        }
        return out;
    }

    // ------------------------------------------------------------------
    // 非流式
    // ------------------------------------------------------------------

    private ChatResponse nonStreamChat(Map<String, Object> body) {
        JSONObject resp = postJson(body, null);
        JSONArray choices = resp.getJSONArray("choices");
        if (choices == null || choices.isEmpty()) {
            throw new LlmException("模型响应缺少 choices: " + resp.toJSONString());
        }
        JSONObject choice = choices.getJSONObject(0);
        JSONObject msg = choice.getJSONObject("message");
        String content = msg == null ? null : msg.getString("content");
        List<ToolCall> calls = parseToolCalls(msg == null ? null : msg.getJSONArray("tool_calls"));
        return ChatResponse.of(content, calls, extractUsage(resp));
    }

    // ------------------------------------------------------------------
    // 流式（SSE）
    // ------------------------------------------------------------------

    private ChatResponse streamChat(Map<String, Object> body) {
        StringBuilder content = new StringBuilder();
        List<ToolCall> calls = new ArrayList<>();
        Map<String, JSONObject> callAccum = new LinkedHashMap<>();

        JSONObject resp = postJson(body, event -> {
            if (event.startsWith("data:")) {
                String data = event.substring(5).trim();
                if ("[DONE]".equals(data)) {
                    return;
                }
                try {
                    JSONObject obj = JSON.parseObject(data);
                    JSONArray choices = obj.getJSONArray("choices");
                    if (choices == null || choices.isEmpty()) {
                        return;
                    }
                    JSONObject choice = choices.getJSONObject(0);
                    JSONObject delta = choice.getJSONObject("delta");
                    if (delta == null) {
                        return;
                    }
                    String text = delta.getString("content");
                    if (text != null) {
                        content.append(text);
                        System.out.print(text);
                        System.out.flush();
                    }
                    JSONArray tcArr = delta.getJSONArray("tool_calls");
                    if (tcArr != null) {
                        accumulateToolCalls(tcArr, callAccum);
                    }
                } catch (Exception ignore) {
                    // 忽略非 JSON 数据帧
                }
            }
        });

        if (callAccum != null && !callAccum.isEmpty()) {
            for (JSONObject acc : callAccum.values()) {
                String id = acc.getString("id");
                String name = acc.getString("name");
                String args = acc.getString("arguments");
                if (id != null && name != null) {
                    calls.add(new ToolCall(id, name, args));
                }
            }
        }
        String text = content.toString();
        if (text.length() > 0) {
            System.out.println();
        }
        return ChatResponse.of(text, calls, extractUsage(resp));
    }

    private static void accumulateToolCalls(JSONArray tcArr, Map<String, JSONObject> acc) {
        for (int i = 0; i < tcArr.size(); i++) {
            JSONObject tc = tcArr.getJSONObject(i);
            // OpenAI 流式协议：同一工具调用的分片共享 index，仅首帧携带 id
            int index = tc.getIntValue("index", -1);
            if (index < 0) {
                continue;
            }
            String key = "idx_" + index;
            JSONObject slot = acc.get(key);
            if (slot == null) {
                slot = new JSONObject();
                acc.put(key, slot);
            }
            String id = tc.getString("id");
            if (id != null) {
                slot.put("id", id);
            }
            JSONObject fn = tc.getJSONObject("function");
            if (fn != null) {
                String name = fn.getString("name");
                if (name != null) {
                    slot.put("name", name);
                }
                String args = fn.getString("arguments");
                if (args != null) {
                    String prev = slot.getString("arguments");
                    slot.put("arguments", (prev == null ? "" : prev) + args);
                }
            }
        }
    }

    private static List<ToolCall> parseToolCalls(JSONArray toolCalls) {
        List<ToolCall> out = new ArrayList<>();
        if (toolCalls == null) {
            return out;
        }
        for (int i = 0; i < toolCalls.size(); i++) {
            JSONObject tc = toolCalls.getJSONObject(i);
            String id = tc.getString("id");
            JSONObject fn = tc.getJSONObject("function");
            if (fn == null) {
                continue;
            }
            String name = fn.getString("name");
            String args = fn.getString("arguments");
            if (id == null || name == null) {
                continue;
            }
            out.add(new ToolCall(id, name, args));
        }
        return out;
    }

    private static Map<String, Object> extractUsage(JSONObject resp) {
        JSONObject usage = resp.getJSONObject("usage");
        if (usage == null) {
            return java.util.Collections.emptyMap();
        }
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("prompt_tokens", usage.getIntValue("prompt_tokens", 0));
        m.put("completion_tokens", usage.getIntValue("completion_tokens", 0));
        m.put("total_tokens", usage.getIntValue("total_tokens", 0));
        return m;
    }

    // ------------------------------------------------------------------
    // HTTP
    // ------------------------------------------------------------------

    private interface SseHandler {
        void onEvent(String event);
    }

    private JSONObject postJson(Map<String, Object> body, SseHandler handler) {
        HttpURLConnection conn = null;
        try {
            URL url = new URL(config.baseUrl() + "/chat/completions");
            conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(CONNECT_TIMEOUT_MS);
            conn.setReadTimeout(READ_TIMEOUT_MS);
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setRequestProperty("Accept", handler == null ? "application/json" : "text/event-stream");
            conn.setRequestProperty("Authorization", "Bearer " + config.apiKey());
            // 兼容 DashScope 等网关
            conn.setRequestProperty("X-DashScope-SSE", handler == null ? "disable" : "enable");

            byte[] payload = JSON.toJSONBytes(body);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(payload);
                os.flush();
            }

            int code = conn.getResponseCode();
            if (code != 200) {
                String err = readStream(conn.getErrorStream());
                throw new LlmException("LLM 网关返回 HTTP " + code + ": " + truncate(err, 500));
            }

            if (handler != null) {
                readSse(conn.getInputStream(), handler);
                // 流式响应没有完整 JSON body，构造最小 usage
                JSONObject minimal = new JSONObject();
                minimal.put("choices", new JSONArray());
                minimal.put("usage", new JSONObject());
                return minimal;
            }
            String text = readStream(conn.getInputStream());
            return JSON.parseObject(text);
        } catch (IOException e) {
            throw new LlmException("LLM 网关通信失败: " + e.getMessage(), e);
        } finally {
            if (conn != null) {
                conn.disconnect();
            }
        }
    }

    private static void readSse(InputStream in, SseHandler handler) throws IOException {
        BufferedReader reader = new BufferedReader(
                new InputStreamReader(in, StandardCharsets.UTF_8));
        StringBuilder event = new StringBuilder();
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.isEmpty()) {
                if (event.length() > 0) {
                    handler.onEvent(event.toString());
                    event.setLength(0);
                }
            } else if (line.startsWith(":")) {
                // comment, ignore
            } else {
                if (event.length() > 0) {
                    event.append('\n');
                }
                event.append(line);
            }
        }
        if (event.length() > 0) {
            handler.onEvent(event.toString());
        }
    }

    private static String readStream(InputStream in) throws IOException {
        if (in == null) {
            return "";
        }
        BufferedReader reader = new BufferedReader(
                new InputStreamReader(in, StandardCharsets.UTF_8));
        StringBuilder sb = new StringBuilder();
        char[] buf = new char[4096];
        int n;
        while ((n = reader.read(buf)) != -1) {
            sb.append(buf, 0, n);
        }
        return sb.toString();
    }

    private static String truncate(String s, int max) {
        if (s == null) {
            return "";
        }
        return s.length() > max ? s.substring(0, max) + "..." : s;
    }

    /** LLM 网关异常。 */
    public static final class LlmException extends RuntimeException {
        public LlmException(String message) {
            super(message);
        }

        public LlmException(String message, Throwable cause) {
            super(message, cause);
        }
    }
}
