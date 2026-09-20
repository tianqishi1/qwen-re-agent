"""蒸馏：把知识库 + 风格统计 + 语录，固化成可复用的人格 Skill 包。

产物（默认写入 ``<root>/skills/<slug>/``）::

    SKILL.md                  Agent Skill 定义（frontmatter + 人格指令）
    playbook.md               使用手册（三种模式、如何追问、如何维护）
    README.md                 给人看的快速上手
    persona.json              结构化人格卡（可手工微调）
    persona.overrides.json    手工覆盖层（蒸馏时自动合并，永不被覆盖）
    evidence/chunks.jsonl     证据包（可移植，含定位信息）
    evidence/quotes.md        语录墙（带出处）
    evidence/sources.md       语料清单（含 sha1、字数）
    prompts/*.md              可直接粘贴到任意 LLM 客户端的提示词模板
    meta.json                 版本、指纹、文件清单
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import __version__
from .chunk import chunk_text, mine_quotes
from .llm import LLM, LLMError, parse_json_block
from .store import KB, KIND_LABELS
from .style import analyze
from .util import (
    atomic_write_text,
    dumps,
    read_json,
    sha1,
    skills_dir,
    truncate,
    write_json,
)

DEFAULT_GUARDRAILS = [
    "我是依据指定材料（书、文档、链接、经历、语录）构建的「人格模拟体」，不是本人；"
    "不得以本人名义做承诺、背书、签约、募资或发表声明。",
    "不编造名言、经历与数据。材料里没有的，明确说「这点材料里没有」或标注「以下是我的推断」。",
    "引用材料时给出编号（如 [2]）或出处（书名 · 章节）；单条引用不超过两句，尊重版权。",
    "涉及医疗、法律、投资、人身安全等高风险问题时，只给思考框架并提示寻求专业人士意见。",
    "不输出违法、伤害他人、规避监管或欺骗性的做法；被要求时直接拒绝并说明理由。",
    "保持角色一致，但一旦被问「你是不是真人」，如实说明这是基于材料的模拟。",
]

MODE_SPECS: Dict[str, Dict[str, str]] = {
    "interview": {
        "name": "访谈 / 交流",
        "goal": "像真人一样对话：给出观点、追问、举例、反驳，让用户获得这个人的判断与价值观。",
        "output": "口语化回答（可分段），结尾最多一个追问；必要时用 [n] 标注材料出处。",
    },
    "delegate": {
        "name": "委托 / 代劳",
        "goal": "用这个人的方法论，替用户把事做出来：方案、清单、代码/公式骨架、计划、评审意见。",
        "output": "先一句话结论 → 分步可执行方案（含交付物形态）→ 关键取舍与风险 → 待用户确认的问题。",
    },
    "critique": {
        "name": "挑剔 / 审视",
        "goal": "以这个人的标准挑刺：找出用户方案里最致命的 3 个问题，并给出替代方案。",
        "output": "最致命问题（按严重度排序，每条含「为什么致命」）→ 反例/边界条件 → 改写后的方案 → 验证方式。",
    },
    "teach": {
        "name": "讲解 / 带着学",
        "goal": "用这个人惯用的类比与分层方式，把复杂概念讲清楚，并设计检验理解的练习。",
        "output": "一句话直觉 → 类比/例子 → 逐步拆解 → 常见误解 → 一道自测题（附答案要点）。",
    },
}
MODE_ALIASES = {
    "ask": "interview", "chat": "interview", "talk": "interview", "访谈": "interview", "交流": "interview",
    "do": "delegate", "work": "delegate", "task": "delegate", "委托": "delegate", "代劳": "delegate",
    "review": "critique", "reviewer": "critique", "挑选": "critique", "审视": "critique", "挑剔": "critique",
    "explain": "teach", "learn": "teach", "讲解": "teach", "教学": "teach",
}


def normalize_mode(mode: Optional[str]) -> str:
    if not mode:
        return "interview"
    key = mode.strip().lower()
    key = MODE_ALIASES.get(key, key)
    return key if key in MODE_SPECS else "interview"


# --------------------------------------------------------------------------- 指纹
def fingerprint(kb: KB) -> str:
    """知识库内容指纹：用于判断「蒸馏后是否又补了资料」。"""
    parts = []
    for ch in kb.chunks():
        parts.append("%s:%s:%s" % (ch.id, ch.kind, sha1(ch.text)))
    sources = "|".join("%s:%s" % (s.id, s.sha1) for s in kb.sources())
    return sha1("\n".join(parts) + "#" + sources)


# --------------------------------------------------------------------------- 语录
def collect_quotes(kb: KB, limit: int = 30) -> List[Dict[str, str]]:
    """优先取显式语录来源，其次从正文里挖掘。"""
    out: List[Dict[str, str]] = []
    seen = set()

    def push(text: str, locator: str, kind: str) -> None:
        t = (text or "").strip()
        key = "".join(t.split())[:48]
        if not t or key in seen:
            return
        seen.add(key)
        out.append({"text": t, "locator": locator, "kind": kind})

    for ch in kb.chunks(kind="quote"):
        for line in ch.text.split("\n"):
            push(line.lstrip("-*> ").strip(), ch.locator, "quote")
    for ch in kb.chunks(kind="experience"):
        for sent in mine_quotes(ch.text, limit=2):
            push(sent["text"], ch.locator, "experience")
    for ch in kb.chunks():
        if ch.kind in ("quote", "experience"):
            continue
        for item in mine_quotes(ch.text, limit=2):
            push(item["text"], ch.locator, "mined")
    # 短的、信息密度高的优先
    out.sort(key=lambda q: (len(q["text"]) > 70, len(q["text"])))
    return out[:limit]


# --------------------------------------------------------------------------- 离线人格卡
def build_card_offline(kb: KB, quotes: Sequence[Dict[str, str]], top_terms: int = 24) -> Dict[str, Any]:
    profile = kb.get_persona() or {}
    chunks = kb.chunks()
    stats = analyze(chunks, top_terms=top_terms)
    voice = stats["voice"]
    name = profile.get("display_name") or kb.slug
    domain = profile.get("domain") or "、".join(stats["domains"][:2])
    keywords = [k["term"] for k in stats["keywords"]]

    sentence_style = _describe_sentence_style(voice)
    card: Dict[str, Any] = {
        "schema": "persona-card/1",
        "slug": kb.slug,
        "display_name": name,
        "aliases": profile.get("aliases") or [],
        "domain": domain,
        "era": profile.get("era", ""),
        "language": profile.get("language", "zh"),
        "one_line": "%s（%s）的可交互人格：%s" % (
            name, domain or "通用", (profile.get("style_hint") or sentence_style).strip()
        ),
        "identity_summary": _identity_summary(kb, name, domain, stats, quotes),
        "voice": {
            "tone": voice["tone"],
            "sentence_style": sentence_style,
            "rhetoric_devices": _rhetoric_devices(voice, stats),
            "lexicon": keywords[:14],
            "signature_phrases": [p["phrase"] for p in stats["signature_phrases"][:10]],
            "openers": (stats["connectives"][:5] or ["先说结论：", "换个角度看：", "在我看来，"]),
            "closers": _closers(stats["connectives"]),
            "avoid": ["空话套话", "没有依据的绝对化断言", "与本人立场明显矛盾的立场"],
        },
        "thinking": {
            "mental_models": stats["mental_models"],
            "heuristics": stats["heuristics"],
            "values": stats["value_terms"] + [truncate(s, 40) for s in stats["value_sentences"][:3]],
            "questioning_style": _questioning_style(voice),
        },
        "expertise": {
            "domains": stats["domains"],
            "strengths": _strengths(kb, stats),
            "limits": [
                "材料未覆盖的领域与年代之后的事实，只能推断，不能作为其真实观点。",
                "细节（数字、人名、时间）如材料中没有，一律不编。",
            ],
        },
        "quotes": list(quotes[:24]),
        "stats": {
            "chars": stats["chars"],
            "sentences": stats["sentences"],
            "voice": voice,
            "keywords": stats["keywords"],
            "markers": stats["marker_counts"],
            "sources": len(kb.sources()),
            "chunks": len(chunks),
        },
        "guardrails": list(DEFAULT_GUARDRAILS),
        "distilled_by": "offline-heuristics",
    }
    return card


def _describe_sentence_style(voice: Dict[str, Any]) -> str:
    parts = []
    avg = voice.get("avg_sentence_chars", 0)
    if avg >= 45:
        parts.append("句子偏长、层层递进")
    elif avg <= 20:
        parts.append("句子短促、直给结论")
    else:
        parts.append("长短句交错")
    if voice.get("question_ratio", 0) > 0.12:
        parts.append("经常反问")
    if voice.get("first_person_ratio", 0) > 0.35:
        parts.append("多用第一人称讲经验")
    if voice.get("analogy_per_kchar", 0) > 1.5:
        parts.append("爱打比方")
    if voice.get("assertive_per_kchar", 0) > voice.get("hedge_per_kchar", 0):
        parts.append("语气笃定")
    else:
        parts.append("措辞留有余地")
    return "；".join(parts)


def _identity_summary(kb: KB, name: str, domain: str, stats: Dict[str, Any], quotes: Sequence[Dict[str, str]]) -> str:
    profile = kb.get_persona() or {}
    kinds: Dict[str, int] = {}
    for s in kb.sources():
        kinds[s.kind] = kinds.get(s.kind, 0) + 1
    kind_txt = "、".join("%s×%d" % (KIND_LABELS.get(k, k), v) for k, v in sorted(kinds.items()))
    bits = ["以 %s 规模的材料（%s）构建" % (_human_chars(stats["chars"]), kind_txt or "无来源")]
    if profile.get("era"):
        bits.append("活跃时期：%s" % profile["era"])
    if stats["domains"]:
        bits.append("主要涉及：%s" % "、".join(stats["domains"][:3]))
    if quotes:
        bits.append("口头禅式表达如「%s」" % truncate(quotes[0]["text"], 24))
    return "%s：%s。" % (name, "；".join(bits))


def _human_chars(n: int) -> str:
    if n >= 1_000_000:
        return "%.1f 百万字" % (n / 1_000_000)
    if n >= 1000:
        return "%.1f 千字" % (n / 1000)
    return "%d 字" % n


def _rhetoric_devices(voice: Dict[str, Any], stats: Dict[str, Any]) -> List[str]:
    devices = []
    if voice.get("analogy_per_kchar", 0) > 1.0:
        devices.append("类比/故事先行，再用一句话点出规律")
    if voice.get("assertive_per_kchar", 0) > 1.0:
        devices.append("先给判断，再补条件与边界")
    if voice.get("hedge_per_kchar", 0) > voice.get("assertive_per_kchar", 0):
        devices.append("用「也许/如果」等限定词控制承诺强度")
    if stats["marker_counts"].get("question", 0) > 0:
        devices.append("用反问把问题抛回给提问者")
    if stats["signature_phrases"]:
        devices.append("复用其口头禅：%s" % "、".join(p["phrase"] for p in stats["signature_phrases"][:4]))
    return devices or ["先定义问题，再给结论"]


def _closers(connectives: Sequence[str]) -> List[str]:
    base = ["所以，说到底还是那句话——", "你先想想这个问题：", "别急着动手，先把边界划清。"]
    extra = [c + "……" for c in list(connectives)[:2]]
    return extra + base


def _questioning_style(voice: Dict[str, Any]) -> List[str]:
    style = ["先问「你真正想解决的是什么」", "追问约束条件与可承受的代价"]
    if voice.get("question_ratio", 0) > 0.1:
        style.append("习惯用反问推进思考")
    style.append("最后问「怎么验证它真的成立」")
    return style


def _strengths(kb: KB, stats: Dict[str, Any]) -> List[str]:
    out = []
    for domain in stats["domains"][:4]:
        out.append("在「%s」领域提供其惯用框架与判断" % domain)
    kinds = {s.kind for s in kb.sources()}
    if "experience" in kinds:
        out.append("基于其亲身经历给出先例与教训")
    if "quote" in kinds:
        out.append("能引用其原话，帮助用户理解其立场")
    out.append("把用户的问题翻译成这个人会问的问题")
    return out


# --------------------------------------------------------------------------- LLM 增强
def build_card_llm(kb: KB, card: Dict[str, Any], llm: LLM, quotes: Sequence[Dict[str, str]]) -> Dict[str, Any]:
    """让 LLM 基于压缩证据产出更凝练的人格卡，并与离线结果合并。"""
    profile = kb.get_persona() or {}
    chunks = kb.chunks()
    sampled = _stratified(chunks, 10)
    evidence = []
    for i, ch in enumerate(sampled, 1):
        evidence.append("[%d] %s\n%s" % (i, ch.locator, ch.text[:900]))
    quote_lines = "\n".join("- %s（出处：%s）" % (q["text"], q["locator"]) for q in quotes[:30])
    style_json = dumps(card["stats"])

    system = (
        "你是人格建模专家。任务：仅根据给定材料，为一个可交互人格写出精确、可执行的人物设定卡。"
        "不要编造材料中没有的事实；不确定的字段留空数组或空字符串。只输出 JSON。"
    )
    user = (
        "目标人物：%s（领域：%s；时期：%s）\n"
        "语言：%s\n\n"
        "【风格统计】\n%s\n\n"
        "【挖掘到的语录】\n%s\n\n"
        "【材料抽样】\n%s\n\n"
        "请输出如下 JSON（键名严格一致，中文内容）：\n"
        "{\n"
        '  "one_line": "一句话人设",\n'
        '  "identity_summary": "他是谁、以什么著称、依据哪些材料说话（80-160字）",\n'
        '  "voice": {"tone": ["..."], "sentence_style": "...", "rhetoric_devices": ["..."],\n'
        '            "lexicon": ["高频词"], "signature_phrases": ["口头禅"], "openers": ["开场句"],\n'
        '            "closers": ["收尾句"], "avoid": ["不能出现的话"]},\n'
        '  "thinking": {"mental_models": ["思维模型"], "heuristics": ["决策规矩"],\n'
        '               "values": ["价值观"], "questioning_style": ["会先问什么"]},\n'
        '  "expertise": {"domains": ["..."], "strengths": ["能帮用户做什么"], "limits": ["不能做什么"]}\n'
        "}"
        % (
            profile.get("display_name") or kb.slug,
            profile.get("domain") or "、".join(card["expertise"]["domains"][:3]),
            profile.get("era") or "未知",
            profile.get("language", "zh"),
            style_json,
            quote_lines,
            "\n\n".join(evidence),
        )
    )
    raw = llm.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=2200)
    patch = parse_json_block(raw)
    merged = deep_merge(card, patch)
    merged["distilled_by"] = "offline-heuristics + %s" % llm.cfg.model
    return merged


def _stratified(chunks: Sequence[Any], n: int) -> List[Any]:
    if not chunks:
        return []
    if len(chunks) <= n:
        return list(chunks)
    step = len(chunks) / float(n)
    return [chunks[min(len(chunks) - 1, int(i * step))] for i in range(n)]


# --------------------------------------------------------------------------- 合并
def deep_merge(base: Any, patch: Any) -> Any:
    """patch 中「非空」的值覆盖 base；字典递归合并，列表整体替换。"""
    if isinstance(base, dict) and isinstance(patch, dict):
        out = dict(base)
        for k, v in patch.items():
            if v in (None, "", [], {}):
                continue
            out[k] = deep_merge(base.get(k), v) if isinstance(base.get(k), (dict, list)) and isinstance(v, (dict, list)) else v
        return out
    return patch


def load_overrides(out_dir: Path) -> Dict[str, Any]:
    return read_json(Path(out_dir) / "persona.overrides.json", {}) or {}


def write_overrides_template(out_dir: Path) -> Path:
    path = Path(out_dir) / "persona.overrides.json"
    if path.exists():
        return path
    write_json(path, {
        "_说明": "在这里写你想固化的修改（例如声音、口头禅、禁忌）。字段与 persona.json 一致，"
                 "蒸馏时会被自动合并，且永远不会被生成结果覆盖。",
        "voice": {"signature_phrases": [], "avoid": []},
        "thinking": {"heuristics": []},
        "expertise": {"limits": []},
        "guardrails": [],
    })
    return path


# --------------------------------------------------------------------------- 渲染
def render_skill_md(card: Dict[str, Any], meta: Dict[str, Any]) -> str:
    voice = card["voice"]
    think = card["thinking"]
    exp = card["expertise"]
    name = card["display_name"]
    slug = card["slug"]

    def bullets(items: Iterable[str], fallback: str = "（暂无）") -> str:
        items = [str(i).strip() for i in items if str(i).strip()]
        return "\n".join("- %s" % i for i in items) if items else "- " + fallback

    def numbered(items: Iterable[str]) -> str:
        items = [str(i).strip() for i in items if str(i).strip()]
        return "\n".join("%d. %s" % (i, t) for i, t in enumerate(items, 1)) if items else "1. （暂无）"

    desc = (
        "%s（%s）的可交互人格 Skill：需要以他的视角对话、请教、委托做事或接受审视时使用。"
        "基于本地知识库（%s）蒸馏，回答须引用证据并遵守边界。"
        % (name, card.get("domain") or "通用", _human_chars(card["stats"]["chars"]))
    )
    quote_block = "\n".join(
        "> %s\n> —— 《%s》" % (q["text"], q.get("locator", "材料"))
        for q in card["quotes"][:5]
    ) or "> （知识库中暂无可引用原话）"

    return f"""---
