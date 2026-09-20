"""人格运行时：把人格卡 + 检索到的证据，变成一个能对话/能代劳的「Skill」。

两种执行方式：
* **LLM 模式**：人格卡 → system prompt，检索结果 → ``<evidence>`` 上下文，交给模型生成。
* **离线模式**：不调用任何模型，返回「最相关原话 + 人格卡推演骨架」，并明确标注这不是模型推理结果。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .distill import MODE_SPECS, load_skill, normalize_mode
from .llm import LLM, LLMError, pack_evidence
from .retrieve import BM25, Hit, best_snippet
from .store import KB
from .util import truncate

OFFLINE_BANNER = (
    "【离线检索模式】未配置 LLM：以下内容由本地知识库检索 + 人格卡规则拼装，"
    "**不是模型推理结果**。配置 LLM 后可获得真正的多轮对话（见 `pd doctor`）。"
)


# --------------------------------------------------------------------------- 提示词
def build_system_prompt(card: Dict[str, Any], mode: str = "interview", meta: Optional[Dict[str, Any]] = None) -> str:
    mode = normalize_mode(mode)
    spec = MODE_SPECS[mode]
    voice = card.get("voice", {})
    think = card.get("thinking", {})
    exp = card.get("expertise", {})

    def join(items: Sequence[str], sep: str = "、", fallback: str = "（无）") -> str:
        vals = [str(i).strip() for i in items if str(i).strip()]
        return sep.join(vals) if vals else fallback

    guardrails = "\n".join("%d. %s" % (i, g) for i, g in enumerate(card.get("guardrails", []), 1))
    quotes = "\n".join("「%s」（%s）" % (q["text"], q.get("locator", "")) for q in card.get("quotes", [])[:8])

    return f"""你是「{card['display_name']}」的可交互人格模拟体（Persona Skill v1.0），由 persona-distiller 从指定语料蒸馏而来。

# 身份
{card.get('identity_summary', '')}
- 领域：{card.get('domain') or '通用'}
- 活跃时期：{card.get('era') or '未标注'}
- 你现在以第一人称说话，扮演他/她的思路、口吻与价值观。**但你清楚自己是模拟体**，被直接问到时如实说明。

# 语言风格（必须遵守）
- 语气：{join(voice.get('tone', []))}
- 句式：{voice.get('sentence_style', '平实')}
- 修辞习惯：{join(voice.get('rhetoric_devices', []), '；')}
- 高频词：{join(voice.get('lexicon', [])[:14])}
- 口头禅（自然嵌入，不要生硬堆砌）：{join(['「%s」' % p for p in voice.get('signature_phrases', [])[:8]])}
- 常用开场：{join(['「%s」' % p for p in voice.get('openers', [])[:4]])}
- 常用收尾：{join(['「%s」' % p for p in voice.get('closers', [])[:3]])}
- 禁止出现：{join(voice.get('avoid', []), '；')}

# 思维模型（用它们拆解问题）
{join(think.get('mental_models', []), '；')}

# 决策启发式（涉及取舍时优先套用）
{join(think.get('heuristics', []), '；')}

# 价值观（冲突时用来站位）
{join(think.get('values', []), '；')}

# 你习惯先问自己
{join(think.get('questioning_style', []), '；')}

# 能力与边界
擅长：{join(exp.get('strengths', []), '；')}
不能做：{join(exp.get('limits', []), '；')}

# 代表原话（可用于佐证你的立场，但不要滥用）
{quotes or '（语料中暂无可用原话）'}

# 证据使用规则（最重要）
1. 用户消息中的 <evidence> 是检索到的原始材料，每条带编号 [n] 和出处。
2. **事实、观点、经历、数字**只能来自 <evidence> 或你的常识边界内；关键论断必须用 [n] 或「《材料》· 章节」标注来源。
3. 区分三类表述并显式区分：
   - 材料明确写过的 → 直接引用 + 标注；
   - 能从材料推出的 → 写「这是我的推断」；
   - 材料里没有的 → 写「这不在我掌握的材料里」，只给方法论层面的回答。
4. 严禁编造名言、经历、数字、他人观点，或把用户的观点说成你的。
5. 不要大段照抄原文（单条引用 ≤ 2 句）。

# 当前模式：{spec['name']}
- 目标：{spec['goal']}
- 输出形态：{spec['output']}
- 期望长度：{_length_hint(mode)}

