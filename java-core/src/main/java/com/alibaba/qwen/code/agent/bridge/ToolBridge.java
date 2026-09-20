package com.alibaba.qwen.code.agent.bridge;

import com.alibaba.fastjson2.JSONObject;
import com.alibaba.qwen.code.agent.tools.ToolRegistry;

import java.util.Map;

/**
 * Java ⇄ Python 执行层桥接抽象（微服务化后的统一端口）。
 *
 * <p>两种实现：</p>
 * <ul>
 *   <li>{@link PythonBridge}：JSON-lines over stdio 子进程（CLI/单机模式）；</li>
 *   <li>{@link PythonHttpBridge}：HTTP 调用独立部署的 executor-service（微服务模式）。</li>
 * </ul>
 */
public interface ToolBridge extends AutoCloseable {

    /**
     * 调用 Python 工具。
     *
     * @param tool   工具名
     * @param params 参数
     * @param timeoutMs 超时（毫秒）
     * @return 工具结果 JSON
     */
    JSONObject call(String tool, Map<String, Object> params, long timeoutMs);

    /** 健康检查：执行层是否可用。 */
    boolean ping(long timeoutMs);

    /** 创建绑定到本桥的新工具注册表（工作流每阶段一个独立注册表）。 */
    ToolRegistry newToolRegistry();

    /** 执行层进程 pid（调试用；HTTP 模式下可能为外部进程或 -1）。 */
    long pid();

    @Override
    void close();
}
