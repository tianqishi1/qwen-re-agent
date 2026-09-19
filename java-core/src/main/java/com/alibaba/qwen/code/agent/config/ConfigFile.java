package com.alibaba.qwen.code.agent.config;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Properties;

/**
 * 配置文件加载：支持 <code>qwencode.properties</code>（Java properties 格式）。
 *
 * <p>查找顺序：<code>--config PATH</code> 参数 &gt; 环境变量 <code>QWEN_CONFIG</code>
 * &gt; 当前目录 <code>qwencode.properties</code> &gt; 用户主目录
 * <code>.qwencode.properties</code>。</p>
 *
 * <p>支持键（与 CLI 参数、环境变量同名语义）：</p>
 * <pre>
 *   api-key = sk-xxx
 *   base-url = https://api.deepseek.com
 *   model = deepseek-flash
 *   permission = auto|plan|approve
 *   python = python
 *   workspace = D:/work/project
 *   max-iterations = 20
 *   stream = true
 * </pre>
 *
 * <p>优先级：命令行参数 &gt; 环境变量 &gt; 配置文件 &gt; 默认值。</p>
 */
public final class ConfigFile {

    private final Properties props = new Properties();
    private final String source;

    private ConfigFile(String source) {
        this.source = source;
    }

    /** 是否存在指定配置项。 */
    public boolean has(String key) {
        String v = props.getProperty(key);
        return v != null && !v.trim().isEmpty();
    }

    public String get(String key) {
        String v = props.getProperty(key);
        return (v == null || v.trim().isEmpty()) ? null : v.trim();
    }

    public String source() {
        return source;
    }

    /**
     * 按查找顺序定位配置文件；找不到时返回空配置（非 null）。
     */
    public static ConfigFile load(String explicitPath) {
        File target = resolveFile(explicitPath);
        if (target == null) {
            return new ConfigFile("(无配置文件)");
        }
        Properties p = new Properties();
        try (InputStream in = new FileInputStream(target)) {
            // 先读全部字节以剥离 UTF-8 BOM（PowerShell/记事本可能写入）
            java.io.ByteArrayOutputStream buf = new java.io.ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int n;
            while ((n = in.read(chunk)) != -1) {
                buf.write(chunk, 0, n);
            }
            byte[] bytes = buf.toByteArray();
            int off = 0;
            if (bytes.length >= 3 && (bytes[0] & 0xFF) == 0xEF
                    && (bytes[1] & 0xFF) == 0xBB && (bytes[2] & 0xFF) == 0xBF) {
                off = 3; // UTF-8 BOM
            }
            p.load(new java.io.InputStreamReader(
                    new java.io.ByteArrayInputStream(bytes, off, bytes.length - off),
                    StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new IllegalArgumentException("读取配置文件失败: " + target.getAbsolutePath()
                    + "（" + e.getMessage() + "）");
        }
        ConfigFile cf = new ConfigFile(target.getAbsolutePath());
        cf.props.putAll(p);
        return cf;
    }

    private static File resolveFile(String explicitPath) {
        if (explicitPath != null && !explicitPath.trim().isEmpty()) {
            File f = new File(explicitPath.trim());
            if (!f.isFile()) {
                throw new IllegalArgumentException("配置文件不存在: " + explicitPath);
            }
            return f;
        }
        // 1. 环境变量 QWEN_CONFIG
        String envPath = System.getenv("QWEN_CONFIG");
        if (envPath != null && !envPath.trim().isEmpty()) {
            File f = new File(envPath.trim());
            if (f.isFile()) {
                return f;
            }
        }
        // 2. 当前目录 qwencode.properties
        File cwd = new File(System.getProperty("user.dir"), "qwencode.properties");
        if (cwd.isFile()) {
            return cwd;
        }
        // 3. jar 同目录 qwencode.properties（便于随产物分发）
        File jarSide = jarSideConfig();
        if (jarSide != null && jarSide.isFile()) {
            return jarSide;
        }
        // 4. 用户主目录 .qwencode.properties
        String home = System.getProperty("user.home");
        if (home != null) {
            File homeFile = new File(home, ".qwencode.properties");
            if (homeFile.isFile()) {
                return homeFile;
            }
        }
        return null;
    }

    private static File jarSideConfig() {
        try {
            java.security.CodeSource cs = ConfigFile.class.getProtectionDomain().getCodeSource();
            if (cs != null && cs.getLocation() != null) {
                Path loc = Paths.get(cs.getLocation().toURI());
                Path dir = loc.toFile().isFile() ? loc.getParent() : loc;
                return dir.resolve("qwencode.properties").toFile();
            }
        } catch (Exception ignore) {
            // ignore
        }
        return null;
    }

    /**
     * 返回配置文件的建议路径（与 jar 同级），供 --config 使用。
     */
    public static String suggestPath() {
        try {
            java.security.CodeSource cs = ConfigFile.class.getProtectionDomain().getCodeSource();
            if (cs != null && cs.getLocation() != null) {
                Path loc = Paths.get(cs.getLocation().toURI());
                Path dir = loc.toFile().isFile() ? loc.getParent() : loc;
                return dir.resolve("qwencode.properties").toString();
            }
        } catch (Exception ignore) {
            // ignore
        }
        return new File(System.getProperty("user.dir"), "qwencode.properties").getAbsolutePath();
    }
}
