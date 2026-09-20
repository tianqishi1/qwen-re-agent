package com.alibaba.qwen.code.agent.core;

/**
 * Agent 执行过程事件监听器（供 Web 前端 SSE 流式推送与观测）。
 *
 * <p>全部方法默认空实现，未设置监听器时行为与旧版完全一致。</p>
 */
public interface AgentEventListener {

    /** 模型发起一次工具调用（输入侧）。 */
    default void onToolCall(String tool, String args) {
    }

    /** 工具执行完成（输出侧）。 */
    default void onToolResult(String tool, boolean success, long costMs, String preview) {
    }

    /** 模型输出文本片段（流式文本）。 */
    default void onText(String text) {
    }

    /** 工作流阶段事件：status ∈ stage_start / stage_end / workflow_end / error。 */
    default void onStage(String stage, String status, String detail) {
    }
}