name: persona-{slug}
description: {desc}
version: {__version__}
persona: {name}
mode: [interview, delegate, critique, teach]
---

# {name} · 人格 Skill

{dest_line(card)}

## 1. 你是谁

{card['identity_summary']}

- **一句话人设**：{card['one_line']}
- **领域**：{card.get('domain') or '通用'}
- **活跃时期**：{card.get('era') or '未标注'}
- **语言**：{card.get('language', 'zh')}
- **语料规模**：{_human_chars(card['stats']['chars'])} / {card['stats']['sources']} 个来源 / {card['stats']['chunks']} 个证据块
- **蒸馏方式**：{card.get('distilled_by', 'unknown')}

## 2. 语言风格（必须像这样说话）

- **语气**：{'、'.join(voice.get('tone') or ['平实']) }
- **句式**：{voice.get('sentence_style', '')}
- **修辞习惯**：
{bullets(voice.get('rhetoric_devices', []))}
- **高频词**：{'、'.join(voice.get('lexicon', [])[:14]) or '（暂无）'}
- **口头禅**：{'、'.join('「%s」' % p for p in voice.get('signature_phrases', [])) or '（暂无）'}
- **典型开场**：{' / '.join('「%s」' % p for p in voice.get('openers', [])[:4]) or '（暂无）'}
- **典型收尾**：{' / '.join('「%s」' % p for p in voice.get('closers', [])[:3]) or '（暂无）'}
- **避免**：
{bullets(voice.get('avoid', []))}

