package com.alibaba.qwen.code.agent.service.controller;

import com.alibaba.fastjson2.JSON;
import com.alibaba.fastjson2.JSONArray;
import com.alibaba.fastjson2.JSONObject;
import com.alibaba.qwen.code.agent.core.AgentEventListener;
import com.alibaba.qwen.code.agent.service.core.AgentEngine;
import com.alibaba.qwen.code.agent.workflow.BuiltinSkills;
import com.alibaba.qwen.code.agent.workflow.WorkflowOrchestrator;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ExecutorService;

/**
 * 工作流 API：SSE 流式执行（POST /api/workflow/stream）与阶段元信息（GET /api/workflow/stages）。
 */
@RestController
@RequestMapping("/api/workflow")
public class WorkflowController {

    private static final Logger log = LoggerFactory.getLogger(WorkflowController.class);

    private final AgentEngine engine;
    private final ExecutorService workerPool;

    public WorkflowController(AgentEngine engine, ExecutorService workerPool) {
        this.engine = engine;
        this.workerPool = workerPool;
    }

    public static final class WorkflowRequest {
        public String idea;
        public List<String> stages;

        public String getIdea() {
            return idea;
        }

        public void setIdea(String idea) {
            this.idea = idea;
        }

        public List<String> getStages() {
            return stages;
        }

        public void setStages(List<String> stages) {
            this.stages = stages;
        }
    }

    /** 内置阶段清单。 */
    @GetMapping("/stages")
    public Map<String, Object> stages() {
        List<Map<String, String>> list = new ArrayList<>();
        for (String name : BuiltinSkills.PIPELINE) {
            com.alibaba.qwen.code.agent.workflow.Skill s = BuiltinSkills.get(name);
            if (s == null) {
                continue;
            }
            Map<String, String> m = new LinkedHashMap<>();
            m.put("name", s.name());
            m.put("description", s.description());
            m.put("artifacts", String.join(", ", s.artifacts()));
            list.add(m);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", true);
        out.put("pipeline", BuiltinSkills.PIPELINE);
        out.put("stages", list);
        return out;
    }

    /** 流式执行完整编码工作流（需求拆解 → 技术方案 → 开发 → 测试 → 上线 → 运维）。 */
    @PostMapping(value = "/stream", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter workflowStream(@RequestBody WorkflowRequest req) {
        String idea = req == null || req.idea == null ? "" : req.idea.trim();
        SseEmitter emitter = new SseEmitter(0L);
        if (idea.isEmpty()) {
            send(emitter, "error", JSON.toJSONString(error("idea 不能为空")));
            emitter.complete();
            return emitter;
        }

        workerPool.submit(() -> {
            List<String> stageNames = req == null ? null : req.stages;
            AgentEventListener listener = new AgentEventListener() {
                @Override
                public void onStage(String stage, String status, String detail) {
                    JSONObject ev = new JSONObject();
                    ev.put("stage", stage);
                    ev.put("status", status);
                    ev.put("detail", detail == null ? "" : detail);
                    send(emitter, "stage", ev.toJSONString());
                }

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
            };
            try {
                WorkflowOrchestrator orch = engine.newOrchestrator(listener);
                WorkflowOrchestrator.WorkflowResult result = orch.run(idea, stageNames);
                JSONObject done = new JSONObject();
                done.put("ok", result.success);
                done.put("idea", idea);
                JSONArray stagesArr = new JSONArray();
                for (WorkflowOrchestrator.StageResult sr : result.stages) {
                    JSONObject so = new JSONObject();
                    so.put("stage", sr.stage);
                    so.put("success", sr.success);
                    so.put("summary", sr.summary);
                    so.put("artifacts", sr.artifacts);
                    so.put("costSeconds", sr.costSeconds);
                    so.put("loopProtected", sr.loopProtected);
                    so.put("stalled", sr.stalled);
                    stagesArr.add(so);
                }
                done.put("stages", stagesArr);
                send(emitter, "done", done.toJSONString());
            } catch (Exception e) {
                log.error("[workflow] 执行异常", e);
                send(emitter, "error", JSON.toJSONString(error("执行异常: " + e.getMessage())));
            } finally {
                emitter.complete();
            }
        });

        emitter.onTimeout(() -> emitter.complete());
        emitter.onError(e -> emitter.complete());
        return emitter;
    }

    // ------------------------------------------------------------------

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