# 硬性边界
{guardrails}
"""


def _length_hint(mode: str) -> str:
    return {
        "interview": "200-400 字，像真人说话，不要列一堆小标题。",
        "delegate": "400-900 字，结构化输出，必须给出可执行交付物。",
        "critique": "300-700 字，直击要害，不客套。",
        "teach": "400-800 字，类比先行，最后给一道自测题。",
    }.get(mode, "300-600 字。")


def build_user_prompt(task: str, hits: Sequence[Hit], mode: str = "interview") -> str:
    mode = normalize_mode(mode)
    spec = MODE_SPECS[mode]
    evidence = pack_evidence(hits) or "（未检索到相关材料）"
    return (
        "<evidence>\n%s\n</evidence>\n\n"
        "任务：%s\n\n"
        "输出要求：%s\n"
        "如果证据不足以支撑结论，先说明缺什么，再给出基于方法的推断。" % (evidence, task.strip(), spec["output"])
    )


# --------------------------------------------------------------------------- 运行时
class PersonaRuntime:
    """一次会话所需的全部状态：人格卡 + 检索索引 + LLM。"""

    def __init__(self, root: Path, slug: str, out: Optional[str] = None, llm: Optional[LLM] = None) -> None:
        self.root = Path(root)
        self.slug = slug
        self.card, self.meta = load_skill(self.root, slug, out)
        self.kb = KB(self.root, slug)
        self.index = BM25().build(self.kb.chunks())
        self.llm = llm
        self.warnings: List[str] = []
        try:
            from .distill import is_stale

            if is_stale(self.kb, self.meta):
                self.warnings.append(
                    "知识库在蒸馏后有变化，建议重跑 `pd distill %s`（SKILL.md / persona.json 可能已过期）。" % slug
                )
        except Exception:  # noqa: BLE001 - 指纹计算失败不应阻断问答
            pass

    def close(self) -> None:
        self.kb.close()

    def __enter__(self) -> "PersonaRuntime":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ 检索
    def search(self, query: str, k: int = 8, kinds: Optional[Sequence[str]] = None) -> List[Hit]:
        return self.index.search(query, k=k, kinds=kinds)

    # ------------------------------------------------------------------ 作答
    def answer(
        self,
        task: str,
        mode: str = "interview",
        use_llm: bool = True,
        k: int = 8,
        history: Optional[Sequence[Tuple[str, str]]] = None,
        show_prompt: bool = False,
    ) -> Dict[str, Any]:
        mode = normalize_mode(mode)
        hits = self.search(task, k=k)
        messages = self._build_messages(task, hits, mode, history or [])
        result: Dict[str, Any] = {
            "mode": mode,
            "task": task,
            "evidence": [h.to_dict() for h in hits],
            "warnings": list(self.warnings),
            "model": "offline",
        }
        if show_prompt:
            result["prompt"] = messages

        if use_llm and self.llm is not None and self.llm.available:
            try:
                result["answer"] = self.llm.chat(
                    messages,
                    temperature=_mode_temperature(mode, self.llm.cfg.temperature),
                    max_tokens=1800,
                )
                result["model"] = self.llm.cfg.model
                return result
            except LLMError as exc:
                result["warnings"].append("LLM 调用失败（%s），已回退到离线检索模式。" % exc)
        elif use_llm:
            result["warnings"].append("未配置可用 LLM，使用离线检索模式。")

        result["answer"] = offline_answer(self.card, task, hits, mode)
        return result

    def _build_messages(
        self,
        task: str,
        hits: Sequence[Hit],
        mode: str,
        history: Sequence[Tuple[str, str]],
    ) -> List[Dict[str, str]]:
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": build_system_prompt(self.card, mode, self.meta)}
        ]
        for role, content in history[-8:]:
            messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": build_user_prompt(task, hits, mode)})
        return messages


def _mode_temperature(mode: str, base: float) -> float:
    return {
        "interview": min(0.85, base + 0.15),
        "delegate": max(0.2, base - 0.25),
        "critique": max(0.2, base - 0.2),
        "teach": base,
    }.get(mode, base)


# --------------------------------------------------------------------------- 离线作答
def offline_answer(card: Dict[str, Any], task: str, hits: Sequence[Hit], mode: str = "interview") -> str:
    """不依赖模型，产出「证据 + 推演骨架」，诚实标注其局限。"""
    mode = normalize_mode(mode)
    spec = MODE_SPECS[mode]
    name = card["display_name"]
    voice = card.get("voice", {})
    think = card.get("thinking", {})
    exp = card.get("expertise", {})
    quotes = card.get("quotes", [])
    out: List[str] = [OFFLINE_BANNER, ""]

    # ① 证据
    out.append("## ① 与你的问题最相关的材料（可直接引用）")
    if hits:
        for i, h in enumerate(hits[:6], 1):
            ch = h.chunk
            snippet = best_snippet(ch.text, task, chars=200)
            out.append("%d. [%s] %s" % (i, ch.kind, snippet.replace("\n", " ")))
            out.append("   出处：%s%s" % (ch.locator, "（%s）" % ch.url if ch.url else ""))
    else:
        out.append("（知识库里没有检索到相关内容——这本身就是信息：该问题可能超出他的材料范围。）")
    out.append("")

    # ② 拆解
    out.append("## ② 用 %s 的方式拆解这个问题" % name)
    models = [str(m) for m in think.get("mental_models", []) if str(m).strip()][:3]
    questions = [str(q) for q in think.get("questioning_style", []) if str(q).strip()][:3]
    heuristics = [str(h) for h in think.get("heuristics", []) if str(h).strip()][:3]
    if models:
        out.append("可用的思维模型：%s" % "、".join(models))
    if questions:
        out.append("他大概会先问：")
        out.extend("  - %s" % q for q in questions)
    if heuristics:
        out.append("决策规矩（取舍时套用）：")
        out.extend("  - %s" % h for h in heuristics)
    if not (models or questions or heuristics):
        out.append("（人格卡中尚未提取到思维模型，请补资料后重跑 pd distill）")
    out.append("")

    # ③ 口吻骨架
    out.append("## ③ 按 %s 的口吻作答（骨架，填充需 LLM 或你自己）" % name)
    openers = [str(o) for o in voice.get("openers", []) if str(o).strip()]
    phrases = [str(p) for p in voice.get("signature_phrases", []) if str(p).strip()]
    anchor = quotes[0]["text"] if quotes else ""
    out.append("开场（择一）：「%s」" % (openers[0] if openers else "先说结论："))
    if anchor:
        out.append("锚定一句他的原话：「%s」（%s）" % (truncate(anchor, 60), quotes[0].get("locator", "")))
    if phrases:
        out.append("把口头禅自然嵌入：%s" % "、".join("「%s」" % p for p in phrases[:5]))
    if hits:
        out.append("论点必须落在证据上：围绕第 ① 部分的第 1、2 条展开，逐条标注出处。")
    out.append("收尾：%s" % ("「%s」" % voice["closers"][0] if voice.get("closers") else "（给出下一步动作）"))
    out.append("")

    # ④ 交付物
    out.append("## ④ %s 模式的交付物建议" % spec["name"])
    out.extend("  - %s" % line for line in _deliverables_for(mode, name, exp))
    out.append("")

    # ⑤ 自检
    out.append("## ⑤ 自检（避免「自我感动式回答」）")
    out.append("  - 结论是否至少有 1 条材料支撑？编号是否可回溯（`pd search %s \"关键词\"`）？" % card["slug"])
    out.append("  - 有没有出现材料中不存在的人名/数字/名言？")
    out.append("  - 换成任何一个「聪明人」都能给出同样的回答吗？如果是，说明还没有用到他的独特性。")
    out.append("")
    out.append(
        "> 想拿到真正的成段回答：配置 LLM（`pd doctor`）后重跑，或用 `pd ask %s \"%s\" --show-prompt` "
        "把提示词粘到任意 LLM 客户端。" % (card["slug"], truncate(task, 30))
    )
    return "\n".join(out)


def _deliverables_for(mode: str, name: str, exp: Dict[str, Any]) -> List[str]:
    if mode == "delegate":
        return [
            "一页方案：目标 / 约束 / 方案 / 取舍 / 验证方式",
            "可执行清单：每条含负责人、耗时、完成判据",
            "最小验证实验：3 天内能拿到反馈的那一步",
            "风险与止损线：什么信号出现就该推翻这个方案",
        ]
    if mode == "critique":
        return [
            "最致命的 3 个问题（按严重度排序，每条写「为什么致命」）",
            "反例与边界条件：什么场景下你的方案直接失效",
            "替代方案：至少一个「更小代价」的版本",
            "验证方式：如何用最小成本证明我错了",
        ]
    if mode == "teach":
        return [
            "一句话直觉，再给一个日常类比",
            "层层拆解：每一步只引入一个新概念",
            "常见误解清单（人们通常错在哪一步）",
            "自测题 2 道 + 答案要点",
        ]
    return [
        "先给出他的立场（一句话上升为原则）",
        "再给 2-3 条理由，尽量引用材料原话",
        "补充一个他可能会举的例子或亲身经历",
        "最后抛回一个他惯用的追问",
    ]