## 3. 思维模型与决策规矩

**思维模型**（用它们拆解用户的问题）：
{numbered(think.get('mental_models', []))}

**决策启发式**（涉及选择、取舍时优先套用）：
{numbered(think.get('heuristics', []))}

**价值观**（出现冲突时用来站位）：
{bullets(think.get('values', []))}

**提问习惯**（回答前先在内部问自己）：
{bullets(think.get('questioning_style', []))}

## 4. 能帮用户做什么 / 不做什么

**擅长**：
{bullets(exp.get('strengths', []))}

**限制**（越界时必须挑明）：
{bullets(exp.get('limits', []))}

## 5. 代表原话（引用示范）

{quote_block}

## 6. 工作协议

### 6.1 四种模式

| 模式 | 触发场景 | 输出形态 |
| --- | --- | --- |
""" + "\n".join(
        "| `%s` (%s) | %s | %s |" % (k, v["name"], v["goal"], v["output"]) for k, v in MODE_SPECS.items()
    ) + f"""

### 6.2 检索式回答（重要）

1. **先取证**：拿到问题后，先在知识库里检索相关原文（工具：`pd search <persona> "关键词"`）。
2. **再作答**：结论必须建立在证据上；对每条关键论断标注 `[n]`（对应 evidence 编号）或「出自《书名》· 章节」。
3. **区分三类话**：
   - **材料里明确说过的** → 直接引用并标注出处；
   - **可由材料推出来的** → 写明「这是我基于 XX 的推断」；
   - **材料里没有的** → 直说「这不在我的经验里」，然后只给方法论层面的回答。
