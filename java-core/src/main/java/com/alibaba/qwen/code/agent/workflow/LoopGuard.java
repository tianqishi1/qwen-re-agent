package com.alibaba.qwen.code.agent.workflow;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * 死循环防护（对齐 qwen-code 的 getToolCallRepeatKey + 重复检测 + 停滞看门狗）。
 *
 * <p>机制：</p>
 * <ol>
 *   <li><b>重复调用检测</b>：对 (工具名, 规范化参数) 计算 sha256 指纹；
 *       参数做规范化（对象键排序、数组保序），使字段顺序不同但语义相同的调用
 *       命中同一指纹，防止模型靠重排字段绕过防护。</li>
 *   <li><b>循环判定</b>：同一阶段内同一指纹调用次数达到 {@code maxRepeat}
 *       （默认 3）即判定循环，触发 {@link #fallbackHint()} 回退提示注入。</li>
 *   <li><b>停滞看门狗</b>：连续 N 轮无任何工具执行成功（或连续空文本响应），
 *       判定停滞并提示换策略。</li>
 * </ol>
 */
public final class LoopGuard {

    /** 同一 (tool,args) 指纹允许的最大重复次数。 */
    private final int maxRepeat;
    /** 连续无进展轮次阈值。 */
    private final int maxStalledTurns;
    /** 阶段名称（用于日志与提示）。 */
    private final String stage;

    private final Map<String, Integer> callCounts = new LinkedHashMap<>();
    private final List<String> recentCalls = new ArrayList<>();
    private int stalledTurns;
    private boolean stalled;
    /** 是否真正触发过防护（重复调用或停滞）。 */
    private boolean triggered;

    public LoopGuard(String stage) {
        this(stage, 3, 4);
    }

    public LoopGuard(String stage, int maxRepeat, int maxStalledTurns) {
        this.stage = stage;
        this.maxRepeat = maxRepeat;
        this.maxStalledTurns = maxStalledTurns;
    }

    /**
     * 记录一次工具调用，返回是否允许继续（false = 已触发循环保护，应中断）。
     *
     * @param toolName  工具名
     * @param argsJson  参数 JSON 字符串
     * @param succeeded 工具执行是否成功
     */
    public boolean recordCall(String toolName, String argsJson, boolean succeeded) {
        String key = repeatKey(toolName, argsJson);
        int count = callCounts.containsKey(key) ? callCounts.get(key) + 1 : 1;
        callCounts.put(key, count);
        recentCalls.add(key);
        if (recentCalls.size() > 32) {
            String oldest = recentCalls.remove(0);
            // 简单滑动：保留计数，避免无限增长（字段有界）
        }
        if (count >= maxRepeat) {
            triggered = true;
            return false;
        }
        if (succeeded) {
            stalledTurns = 0;
        }
        return true;
    }

    /** 记录一轮模型响应（用于停滞检测）。 */
    public void recordTurn(boolean producedContent, boolean executedTool) {
        if (producedContent || executedTool) {
            stalledTurns = 0;
        } else {
            stalledTurns++;
            if (stalledTurns >= maxStalledTurns) {
                stalled = true;
                triggered = true;
            }
        }
    }

    public boolean isStalled() {
        return stalled;
    }

    /**
     * 计算 (tool, args) 的稳定指纹：规范化参数（对象键排序、数组保序）+ sha256。
     * 语义相同仅字段顺序不同的调用得到相同指纹。
     */
    public static String repeatKey(String toolName, String argsJson) {
        Object canonical = canonicalize(parseJson(argsJson));
        String keyString = toolName + ":" + (canonical == null ? "null" : canonical.toString());
        return sha256(keyString);
    }

    /** 回退提示：注入给模型，要求其换策略。 */
    public String fallbackHint() {
        return "[循环防护] 系统检测到你在阶段「" + stage + "」反复执行相同的工具调用而没有进展。"
                + "请停止重复，改用不同的策略：先读取相关文件理解现状，缩小问题范围，"
                + "或直接给出结论。若确实卡住，请明确说明阻塞点。";
    }

    public String stallHint() {
        return "[停滞防护] 系统检测到连续多轮没有产生有效进展（无文本、无工具结果）。"
                + "请停止空转：要么执行一个有价值的工具调用，要么直接输出你的阶段性结论。";
    }

    /** 是否真正触发过防护（重复调用被拦截或停滞检测命中）。 */
    public boolean hasHints() {
        return triggered;
    }

    // ------------------------------------------------------------------
    // 规范化 + 哈希
    // ------------------------------------------------------------------

    private static Object parseJson(String json) {
        if (json == null || json.trim().isEmpty()) {
            return null;
        }
        try {
            return com.alibaba.fastjson2.JSON.parse(json);
        } catch (Exception e) {
            return json; // 无法解析时按原始字符串参与指纹
        }
    }

    /**
     * 递归规范化：对象键排序（TreeMap），数组保序。
     * 数字按字符串规范化（1 与 1.0 视为相同）；布尔/字符串原样。
     */
    @SuppressWarnings("unchecked")
    private static Object canonicalize(Object value) {
        if (value instanceof Map) {
            Map<String, Object> src = (Map<String, Object>) value;
            Map<String, Object> sorted = new TreeMap<>();
            for (Map.Entry<String, Object> e : src.entrySet()) {
                sorted.put(e.getKey(), canonicalize(e.getValue()));
            }
            return sorted;
        }
        if (value instanceof List) {
            List<Object> src = (List<Object>) value;
            List<Object> out = new ArrayList<>(src.size());
            for (Object o : src) {
                out.add(canonicalize(o));
            }
            return out;
        }
        if (value instanceof Number) {
            // 整数规范化，避免 1 vs 1.0 产生不同指纹
            double d = ((Number) value).doubleValue();
            if (d == Math.floor(d) && !Double.isInfinite(d)) {
                return String.valueOf((long) d);
            }
            return String.valueOf(d);
        }
        if (value instanceof Boolean || value instanceof String) {
            return value;
        }
        return String.valueOf(value);
    }

    private static String sha256(String input) {
        try {
            MessageDigest md = MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(input.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(64);
            for (byte b : digest) {
                sb.append(String.format("%02x", b & 0xff));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 不可用", e);
        }
    }
}
