package com.alibaba.qwen.code.agent.bridge;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.alibaba.qwen.code.agent.tools.ToolRegistry;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Map;

/**
 * Java ⇄ Python 执行层 HTTP 桥（微服务模式）。
 *
 * <p>通过 HTTP 调用独立部署的 executor-service（python-runtime 的 http_server.py），
 * 支持多会话共享同一执行层、并发工具调用。协议：</p>
 * <pre>
 *   POST {base}/tool   body: {"tool":"read_file","params":{...}}
 *   GET  {base}/health
 * </pre>
 */
public final class PythonHttpBridge implements ToolBridge {

    private final String baseUrl;
    private final int connectTimeoutMs;
    private final int readTimeoutMs;

    public PythonHttpBridge(String baseUrl) {
        this(baseUrl, 15_000, 300_000);
    }

    public PythonHttpBridge(String baseUrl, int connectTimeoutMs, int readTimeoutMs) {
        this.baseUrl = baseUrl.endsWith("/")
                ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.connectTimeoutMs = connectTimeoutMs;
        this.readTimeoutMs = readTimeoutMs;
    }

    @Override
    public JSONObject call(String tool, Map<String, Object> params, long timeoutMs) {
        JSONObject req = new JSONObject();
        req.put("tool", tool);
        req.put("params", params == null ? new JSONObject() : new JSONObject(params));

        long timeout = Math.max(timeoutMs, 5_000);
        try {
            JSONObject resp = post("/tool", req, timeout);
            if (resp == null) {
                return error("执行层无响应");
            }
            return resp;
        } catch (IOException e) {
            return error("调用执行层失败: " + e.getMessage());
        } catch (BridgeTimeoutException e) {
            return error("工具执行超时: " + tool);
        }
    }

    @Override
    public boolean ping(long timeoutMs) {
        try {
            JSONObject resp = get("/health", timeoutMs);
            return resp != null && Boolean.TRUE.equals(resp.getBoolean("ok"));
        } catch (Exception e) {
            return false;
        }
    }

    @Override
    public ToolRegistry newToolRegistry() {
        return new ToolRegistry(this, 120_000);
    }

    @Override
    public long pid() {
        return -1L; // HTTP 模式下执行层为外部进程
    }

    @Override
    public void close() {
        // HTTP 模式下不拥有执行层进程，无需关闭；如需要可调用 POST /shutdown
    }

    /** 关闭远程执行层（供服务关闭时优雅停止 executor）。 */
    public void shutdownRemote() {
        try {
            post("/shutdown", new JSONObject(), 5_000);
        } catch (Exception ignore) {
            // 执行层可能已退出
        }
    }

    // ------------------------------------------------------------------
    // HTTP 工具
    // ------------------------------------------------------------------

    private JSONObject post(String path, JSONObject body, long timeoutMs)
            throws IOException, BridgeTimeoutException {
        HttpURLConnection conn = open(path, "POST", timeoutMs);
        try {
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            byte[] data = body.toJSONString().getBytes(StandardCharsets.UTF_8);
            conn.setFixedLengthStreamingMode(data.length);
            try (OutputStream os = conn.getOutputStream()) {
                os.write(data);
            }
            return readResponse(conn, timeoutMs);
        } finally {
            conn.disconnect();
        }
    }

    private JSONObject get(String path, long timeoutMs)
            throws IOException, BridgeTimeoutException {
        HttpURLConnection conn = open(path, "GET", timeoutMs);
        try {
            return readResponse(conn, timeoutMs);
        } finally {
            conn.disconnect();
        }
    }

    private HttpURLConnection open(String path, String method, long timeoutMs)
            throws IOException {
        URL url = new URL(baseUrl + path);
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setRequestMethod(method);
        conn.setConnectTimeout(Math.max(3_000, (int) Math.min(timeoutMs, 60_000)));
        conn.setReadTimeout(Math.max(3_000, (int) Math.min(timeoutMs, 300_000)));
        return conn;
    }

    private JSONObject readResponse(HttpURLConnection conn, long timeoutMs)
            throws IOException, BridgeTimeoutException {
        int code = conn.getResponseCode();
        InputStream is = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
        String text = readAll(is);
        if (code >= 400) {
            throw new IOException("执行层 HTTP " + code + ": " + truncate(text, 200));
        }
        if (text == null || text.trim().isEmpty()) {
            return null;
        }
        try {
            return JSON.parseObject(text);
        } catch (Exception e) {
            throw new IOException("执行层返回非法 JSON: " + truncate(text, 200));
        }
    }

    private static String readAll(InputStream is) throws IOException {
        if (is == null) {
            return "";
        }
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(
                new InputStreamReader(is, StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) {
                sb.append(line);
            }
        }
        return sb.toString();
    }

    private static JSONObject error(String message) {
        JSONObject o = new JSONObject();
        o.put("ok", false);
        o.put("error", message);
        return o;
    }

    private static String truncate(String s, int max) {
        if (s == null) {
            return "";
        }
        return s.length() > max ? s.substring(0, max) + "..." : s;
    }

    /** HTTP 调用超时。 */
    public static final class BridgeTimeoutException extends RuntimeException {
        public BridgeTimeoutException(String message) {
            super(message);
        }
    }
}