4. **绝不做的事**：编造名言、编造数字、冒充本人做承诺或背书、把用户的想法说成本人观点。

### 6.3 回答模板（委托 / 代劳模式）

```
【结论】一句话给判断。
【为什么】2-4 条理由，尽量对应上面某个思维模型。
【怎么做】分步骤方案 / 清单 / 代码或公式骨架 / 时间表，指出交付物的形态。
【取舍与风险】最容易被忽略的 2-3 个坑，以及什么情况下这套方案不成立。
【待你确认】1-3 个必须由用户拍板的问题。
```

## 7. 边界与合规

{bullets(card.get('guardrails', DEFAULT_GUARDRAILS))}

## 8. 自检清单（每次回答前跑一遍）

- [ ] 我引用了知识库中的证据吗？编号/出处正确吗？
- [ ] 我在用「他/她」的语言和思维模型，而不是通用 AI 腔吗？
- [ ] 有没有出现材料里没有的人名、数字、名言？
- [ ] 越界的地方我挑明了吗？
- [ ] 用户拿到的东西能不能直接执行（有没有交付物）？

## 9. 维护

- 补充资料：`pd add {slug} <文件|目录|URL> --type book --tag 主题`
- 手工固化你的调整：编辑 `persona.overrides.json`（永不被覆盖），然后重跑 `pd distill {slug}`
- 蒸馏后进行：`pd ask {slug} "..."`（单轮）或 `pd chat {slug}`（多轮）
- 语料指纹：`{meta.get('kb_fingerprint', '')[:12]}`（`pd show {slug}` 会提示是否已过期）
"""


def dest_line(card: Dict[str, Any]) -> str:
    return "> 本 Skill 由 `persona-distiller` 从「%s」的个人语料蒸馏而成：它模拟其语言与思维，但不是本人。" % card["display_name"]


def render_playbook(card: Dict[str, Any]) -> str:
    slug = card["slug"]
    name = card["display_name"]
    return f"""# {name} · 使用手册（playbook）

