package com.alibaba.qwen.code.agent.service.executor;

import com.alibaba.qwen.code.agent.bridge.PythonHttpBridge;
import com.alibaba.qwen.code.agent.bridge.ToolBridge;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.util.concurrent.TimeUnit;

/**
 * Python 执行层（executor-service）生命周期管理。
 *
 * <p>两种模式：</p>
 * <ul>
 *   <li><b>auto-start</b>：agent-service 启动时拉起 {@code python -m qwen_agent_runtime.http_server}
 *       子进程并等待健康检查通过，关闭时优雅停止；</li>
 *   <li><b>外部模式</b>：连接已独立部署的 executor（运维手动启动）。</li>
 * </ul>
 */
public final class ExecutorManager {

    private static final Logger log = LoggerFactory.getLogger(ExecutorManager.class);

    private final String host;
    private final int port;
    private final boolean autoStart;
    private final int startupTimeoutSeconds;
    private final String pythonCommand;
    private final String pythonRuntimeDir;
    private final String workspace;

    private Process process;
    private PythonHttpBridge bridge;

    public ExecutorManager(String host, int port, boolean autoStart, int startupTimeoutSeconds,
                           String pythonCommand, String pythonRuntimeDir, String workspace) {
        this.host = host;
        this.port = port;
        this.autoStart = autoStart;
        this.startupTimeoutSeconds = startupTimeoutSeconds;
        this.pythonCommand = pythonCommand;
        this.pythonRuntimeDir = pythonRuntimeDir;
        this.workspace = workspace;
    }

    public String baseUrl() {
        return "http://" + host + ":" + port;
    }

    public synchronized void start() throws IOException, InterruptedException {
        if (bridge != null) {
            return;
        }
        if (autoStart) {
            launchProcess();
        }
        bridge = new PythonHttpBridge(baseUrl());
        awaitHealthy();
        log.info("[executor] 执行层就绪: {}  workspace={}", baseUrl(), workspace);
    }

    public synchronized ToolBridge bridge() {
        if (bridge == null) {
            throw new IllegalStateException("executor 尚未启动");
        }
        return bridge;
    }

    public synchronized void stop() {
        if (bridge != null) {
            bridge.shutdownRemote();
            bridge = null;
        }
        if (process != null) {
            process.destroy();
            try {
                if (!process.waitFor(3, TimeUnit.SECONDS)) {
                    process.destroyForcibly();
                }
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            process = null;
            log.info("[executor] 执行层子进程已停止");
        }
    }

    // ------------------------------------------------------------------

    private void launchProcess() throws IOException {
        ProcessBuilder pb = new ProcessBuilder(
                pythonCommand, "-u", "-m", "qwen_agent_runtime.http_server",
                "--workspace", workspace, "--host", host, "--port", String.valueOf(port));
        pb.redirectErrorStream(false);
        pb.environment().put("PYTHONIOENCODING", "utf-8");
        String sep = System.getProperty("path.separator");
        String pyPath = pb.environment().get("PYTHONPATH");
        pb.environment().put("PYTHONPATH",
                (pyPath == null || pyPath.isEmpty()) ? pythonRuntimeDir : pythonRuntimeDir + sep + pyPath);
        process = pb.start();
        log.info("[executor] 已启动子进程: {} -m qwen_agent_runtime.http_server --port {}", pythonCommand, port);
    }

    private void awaitHealthy() throws InterruptedException {
        long deadline = System.currentTimeMillis() + startupTimeoutSeconds * 1000L;
        while (System.currentTimeMillis() < deadline) {
            if (bridge.ping(2_000)) {
                return;
            }
            Thread.sleep(500);
        }
        throw new IllegalStateException("executor 启动超时（" + startupTimeoutSeconds + "s），"
                + "请检查 python-runtime 与端口 " + port + " 是否被占用");
    }
}
