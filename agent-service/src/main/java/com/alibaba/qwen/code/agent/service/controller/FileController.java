package com.alibaba.qwen.code.agent.service.controller;

import com.alibaba.qwen.code.agent.service.core.AgentEngine;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 文件浏览 API：列出工作区目录、读取文件内容。
 *
 * <p>所有路径限制在工作区根（沙箱）内，防止越界访问。</p>
 */
@RestController
@RequestMapping("/api/files")
public class FileController {

    private static final Logger log = LoggerFactory.getLogger(FileController.class);

    private final AgentEngine engine;

    public FileController(AgentEngine engine) {
        this.engine = engine;
    }

    @GetMapping
    public Map<String, Object> list(@RequestParam(value = "path", defaultValue = "") String path) {
        Path root = engine.config().workspace();
        Path target = resolveSafe(root, path);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", true);
        out.put("root", root.toString());
        out.put("path", relativize(root, target));
        if (!Files.exists(target)) {
            out.put("ok", false);
            out.put("error", "路径不存在: " + relativize(root, target));
            return out;
        }
        if (Files.isDirectory(target)) {
            List<Map<String, Object>> entries = new ArrayList<>();
            try (java.util.stream.Stream<Path> stream = Files.list(target)) {
                stream.sorted((a, b) -> {
                    boolean ad = Files.isDirectory(a);
                    boolean bd = Files.isDirectory(b);
                    if (ad != bd) {
                        return ad ? -1 : 1;
                    }
                    return a.getFileName().toString().compareToIgnoreCase(b.getFileName().toString());
                }).forEach(p -> {
                    Map<String, Object> e = new LinkedHashMap<>();
                    e.put("name", p.getFileName().toString());
                    e.put("type", Files.isDirectory(p) ? "dir" : "file");
                    try {
                        e.put("size", Files.isDirectory(p) ? 0L : Files.size(p));
                        e.put("mtime", Files.getLastModifiedTime(p).toMillis());
                    } catch (IOException ignore) {
                        e.put("size", 0L);
                        e.put("mtime", 0L);
                    }
                    entries.add(e);
                });
            } catch (IOException e) {
                out.put("ok", false);
                out.put("error", "读取目录失败: " + e.getMessage());
                return out;
            }
            out.put("isDir", true);
            out.put("entries", entries);
        } else {
            out.put("isDir", false);
        }
        return out;
    }

    @GetMapping("/content")
    public Map<String, Object> content(@RequestParam("path") String path) {
        Path root = engine.config().workspace();
        Path target = resolveSafe(root, path);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", true);
        out.put("root", root.toString());
        out.put("path", relativize(root, target));
        if (!Files.exists(target) || Files.isDirectory(target)) {
            out.put("ok", false);
            out.put("error", "文件不存在: " + relativize(root, target));
            return out;
        }
        try {
            long size = Files.size(target);
            // 单次最多读 200KB，超出提示用工具读取
            byte[] data = Files.readAllBytes(target);
            String text = new String(data, StandardCharsets.UTF_8);
            out.put("size", size);
            out.put("truncated", size > 200_000);
            out.put("content", size > 200_000 ? text.substring(0, 200_000) : text);
        } catch (IOException e) {
            out.put("ok", false);
            out.put("error", "读取失败: " + e.getMessage());
        }
        return out;
    }

    // ------------------------------------------------------------------

    private static Path resolveSafe(Path root, String path) {
        String p = path == null ? "" : path.trim();
        // 去掉前导分隔符，防止绝对路径越界
        while (p.startsWith("/") || p.startsWith("\\")) {
            p = p.substring(1);
        }
        if (p.contains("..")) {
            // 禁止 .. 越界
            p = p.replace("..", "");
        }
        return root.resolve(p).normalize();
    }

    private static String relativize(Path root, Path target) {
        try {
            Path rel = root.relativize(target);
            String s = rel.toString().replace('\\', '/');
            return s.isEmpty() ? "." : s;
        } catch (Exception e) {
            return target.toString();
        }
    }
}
