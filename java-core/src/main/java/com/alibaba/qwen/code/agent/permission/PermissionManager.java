package com.alibaba.qwen.code.agent.permission;

import com.alibaba.fastjson2.JSONObject;
import com.alibaba.qwen.code.agent.config.AgentConfig;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * 权限控制：根据模式决定工具调用是否放行。
 *
 * <ul>
 *   <li>AUTO：只读工具与安全修改直接放行；危险命令（rm -rf / 等）仍拦截。</li>
 *   <li>PLAN：只读工具放行，修改类工具询问用户。</li>
 *   <li>APPROVE：每次工具调用都询问用户。</li>
 * </ul>
 */
public final class PermissionManager {

    private final AgentConfig.PermissionMode mode;
    private final java.util.Scanner console;

    public PermissionManager(AgentConfig config) {
        this.mode = config.permissionMode();
        this.console = new java.util.Scanner(System.in);
    }

    /**
     * 检查工具调用是否允许执行。
     *
     * @param toolName   工具名
     * @param isMutating 是否修改类工具
     * @param paramsJson 参数（用于危险命令检测）
     * @return true 放行；false 拒绝
     */
    public boolean check(String toolName, boolean isMutating, String paramsJson) {
        // 1. 危险命令硬拦截（任何模式）
        if ("run_command".equals(toolName)) {
            String cmd = extractCommand(paramsJson);
            String reason = DangerousCommands.detect(cmd);
            if (reason != null) {
                System.out.println("[权限] 已拦截危险命令: " + reason);
                return false;
            }
        }

        // 2. 模式判断
        switch (mode) {
            case AUTO:
                return true;
            case PLAN:
                if (!isMutating) {
                    return true;
                }
                return ask("允许执行修改操作 [" + toolName + "] " + summarize(paramsJson) + " ? (y/n) ");
            case APPROVE:
                return ask("允许执行工具 [" + toolName + "] " + summarize(paramsJson) + " ? (y/n) ");
            default:
                return true;
        }
    }

    private boolean ask(String prompt) {
        System.out.print(prompt);
        System.out.flush();
        String line = console.hasNextLine() ? console.nextLine() : "n";
        String a = line.trim().toLowerCase(Locale.ROOT);
        return a.equals("y") || a.equals("yes") || a.equals("是") || a.equals("同意");
    }

    private static String extractCommand(String paramsJson) {
        try {
            JSONObject o = JSONObject.parseObject(paramsJson);
            return o == null ? "" : String.valueOf(o.getOrDefault("command", ""));
        } catch (Exception e) {
            return "";
        }
    }

    private static String summarize(String paramsJson) {
        if (paramsJson == null || paramsJson.isEmpty()) {
            return "";
        }
        String s = paramsJson.replace('\n', ' ');
        return s.length() > 120 ? s.substring(0, 120) + "..." : s;
    }

    /**
     * 危险命令检测规则。
     */
    public static final class DangerousCommands {

        private static final List<Pattern> BLACKLIST = Arrays.asList(
                // 破坏性系统操作
                Pattern.compile("(?i)\\brm\\s+(-[a-z]*[rf][a-z]*\\s+)*/(\\s|$)"),
                Pattern.compile("(?i)\\brm\\s+(-[a-z]*[rf][a-z]*\\s+)*~"),
                Pattern.compile("(?i)\\brm\\s+-rf\\s+\\*"),
                Pattern.compile("(?i)\\brm\\s+-rf\\s+[a-zA-Z]:\\\\"),
                Pattern.compile("(?i)\\bformat\\s+[a-zA-Z]:"),
                Pattern.compile("(?i)\\bmkfs\\b"),
                Pattern.compile("(?i)\\bdd\\s+if=.*of=/dev/"),
                Pattern.compile("(?i)\\bshutdown\\b|\\breboot\\b|\\bpoweroff\\b|\\binit\\s+0\\b"),
                Pattern.compile("(?i)\\bdel\\s+/[fs]\\b"),
                Pattern.compile("(?i)\\brmdir\\s+/"),
                // 危险下载执行
                Pattern.compile("(?i)curl[^|&;]*\\|\\s*(sudo\\s+)?(ba)?sh"),
                Pattern.compile("(?i)wget[^|&;]*\\|\\s*(sudo\\s+)?(ba)?sh"),
                // 权限提升
                Pattern.compile("(?i)\\bchmod\\s+777\\s+/"),
                Pattern.compile("(?i)\\bsudo\\s+rm\\b"),
                // 密钥/证书破坏
                Pattern.compile("(?i)\\brm\\s+(-[a-z]*[rf][a-z]*\\s+)*~/\\.ssh"),
                // Windows 特殊
                Pattern.compile("(?i)\\brmdir\\s+/s\\s+/q\\s+[a-zA-Z]:\\\\"),
                Pattern.compile("(?i)\\bdel\\s+/q\\s+[a-zA-Z]:\\\\")
        );

        private DangerousCommands() {
        }

        /**
         * 检测命令是否危险。
         *
         * @return 命中规则的描述；未命中返回 null。
         */
        public static String detect(String command) {
            if (command == null || command.trim().isEmpty()) {
                return null;
            }
            String cmd = command.trim();
            for (Pattern p : BLACKLIST) {
                if (p.matcher(cmd).find()) {
                    return p.pattern();
                }
            }
            return null;
        }
    }
}
