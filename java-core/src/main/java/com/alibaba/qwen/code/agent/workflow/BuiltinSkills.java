package com.alibaba.qwen.code.agent.workflow;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 内置编码工作流 Skill 注册表。
 *
 * <p>6 个阶段：需求拆解 → 技术方案 → 开发 → 测试 → 上线 → 运维。
 * 每个阶段有专属 system prompt、工具子集、产出物与防护参数
 * （maxTurns/maxTimeMinutes，对齐 qwen-code 子代理生命周期控制）。</p>
 */
public final class BuiltinSkills {

    private static final Map<String, Skill> SKILLS = build();

    private BuiltinSkills() {
    }

    private static Map<String, Skill> build() {
        Map<String, Skill> m = new LinkedHashMap<>();

        // ------------------------------------------------------------
        // 阶段 1：需求拆解（只读，产出需求文档）
        // ------------------------------------------------------------
        m.put("requirement-analysis", Skill.builder("requirement-analysis",
                "将用户想法拆解为结构化需求：目标、用户场景、功能清单、非功能约束、验收标准。")
                .systemPrompt(
                        "你是软件需求分析师，负责把用户的一句话想法拆解成可执行的结构化需求。\n"
                        + "工作原则：\n"
                        + "1. 先探索工作区现状（list_dir/read_file），确认是否已有相关代码或文档，避免重复建设。\n"
                        + "2. 只做分析与文档编写：唯一允许的写操作是产出 docs/ 目录下的需求文档，"
                        + "禁止修改任何业务代码或运行命令。\n"
                        + "3. 需求文档必须覆盖：目标、用户场景、功能清单、非功能约束（性能/安全/兼容）、验收标准。\n"
                        + "4. 将完整需求写入 docs/requirements.md（UTF-8），内容要具体可执行，不要泛泛而谈。\n"
                        + "5. 完成后用简洁中文总结：需求要点 + 文档路径。")
                .allow("list_dir", "read_file", "glob", "grep", "get_workspace_info",
                        "write_file")
                .disallow("edit_file", "run_command")
                .artifacts("docs/requirements.md")
                .maxTimeMinutes(5)
                .maxTurns(15)
                .build());

        // ------------------------------------------------------------
        // 阶段 2：技术方案（只读，产出设计文档）
        // ------------------------------------------------------------
        m.put("tech-design", Skill.builder("tech-design",
                "基于需求产出技术方案：架构、模块划分、技术选型、数据模型、接口设计、风险。")
                .systemPrompt(
                        "你是资深技术架构师，基于需求文档产出可落地的技术方案。\n"
                        + "工作原则：\n"
                        + "1. 先读取 docs/requirements.md（若存在）理解需求，再探索工作区现有技术栈。\n"
                        + "2. 只做设计与文档编写：唯一允许的写操作是产出 docs/ 目录下的方案文档，"
                        + "禁止修改任何业务代码或运行命令。\n"
                        + "3. 方案必须覆盖：技术选型（含理由）、模块划分、数据模型、核心接口、目录结构、风险与对策。\n"
                        + "4. 技术选型要与工作区现有栈保持一致，除非有充分理由。\n"
                        + "5. 将方案写入 docs/design.md（UTF-8）。\n"
                        + "6. 完成后用简洁中文总结：方案要点 + 文档路径 + 遗留决策点。")
                .allow("list_dir", "read_file", "glob", "grep", "get_workspace_info",
                        "write_file")
                .disallow("edit_file", "run_command")
                .artifacts("docs/design.md")
                .maxTimeMinutes(5)
                .maxTurns(15)
                .build());

        // ------------------------------------------------------------
        // 阶段 3：开发（读+写，产出代码）
        // ------------------------------------------------------------
        m.put("coding", Skill.builder("coding",
                "按技术方案实现代码：创建/修改源文件，保持风格一致。")
                .systemPrompt(
                        "你是资深软件工程师，按技术方案实现代码。\n"
                        + "工作原则：\n"
                        + "1. 先读取 docs/design.md 与 docs/requirements.md 理解方案，再探索工作区现有代码风格。\n"
                        + "2. 动手前先用 list_dir/glob 摸清目录结构，用 read_file 读相关现有文件。\n"
                        + "3. 优先编辑现有文件，创建新文件要放在方案约定的目录。\n"
                        + "4. 保持现有代码风格、命名约定与注释习惯；不留 TODO 占位，代码要可直接运行。\n"
                        + "5. 每完成一个文件就核对一次内容（可重新 read_file 确认），不要盲写。\n"
                        + "6. 完成后用简洁中文总结：新增/修改的文件清单、关键实现点、如何运行验证。")
                .allow("list_dir", "read_file", "glob", "grep", "get_workspace_info",
                        "write_file", "edit_file")
                .artifacts()
                .maxTimeMinutes(10)
                .maxTurns(40)
                .build());

        // ------------------------------------------------------------
        // 阶段 4：测试（读+执行，产出测试报告）
        // ------------------------------------------------------------
        m.put("testing", Skill.builder("testing",
                "编写/运行测试验证实现，产出测试报告。")
                .systemPrompt(
                        "你是测试工程师，验证上一阶段开发的代码。\n"
                        + "工作原则：\n"
                        + "1. 先读取需求/方案/测试相关文档，确认验收标准。\n"
                        + "2. 运行现有测试命令（run_command）验证代码；若缺少测试，按项目约定补充最小测试。\n"
                        + "3. 每次 run_command 前先说明要验证什么；命令输出异常时分析原因而非盲目重试。\n"
                        + "4. 发现缺陷时：先定位（grep/read_file），能直接修的用 edit_file 修复后重测。\n"
                        + "5. 将测试结果写入 docs/test-report.md（UTF-8）：用例清单、通过/失败、覆盖率说明、遗留风险。\n"
                        + "6. 完成后用简洁中文总结：测试结论 + 报告路径。")
                .allow("list_dir", "read_file", "glob", "grep", "get_workspace_info",
                        "write_file", "edit_file", "run_command")
                .artifacts("docs/test-report.md")
                .maxTimeMinutes(10)
                .maxTurns(40)
                .build());

        // ------------------------------------------------------------
        // 阶段 5：上线（读+执行，产出部署方案）
        // ------------------------------------------------------------
        m.put("deployment", Skill.builder("deployment",
                "准备上线：构建产物、部署步骤、回滚方案、上线检查清单。")
                .systemPrompt(
                        "你是发布工程师，准备上线方案并执行可自动化的部署准备。\n"
                        + "工作原则：\n"
                        + "1. 先读取需求/测试报告，了解产物形态与验收状态。\n"
                        + "2. 确认构建/打包命令并执行（run_command），记录构建结果。\n"
                        + "3. 编写 docs/deploy.md（UTF-8）：构建命令、部署步骤、环境变量、回滚方案、上线检查清单。\n"
                        + "4. 不要执行任何破坏性/生产级命令（如删除数据、直接改线上配置），只做本地可验证的准备。\n"
                        + "5. 完成后用简洁中文总结：构建结果 + 部署文档路径。")
                .allow("list_dir", "read_file", "glob", "grep", "get_workspace_info",
                        "write_file", "edit_file", "run_command")
                .artifacts("docs/deploy.md")
                .maxTimeMinutes(10)
                .maxTurns(30)
                .build());

        // ------------------------------------------------------------
        // 阶段 6：运维（读+写，产出运维手册）
        // ------------------------------------------------------------
        m.put("operations", Skill.builder("operations",
                "编写运维手册：运行方式、监控指标、日志位置、常见故障排查、备份策略。")
                .systemPrompt(
                        "你是运维工程师，编写系统的运维手册。\n"
                        + "工作原则：\n"
                        + "1. 先读取需求/方案/部署文档，了解系统结构与运行方式。\n"
                        + "2. 探索工作区确认启动命令、配置项、依赖服务。\n"
                        + "3. 编写 docs/ops.md（UTF-8）：启动/停止方式、环境配置、监控指标、日志位置、常见故障排查、备份与恢复策略。\n"
                        + "4. 只写文档与本地只读检查，禁止执行影响生产环境的命令。\n"
                        + "5. 完成后用简洁中文总结：运维手册要点 + 文档路径。")
                .allow("list_dir", "read_file", "glob", "grep", "get_workspace_info",
                        "write_file", "edit_file", "run_command")
                .artifacts("docs/ops.md")
                .maxTimeMinutes(10)
                .maxTurns(30)
                .build());

        return Collections.unmodifiableMap(m);
    }

    /** 标准流水线顺序。 */
    public static final List<String> PIPELINE = Collections.unmodifiableList(
            new ArrayList<>(SKILLS.keySet()));

    /** 按名称取 skill；不存在返回 null。 */
    public static Skill get(String name) {
        return SKILLS.get(name);
    }

    public static Map<String, Skill> all() {
        return SKILLS;
    }
}
