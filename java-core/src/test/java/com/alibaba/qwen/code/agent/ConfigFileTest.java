package com.alibaba.qwen.code.agent;

import com.alibaba.qwen.code.agent.config.ConfigFile;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 配置文件加载单元测试。
 */
class ConfigFileTest {

    @TempDir
    Path tmp;

    @Test
    void loadExplicitFile() throws IOException {
        Path cfg = tmp.resolve("qwencode.properties");
        Files.write(cfg, ("api-key=sk-test\n"
                + "base-url=https://api.deepseek.com\n"
                + "model=deepseek-flash\n"
                + "permission=plan\n").getBytes(StandardCharsets.UTF_8));
        ConfigFile cf = ConfigFile.load(cfg.toString());
        assertEquals("sk-test", cf.get("api-key"));
        assertEquals("https://api.deepseek.com", cf.get("base-url"));
        assertEquals("deepseek-flash", cf.get("model"));
        assertEquals("plan", cf.get("permission"));
        assertTrue(cf.source().contains("qwencode.properties"));
    }

    @Test
    void loadWithCommentsAndBlank() throws IOException {
        Path cfg = tmp.resolve("cfg.properties");
        Files.write(cfg, ("# 注释行\n"
                + "\n"
                + "api-key = sk-abc   \n"
                + "# 空值不应被当作配置\n"
                + "workspace=\n").getBytes(StandardCharsets.UTF_8));
        ConfigFile cf = ConfigFile.load(cfg.toString());
        assertEquals("sk-abc", cf.get("api-key"));
        assertFalse(cf.has("workspace"));
        assertNull(cf.get("workspace"));
    }

    @Test
    void loadWithUtf8Bom() throws IOException {
        Path cfg = tmp.resolve("bom.properties");
        // 手动写入 UTF-8 BOM + 内容，模拟 PowerShell/记事本保存
        byte[] body = "api-key=sk-bom-test\nmodel=deepseek-flash\n".getBytes(StandardCharsets.UTF_8);
        byte[] withBom = new byte[body.length + 3];
        withBom[0] = (byte) 0xEF;
        withBom[1] = (byte) 0xBB;
        withBom[2] = (byte) 0xBF;
        System.arraycopy(body, 0, withBom, 3, body.length);
        Files.write(cfg, withBom);
        ConfigFile cf = ConfigFile.load(cfg.toString());
        assertEquals("sk-bom-test", cf.get("api-key"));
        assertEquals("deepseek-flash", cf.get("model"));
    }

    @Test
    void missingFileThrows() {
        assertThrows(IllegalArgumentException.class,
                () -> ConfigFile.load(tmp.resolve("nope.properties").toString()));
    }

    @Test
    void noConfigReturnsEmpty() {
        // 无显式路径、无环境变量时应返回空配置而非异常
        ConfigFile cf = ConfigFile.load(null);
        assertNull(cf.get("api-key"));
        assertTrue(cf.source().contains("无配置文件"));
    }
}
