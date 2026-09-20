# 费曼 · Persona Skill 包

- 生成时间：2026-09-20T16:17:48
- 工具版本：persona-distiller 1.0.0
- 语料：10 个来源 / 21 个证据块 / 2.7 千字
- LLM：无（离线启发式蒸馏）

## 文件说明

| 文件 | 用途 |
| --- | --- |
| `SKILL.md` | 人格 Skill 本体：把这份文件作为 system prompt 或放进支持 Agent Skills 的目录即可用 |
| `playbook.md` | 使用手册：三种模式、提问技巧、维护流程 |
| `persona.json` | 结构化人格卡（voice / thinking / expertise / quotes / guardrails） |
| `persona.overrides.json` | 你的手工覆盖层，重蒸馏时自动合并、不会被覆盖 |
| `evidence/chunks.jsonl` | 证据包（每行一个证据块，含 locator/kind/weight），可被任何程序消费 |
| `evidence/quotes.md` | 语录墙（带出处），适合快速人工校对 |
| `evidence/sources.md` | 语料清单（含 sha1 与字数），用于溯源 |
| `prompts/*.md` | 可直接粘贴到任意 LLM 客户端的提示词模板 |
| `meta.json` | 版本、语料指纹与文件校验和 |

## 两种使用方式

1. **命令行**：`pd ask 费曼 "你的问题" --mode delegate`、`pd chat 费曼`
2. **任意 LLM 客户端**：把 `prompts/system.md` 的内容作为 system prompt，`SKILL.md` 作为参考，
   再把检索到的证据贴在 `<evidence>` 里（`pd search 费曼 "关键词"` 的输出可整段粘贴）。

> 合规提醒：本包是「基于材料的模拟」，不是本人。不要用它的输出冒充本人观点去发表或做决策依据。