## 0. 一分钟上手

```bash
# 1) 建人格
pd new {slug} --name "{name}" --domain "{card.get('domain', '')}"

# 2) 灌资料（可反复执行；重复内容会自动跳过）
pd add {slug} books/文件.pdf --type book --tag 核心思想
pd add {slug} notes/访谈.md --type doc
pd add {slug} https://example.com/article --type link
pd add {slug} --note "1998 年他带着 20 人重做产品线，砍掉 70% 需求"
pd add {slug} --quote "与其优化一个没人要的东西，不如重新定义问题"

# 3) 蒸馏成 Skill
pd distill {slug}

# 4) 用起来
pd ask {slug} "我这个 SaaS 定价太低了，怎么办" --mode delegate
pd chat {slug}                      # 多轮交流
```

## 1. 三种典型用法

### 1.1 直接交流（interview）
问价值观、问经历、问「如果是你会怎么做」。
```
pd ask {slug} "你为什么反对过早优化？" --mode interview
```
要点：**追问三层**。第一层问结论，第二层问依据（他会引用哪段材料），第三层问反例（什么情况下不成立）。

### 1.2 帮我做事（delegate）
把它当成一个顾问/合伙人，让它输出可执行交付物。
```
pd ask {slug} "帮我把这份周报改成我老板看得懂的版本" --mode delegate --k 10
pd ask {slug} "给我一个 3 周验证这个需求的实验计划" --mode delegate
```
要点：**要交付物形态**。让它产出「清单 / 计划 / 代码骨架 / 评审表」，而不是泛泛而谈。

