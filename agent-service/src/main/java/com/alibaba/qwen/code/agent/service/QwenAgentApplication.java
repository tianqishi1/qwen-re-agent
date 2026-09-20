package com.alibaba.qwen.code.agent.service;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Qwencode 编码 Agent Web 服务入口（Spring Boot 微服务）。
 *
 * <p>职责：</p>
 * <ul>
 *   <li>组装核心引擎（AgentConfig / ChatClient / PythonHttpBridge）；</li>
 *   <li>对外暴露 REST + SSE API（聊天 / 工作流 / 文件浏览）；</li>
 *   <li>托管前端静态页面（src/main/resources/static）。</li>
 * </ul>
 */
@SpringBootApplication
public class QwenAgentApplication {

    public static void main(String[] args) {
        SpringApplication.run(QwenAgentApplication.class, args);
    }
}
