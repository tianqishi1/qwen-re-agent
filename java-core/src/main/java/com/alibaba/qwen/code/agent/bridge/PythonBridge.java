package com.alibaba.qwen.code.agent.bridge;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONObject;
import com.alibaba.qwen.code.agent.tools.ToolRegistry;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;

/**
 * Java ⇄ Python 桥：通过 JSON-lines over stdio 与 Python 执行层通信。
 *
 * <p>协议：每行一个 JSON。请求 <code>{"id":1,"tool":"read_file","params":{...}}</code>，
 * 响应 <code>{"id":1,"ok":true,"result":{...}}</code> 或
 * <code>{"id":1,"ok":false,"error":"..."}</code>。</p>
 */
public final class PythonBridge implements AutoCloseable {

    private final Process process;
    private final Writer stdin;
    private final BufferedReader stdout;
    private final ExecutorService readerExecutor;
    private final Map<Long, PendingCall> pending = new ConcurrentHashMap<>();
    private final AtomicLong nextId = new AtomicLong(1);
    private volatile boolean closed;

    private static final class PendingCall {
        private final Object lock = new Object();
        private JSONObject response;
        private boolean done;
    }

    public PythonBridge(String pythonCommand, String runtimeDir, String workspace) {
        try {
            ProcessBuilder pb = new ProcessBuilder(
                    pythonCommand, "-u", "-m", "qwen_agent_runtime.server", "--workspace", workspace);
            pb.redirectErrorStream(false);
            pb.environment().put("PYTHONIOENCODING", "utf-8");
            if (runtimeDir != null && !runtimeDir.isEmpty()) {
                String sep = System.getProperty("path.separator");
                String pyPath = pb.environment().get("PYTHONPATH");
                pb.environment().put("PYTHONPATH",
                        (pyPath == null || pyPath.isEmpty()) ? runtimeDir : runtimeDir + sep + pyPath);
            }
            process = pb.start();
            stdin = new OutputStreamWriter(process.getOutputStream(), StandardCharsets.UTF_8);
            stdout = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8));
            readerExecutor = Executors.newSingleThreadExecutor(r -> {
                Thread t = new Thread(r, "python-bridge-reader");
                t.setDaemon(true);
                return t;
            });
            readerExecutor.submit(this::readLoop);
        } catch (IOException e) {
            throw new BridgeException("无法启动 Python 执行层: " + e.getMessage(), e);
        }
    }

    private void readLoop() {
        try {
            String line;
            while (!closed && (line = stdout.readLine()) != null) {
                if (line.trim().isEmpty()) {
                    continue;
                }
                try {
                    JSONObject resp = JSON.parseObject(line);
                    Long id = resp.getLong("id");
                    if (id != null) {
                        PendingCall call = pending.remove(id);
                        if (call != null) {
                            synchronized (call.lock) {
                                call.response = resp;
                                call.done = true;
                                call.lock.notifyAll();
                            }
                        }
                    }
                } catch (Exception ignore) {
                    // 忽略无法解析的行
                }
            }
        } catch (IOException ignore) {
            // 子进程退出
        } finally {
            failAllPending("Python 执行层已退出");
        }
    }

    private void failAllPending(String reason) {
        for (PendingCall call : pending.values()) {
            synchronized (call.lock) {
                if (!call.done) {
                    call.response = new JSONObject();
                    call.response.put("ok", false);
                    call.response.put("error", reason);
                    call.done = true;
                    call.lock.notifyAll();
                }
            }
        }
        pending.clear();
    }

    /**
     * 调用 Python 工具。
     *
     * @param tool   工具名
     * @param params 参数
     * @param timeoutMs 超时（毫秒）
     * @return 工具结果 JSON
     */
    public JSONObject call(String tool, Map<String, Object> params, long timeoutMs) {
        if (closed) {
            throw new BridgeException("Python 桥已关闭");
        }
        long id = nextId.getAndIncrement();
        JSONObject req = new JSONObject();
        req.put("id", id);
        req.put("tool", tool);
        if (params == null) {
            params = new java.util.LinkedHashMap<>();
        }
        req.put("params", new JSONObject(params));

        PendingCall call = new PendingCall();
        pending.put(id, call);
        try {
            synchronized (stdin) {
                stdin.write(req.toJSONString());
                stdin.write("\n");
                stdin.flush();
            }
        } catch (IOException e) {
            pending.remove(id);
            throw new BridgeException("向 Python 执行层写入失败: " + e.getMessage(), e);
        }

        synchronized (call.lock) {
            long deadline = System.currentTimeMillis() + timeoutMs;
            while (!call.done) {
                long remain = deadline - System.currentTimeMillis();
                if (remain <= 0) {
                    pending.remove(id);
                    throw new BridgeTimeoutException("工具调用超时: " + tool);
                }
                try {
                    call.lock.wait(Math.min(remain, 1000));
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    pending.remove(id);
                    throw new BridgeException("等待工具结果被打断", e);
                }
            }
        }
        return call.response;
    }

    /** 启动健康检查：ping Python 执行层。 */
    public boolean ping(long timeoutMs) {
        try {
            JSONObject r = call("ping", null, timeoutMs);
            return Boolean.TRUE.equals(r.getBoolean("ok"));
        } catch (Exception e) {
            return false;
        }
    }

    /**
     * 创建绑定到本桥的新工具注册表（工作流每阶段一个独立注册表，
     * 复用同一 Python 执行进程）。
     */
    public ToolRegistry newToolRegistry() {
        return new ToolRegistry(this, 120_000);
    }

    /**
     * Python 子进程 pid（调试用）。
     *
     * <p>Java 9+ 可通过 {@code Process.pid()} 获取；为兼容 Java 8 使用反射，
     * 失败时返回 -1。</p>
     */
    public long pid() {
        try {
            java.lang.reflect.Method m = Process.class.getMethod("pid");
            return (Long) m.invoke(process);
        } catch (Exception e) {
            return -1L;
        }
    }

    @Override
    public void close() {
        closed = true;
        try {
            synchronized (stdin) {
                stdin.write(JSON.toJSONString(java.util.Collections.singletonMap("shutdown", true)));
                stdin.write("\n");
                stdin.flush();
            }
        } catch (IOException ignore) {
            // 子进程可能已退出
        }
        try {
            stdin.close();
        } catch (IOException ignore) {
            // ignore
        }
        readerExecutor.shutdown();
        try {
            readerExecutor.awaitTermination(3, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        failAllPending("Python 桥已关闭");
        process.destroy();
        try {
            if (!process.waitFor(3, TimeUnit.SECONDS)) {
                process.destroyForcibly();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /** 桥异常。 */
    public static class BridgeException extends RuntimeException {
        public BridgeException(String message) {
            super(message);
        }

        public BridgeException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    /** 工具调用超时异常。 */
    public static final class BridgeTimeoutException extends BridgeException {
        public BridgeTimeoutException(String message) {
            super(message);
        }
    }
}