### 1.3 让它挑刺（critique）
把自己已有的方案或想法贴进去，让它当最挑剔的对手。
```
pd ask {slug} "以下是我的方案：…… 请按你的标准挑最致命的 3 个问题" --mode critique
```
要点：要求它给出**替代方案**和**验证方式**，否则挑刺没有价值。

### 1.4 讲解（teach）
```
pd ask {slug} "用你的方式给我讲清楚这个概念的直觉" --mode teach
```

## 2. 怎么问得更好

| 差的问法 | 好的问法 |
| --- | --- |
| 「帮我分析一下」 | 「我有 A/B 两个选择，约束是 X，你选哪个、为什么、怎么验证」 |
| 「给我点建议」 | 「给我 3 条今天就能做的动作，并说明各自的代价」 |
| 「你觉得这个对吗」 | 「按你的标准，这个方案最可能在哪个环节崩掉」 |

## 3. 证据与可信度

- 所有回答都应带有证据编号；用 `pd search {slug} "关键词" -k 10` 单独查看证据。
- `pd ask --show-prompt` 可以看到真正发给模型的完整提示词（含证据块），便于排查「它是不是在编」。
- 没有配置 LLM 时，工具走**离线检索模式**：只返回相关原话 + 人格卡的推演骨架，明确标注不是模型推理结果。

## 4. 维护与迭代

| 目标 | 操作 |
| --- | --- |
| 补资料 | `pd add {slug} <路径或 URL> --type book/doc/link/experience/quote` |
| 固化你的调整 | 编辑 `skills/{slug}/persona.overrides.json` 后重跑 `pd distill {slug}` |
| 换更强的模型蒸馏 | `pd distill {slug} --llm`（先用 `pd doctor` 检查 LLM 配置） |
| 导出给同事 | `pd export {slug} -o {slug}-skill.zip` |
| 看语料规模 | `pd stats {slug}` / `pd show {slug}` |

## 5. 质量自检（蒸馏后必做）

1. `pd ask {slug} "1+1 等于几"`——如果它开始一本正经地大谈人生哲学，说明人设压过了事实，需要加强 `voice.avoid` 与边界约束。
2. 问一个知识库里明确有答案的问题——应当引用到具体出处（用来验证检索链路）。
3. 问一个明显超出材料范围的问题——应当明确说「材料里没有」，而不是编。
4. 把 `persona.json` 通读一遍，删掉与本人明显不符的条目（宁可少，不可假）。
"""


def render_skill_readme(card: Dict[str, Any], meta: Dict[str, Any]) -> str:
    slug = card["slug"]
    return f"""# {card['display_name']} · Persona Skill 包

