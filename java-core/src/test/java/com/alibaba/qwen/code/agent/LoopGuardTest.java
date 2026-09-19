package com.alibaba.qwen.code.agent;

import com.alibaba.qwen.code.agent.workflow.LoopGuard;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** LoopGuard 死循环/停滞防护单元测试。 */
class LoopGuardTest {

    @Test
    void repeatKeyIgnoresFieldOrder() {
        String a = LoopGuard.repeatKey("write_file", "{\"path\":\"x\",\"content\":\"y\"}");
        String b = LoopGuard.repeatKey("write_file", "{\"content\":\"y\",\"path\":\"x\"}");
        assertEquals(a, b, "字段顺序不同应产生相同指纹");
    }

    @Test
    void repeatKeyDistinguishesValuesAndNames() {
        String a = LoopGuard.repeatKey("write_file", "{\"path\":\"x\"}");
        String b = LoopGuard.repeatKey("write_file", "{\"path\":\"y\"}");
        String c = LoopGuard.repeatKey("read_file", "{\"path\":\"x\"}");
        assertFalse(a.equals(b), "不同值应不同指纹");
        assertFalse(a.equals(c), "不同工具应不同指纹");
    }

    @Test
    void repeatKeyNormalizesNumbers() {
        String a = LoopGuard.repeatKey("run_command", "{\"command\":\"x\",\"timeout_seconds\":60}");
        String b = LoopGuard.repeatKey("run_command", "{\"command\":\"x\",\"timeout_seconds\":60.0}");
        assertEquals(a, b, "1 与 1.0 应视为相同");
    }

    @Test
    void repeatDetectionTriggersAtThreshold() {
        LoopGuard g = new LoopGuard("test", 3, 4);
        assertTrue(g.recordCall("read_file", "{\"path\":\"a\"}", true));
        assertTrue(g.recordCall("read_file", "{\"path\":\"a\"}", true));
        assertFalse(g.recordCall("read_file", "{\"path\":\"a\"}", true), "第 3 次相同调用应触发防护");
        assertTrue(g.hasHints(), "防护应标记 triggered");
    }

    @Test
    void differentArgsDoNotTrigger() {
        LoopGuard g = new LoopGuard("test", 3, 4);
        assertTrue(g.recordCall("read_file", "{\"path\":\"a\"}", true));
        assertTrue(g.recordCall("read_file", "{\"path\":\"b\"}", true));
        assertTrue(g.recordCall("read_file", "{\"path\":\"c\"}", true));
        assertFalse(g.hasHints(), "不同参数不应触发防护");
    }

    @Test
    void stallDetectionFiresAfterQuietTurns() {
        LoopGuard g = new LoopGuard("test", 3, 2);
        assertFalse(g.isStalled());
        g.recordTurn(false, false);
        assertFalse(g.isStalled());
        g.recordTurn(false, false);
        assertTrue(g.isStalled(), "连续无进展轮次应触发停滞");
        assertTrue(g.hasHints(), "停滞也应标记 triggered");
    }

    @Test
    void successfulCallsResetStallCounter() {
        LoopGuard g = new LoopGuard("test", 3, 2);
        g.recordTurn(false, false);
        g.recordTurn(true, false); // 有内容产出，重置
        g.recordTurn(false, false);
        assertFalse(g.isStalled(), "有进展后不应误判停滞");
    }
}
