package com.alibaba.qwen.code.agent;

import com.alibaba.fastjson2.JSONObject;
import com.alibaba.qwen.code.agent.bridge.PythonBridge;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Java ⇄ Python 桥端到端联调测试。
 *
 * <p>需要本机 Python 可用；启动真实子进程验证 JSON-lines 协议。</p>
 */
class PythonBridgeIntegrationTest {

    private static String runtimeDir() {
        Path core = java.nio.file.Paths.get("").toAbsolutePath();
        Path parent = core.getParent();
        Path candidate = parent.resolve("python-runtime");
        return candidate.toFile().isDirectory() ? candidate.toString() : core.toString();
    }

    @Test
    void bridgePingAndTools() throws Exception {
        Path tmp = Files.createTempDirectory("qwencode-bridge-test");
        try {
            Files.write(tmp.resolve("hello.txt"),
                    "alpha\nbeta\ngamma\n".getBytes("UTF-8"));

            try (PythonBridge bridge = new PythonBridge("python", runtimeDir(), tmp.toString())) {
                assertTrue(bridge.ping(10_000), "Python 执行层 ping 失败");

                // read_file
                Map<String, Object> p1 = new LinkedHashMap<>();
                p1.put("path", "hello.txt");
                JSONObject r1 = bridge.call("read_file", p1, 20_000);
                assertTrue(r1.getBoolean("ok"), r1.toJSONString());
                assertEquals(3, r1.getJSONObject("result").getIntValue("total_lines"));

                // write_file
                Map<String, Object> p2 = new LinkedHashMap<>();
                p2.put("path", "out.txt");
                p2.put("content", "line1\nline2\n");
                JSONObject r2 = bridge.call("write_file", p2, 20_000);
                assertTrue(r2.getBoolean("ok"), r2.toJSONString());

                // run_command (write then run)
                Map<String, Object> p3 = new LinkedHashMap<>();
                p3.put("command", "python -c \"import sys; print(sys.version_info[0])\"");
                JSONObject r3 = bridge.call("run_command", p3, 30_000);
                assertTrue(r3.getBoolean("ok"), r3.toJSONString());
                assertNotNull(r3.getJSONObject("result").get("stdout"));

                // 危险命令被 Python 侧拒绝
                Map<String, Object> p4 = new LinkedHashMap<>();
                p4.put("command", "rm -rf /");
                JSONObject r4 = bridge.call("run_command", p4, 20_000);
                assertFalse(r4.getBoolean("ok"), "危险命令应被拒绝");
                assertTrue(r4.getString("error").contains("危险"));
            }
        } finally {
            deleteRecursively(tmp);
        }
    }

    private static void deleteRecursively(Path p) {
        if (p == null || !p.toFile().exists()) {
            return;
        }
        try {
            Files.walk(p).sorted(java.util.Comparator.reverseOrder())
                    .forEach(x -> x.toFile().delete());
        } catch (Exception ignore) {
            // ignore
        }
    }
}