- 生成时间：{meta['built_at']}
- 工具版本：persona-distiller {meta['tool_version']}
- 语料：{card['stats']['sources']} 个来源 / {card['stats']['chunks']} 个证据块 / {_human_chars(card['stats']['chars'])}
- LLM：{meta.get('llm', '无（离线启发式蒸馏）')}

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

1. **命令行**：`pd ask {slug} "你的问题" --mode delegate`、`pd chat {slug}`
2. **任意 LLM 客户端**：把 `prompts/system.md` 的内容作为 system prompt，`SKILL.md` 作为参考，
   再把检索到的证据贴在 `<evidence>` 里（`pd search {slug} "关键词"` 的输出可整段粘贴）。

> 合规提醒：本包是「基于材料的模拟」，不是本人。不要用它的输出冒充本人观点去发表或做决策依据。
"""


def render_prompts(card: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, str]:
    from .agent import build_system_prompt  # 延迟导入避免循环

    out: Dict[str, str] = {}
    out["system.md"] = build_system_prompt(card, "interview", meta)
    for key, spec in MODE_SPECS.items():
        out[key + ".md"] = (
            "# %s · %s 模式提示词\n\n"
            "把下面的 system 与 user 两段分别粘贴到你的 LLM 客户端。"
            "`{EVIDENCE}` 用 `pd search %s \"关键词\"` 的输出替换，`{TASK}` 换成你的请求。\n\n"
            "## system\n\n```\n%s\n```\n\n"
            "## user\n\n```\n<evidence>\n{EVIDENCE}\n</evidence>\n\n"
            "任务：{TASK}\n\n"
            "输出要求：%s\n```\n"
            % (card["display_name"], spec["name"], card["slug"], build_system_prompt(card, key, meta), spec["output"])
        )
    return out


def render_quotes_md(card: Dict[str, Any]) -> str:
    lines = ["# %s · 语录墙（含出处）" % card["display_name"], "",
             "> 自动挖掘结果，请人工校对：**标注为 `mined` 的可能不是本人原话，需在原文中确认后再使用。**", ""]
    for i, q in enumerate(card["quotes"], 1):
        label = {"quote": "显式语录", "experience": "经历叙述", "mined": "正文挖掘"}.get(q.get("kind", ""), q.get("kind", ""))
        lines.append("%d. 「%s」" % (i, q["text"]))
        lines.append("   - 出处：%s（%s）" % (q.get("locator", ""), label))
    lines.append("")
    lines.append("## 高频词与口头禅")
    lines.append("")
    lines.append("- 高频词：" + "、".join(card["voice"].get("lexicon", [])))
    lines.append("- 口头禅：" + "、".join(card["voice"].get("signature_phrases", [])))
    lines.append("")
    return "\n".join(lines)


def render_sources_md(kb: KB) -> str:
    srcs = kb.sources()
    lines = ["# 语料清单", "", "| # | 类型 | 标题 | 作者 | 字数 | sha1 | URL |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for i, s in enumerate(srcs, 1):
        lines.append("| %d | %s | %s | %s | %d | %s | %s |" % (
            i, KIND_LABELS.get(s.kind, s.kind), s.title.replace("|", "\\|"),
            (s.author or "-").replace("|", "\\|"), s.chars, s.sha1[:10], s.url or "-"))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- 主流程
def distill(
    root: Path,
    slug: str,
    out: Optional[str] = None,
    use_llm: bool = False,
    force: bool = False,
    k_quotes: int = 30,
    top_terms: int = 24,
    llm: Optional[LLM] = None,
    log=print,
) -> Dict[str, Any]:
    """执行蒸馏，返回产物路径与统计。"""
    kb = KB(root, slug)
    try:
        chunks = kb.chunks()
        if not chunks:
            raise RuntimeError(
                "知识库为空，先导入资料：pd add %s <文件|目录|URL> [--type book]" % slug
            )
        out_dir = skills_dir(root, out) / slug
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "evidence").mkdir(exist_ok=True)
        (out_dir / "prompts").mkdir(exist_ok=True)

        quotes = collect_quotes(kb, limit=max(k_quotes, 24))
        card = build_card_offline(kb, quotes, top_terms=top_terms)
        warn: List[str] = []

        if use_llm:
            if llm is None or not llm.available:
                warn.append("未检测到可用 LLM，已按离线启发式蒸馏。配置方式见 `pd doctor`。")
            else:
                try:
                    log("… 调 LLM 增强人格卡（%s）" % llm.cfg.model)
                    card = build_card_llm(kb, card, llm, quotes)
                except LLMError as exc:
                    warn.append("LLM 蒸馏失败（%s），已回退到离线结果。" % exc)

        overrides = load_overrides(out_dir)
        if overrides:
            card = deep_merge(card, {k: v for k, v in overrides.items() if not str(k).startswith("_")})
            warn.append("已合并 persona.overrides.json 中 %d 个顶层字段。" % len(overrides))

        fp = fingerprint(kb)
        meta: Dict[str, Any] = {
            "schema": "persona-skill/1",
            "slug": slug,
            "display_name": card["display_name"],
            "built_at": datetime.datetime.now().replace(microsecond=0).isoformat(),
            "tool_version": __version__,
            "kb_fingerprint": fp,
            "llm": llm.cfg.model if (use_llm and llm and llm.available) else "无（离线启发式蒸馏）",
            "counts": {"sources": len(kb.sources()), "chunks": len(chunks), "chars": card["stats"]["chars"]},
            "warnings": warn,
        }

        files: Dict[str, str] = {}
        files["SKILL.md"] = render_skill_md(card, meta)
        files["playbook.md"] = render_playbook(card)
        files["README.md"] = render_skill_readme(card, meta)
        files["evidence/quotes.md"] = render_quotes_md(card)
        files["evidence/sources.md"] = render_sources_md(kb)
        for name, content in render_prompts(card, meta).items():
            files["prompts/" + name] = content

        write_json(out_dir / "persona.json", card)
        kb.export_chunks(out_dir / "evidence" / "chunks.jsonl")
        write_overrides_template(out_dir)

        written: Dict[str, str] = {}
        for rel, content in files.items():
            path = out_dir / rel
            atomic_write_text(path, content if content.endswith("\n") else content + "\n")
            written[rel] = sha1(content)
        for rel in ("persona.json", "evidence/chunks.jsonl", "persona.overrides.json"):
            p = out_dir / rel
            if p.exists():
                written[rel] = sha1(p.read_text(encoding="utf-8"))
        meta["files"] = written
        meta["bytes"] = sum((out_dir / r).stat().st_size for r in written if (out_dir / r).exists())
        write_json(out_dir / "meta.json", meta)

        return {
            "out_dir": str(out_dir),
            "card": card,
            "meta": meta,
            "warnings": warn,
            "skill_md": str(out_dir / "SKILL.md"),
        }
    finally:
        kb.close()


def load_skill(root: Path, slug: str, out: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """读取已蒸馏的人格卡与 meta（找不到时给出可操作提示）。"""
    out_dir = skills_dir(root, out) / slug
    card = read_json(out_dir / "persona.json", None)
    if not card:
        raise RuntimeError(
            "尚未蒸馏：%s 不存在。先执行 `pd distill %s`。" % (out_dir / "persona.json", slug)
        )
    meta = read_json(out_dir / "meta.json", {}) or {}
    return card, meta


def is_stale(kb: KB, meta: Dict[str, Any]) -> bool:
    fp = meta.get("kb_fingerprint")
    if not fp:
        return False
    return fp != fingerprint(kb)


# --------------------------------------------------------------------------- 工具函数
def build_evidence_from_kb(kb: KB, query: str, k: int = 8) -> List[Any]:
    from .retrieve import BM25

    hits = BM25().build(kb.chunks()).search(query, k=k)
    return hits


def import_docs_to_kb(
    kb: KB,
    docs: Sequence[Any],
    kind: str,
    tags: Sequence[str] = (),
    force: bool = False,
    log=print,
) -> Dict[str, int]:
    """抽取结果入库：去重、切片、写 chunk。"""
    added = skipped = chunks_added = 0
    for doc in docs:
        text = (doc.text or "").strip()
        if len(text) < 20:
            log("  跳过（文本太短）：%s" % doc.title)
            skipped += 1
            continue
        source_id = kb.add_source(
            kind=doc.kind or kind,
            title=doc.title,
            text=text,
            author=doc.author,
            url=doc.url,
            tags=list(tags),
            meta=doc.meta,
            force=force,
        )
        if source_id is None:
            log("  跳过（内容重复）：%s" % doc.title)
            skipped += 1
            continue
        pieces = chunk_text(text, kind=doc.kind or kind)
        chunks_added += kb.add_chunks(source_id, pieces)
        added += 1
        log("  + %s（%s，%d 字 → %d 块）" % (doc.title, KIND_LABELS.get(kind, kind), len(text), len(pieces)))
    return {"sources": added, "skipped": skipped, "chunks": chunks_added}
