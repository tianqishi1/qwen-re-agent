#!/usr/bin/env python3
"""pd —— 单文件人格蒸馏器（仅 Python 标准库）。

只做一件事：把**文档语料**（.txt/.md/.rst/.html）蒸馏成一个可复用的人格 Skill。

    python pd.py new 费曼 --domain "物理学/教学"
    python pd.py add 费曼 资料目录/            # 唯一支持的资料类型：文档
    python pd.py distill 费曼                  # 生成 skills/费曼/SKILL.md + persona.json
    python pd.py ask 费曼 "这个概念怎么讲给外行" [--mode delegate] [--offline]
    python pd.py list | show 费曼 | rm 费曼

可选接 LLM（不配置也能用，走离线检索模式）：
    PD_API_KEY / PD_BASE_URL / PD_MODEL，或 DASHSCOPE_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

VERSION = "1.1.0"
TOOLDIR = ".pd"
TEXTS = {".txt", ".md", ".markdown", ".rst", ".html", ".htm"}
MODES = {
    "interview": ("交流", "像本人一样对话：给判断、举例、反问，观点必须落在材料上。", "200-400 字，口语化，不要小标题。"),
    "delegate": ("委托", "用他的方法论替用户把事做出来：方案、清单、计划、评审。",
                 "先一句话结论，再分步方案，最后写清取舍风险与待确认项；必须给出可执行交付物。"),
}
MODEL_WORDS = ("第一性原理", "第一原理", "类比", "心智模型", "框架", "范式", "反过来想", "复利", "权衡",
               "机会成本", "杠杆", "系统", "反馈", "迭代", "试错", "证伪", "概率", "期望值", "本质",
               "机制", "激励", "瓶颈", "拆解", "原则", "清单", "first principles", "mental model")
RULE_WORDS = ("我的原则", "我的规矩", "我从不", "我总是", "我一向", "如果是我", "我的做法", "经验告诉我",
              "我倾向于", "我的建议是", "记住", "唯一的办法", "不要", "宁可", "必须")
ANALOGY = ("就像", "好比", "比方", "想象一下", "如同", "类似于", "仿佛", "think of it as", "imagine")
HEDGE = ("也许", "或许", "可能", "大概", "不一定", "似乎", "看起来", "maybe", "probably", "perhaps")


# --------------------------------------------------------------------- 基础工具
def setup() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except Exception:
            pass


def die(msg: str, code: int = 1) -> int:
    print("错误：%s" % msg, file=sys.stderr)
    return code


def root_dir() -> Path:
    env = os.environ.get("PD_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    cur = Path.cwd().resolve()
    for d in (cur, *cur.parents):
        if (d / TOOLDIR).is_dir():
            return d
    return cur


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKC", name or "").strip().lower()
    s = re.sub(r"[\s_/\\]+", "-", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff\-]+", "", s).strip("-")
    return s[:48] or "persona"


def jread(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def jwrite(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def wtext(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")


def width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1
               for c in text if not unicodedata.combining(c))


# --------------------------------------------------------------------- 语料读取
def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def to_plain(text: str, suffix: str) -> str:
    if suffix in (".html", ".htm"):
        text = re.sub(r"(?is)<(script|style|nav|footer|head)\b.*?</\1>", " ", text)
        text = re.sub(r"(?i)<\s*(br|/p|/div|/li|/h[1-6])\s*/?\s*>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;|&#160;", " ", text)
        text = re.sub(r"&amp;", "&", text).replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def collect(targets, log=print):
    """读取文档（文件或目录），返回 [{title, text}]。只认 TEXT... 扩展名。"""
    docs, skipped = [], []
    for t in targets:
        p = Path(t).expanduser()
        files = sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in TEXTS) if p.is_dir() else [p]
        if not files:
            skipped.append("%s（目录中没有支持的文档）" % p)
            continue
        for f in files:
            if not f.is_file():
                skipped.append("%s（文件不存在）" % f)
                continue
            if f.suffix.lower() not in TEXTS:
                skipped.append("%s（不支持的格式，仅支持 %s）" % (f.name, "/".join(sorted(TEXTS))))
                continue
            text = to_plain(read_text(f), f.suffix.lower())
            if len(text) < 20:
                skipped.append("%s（内容太短）" % f.name)
                continue
            docs.append({"title": f.stem, "text": text, "path": str(f)})
    return docs, skipped


def chunk_doc(doc, target: int = 700, minimum: int = 120):
    """按 Markdown 标题分组，再按长度打包成检索单元。"""
    chunks, stack, buf = [], [], ""

    def flush():
        nonlocal buf
        if buf.strip():
            chunks.append({"title": doc["title"], "heading": " · ".join(stack), "text": buf.strip()})
        buf = ""

    for line in doc["text"].split("\n"):
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*$", line)
        if m:
            flush()
            stack[:] = stack[: len(m.group(1)) - 1]
            stack.append(m.group(2).strip())
            continue
        buf += line + "\n"
        if len(buf) >= target and line.strip() == "":
            flush()
    flush()
    if len(chunks) > 1:  # 合并过短碎片
        merged = []
        for c in chunks:
            if merged and len(merged[-1]["text"]) < minimum and len(merged[-1]["text"]) + len(c["text"]) <= target * 2:
                merged[-1]["text"] += "\n" + c["text"]
            else:
                merged.append(c)
        chunks = merged
    return chunks


# --------------------------------------------------------------------- 检索
_WORD = re.compile(r"[a-z0-9][a-z0-9'\-]+")
_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+")


def toks(text: str):
    text = (text or "").lower()
    out = _WORD.findall(text)
    for m in _CJK.finditer(text):
        run = m.group(0)
        out += list(run) + [run[i:i + 2] for i in range(len(run) - 1)]
    return out


def build_index(chunks):
    docs = [toks(c["text"] + " " + c.get("heading", "")) for c in chunks]
    df = Counter()
    for d in docs:
        df.update(set(d))
    avg = (sum(len(d) for d in docs) / len(docs)) if docs else 1.0
    return {"docs": docs, "df": df, "avg": avg or 1.0}


def search(chunks, idx, query: str, k: int = 6):
    q = set(toks(query))
    if not q:
        return []
    n, k1, b = len(chunks), 1.4, 0.7
    hits = []
    for chunk, d in zip(chunks, idx["docs"]):
        f = Counter(d)
        score = 0.0
        for t in q:
            if t not in f:
                continue
            idf = math.log(1 + (n - idx["df"][t] + 0.5) / (idx["df"][t] + 0.5))
            score += idf * f[t] * (k1 + 1) / (f[t] + k1 * (1 - b + b * len(d) / idx["avg"]))
        if score > 0:
            hits.append((round(score, 3), chunk))
    hits.sort(key=lambda x: -x[0])
    return hits[:k]


def snippet(text: str, query: str, limit: int = 200) -> str:
    sents = [s for s in re.split(r"(?<=[。！？!?…])|(?<=[；;])|\n+", text) if s.strip()]
    if not sents:
        return text[:limit]
    q = set(toks(query))
    best = max(range(len(sents)), key=lambda i: len(q & set(toks(sents[i]))) / (1 + len(toks(sents[i]))))
    out = ""
    for s in sents[best:]:
        out += s
        if len(out) >= limit:
            break
    return out.strip()[:limit]


# --------------------------------------------------------------------- 风格分析
def analyze(chunks):
    text = "\n".join(c["text"] for c in chunks)
    sents = [s for s in re.split(r"(?<=[。！？!?…；;])|\n+", text) if len(s.strip()) >= 6]
    n = max(1, len(sents))
    lens = [len(s) for s in sents] or [0]
    cnt = lambda words: sum(text.lower().count(w.lower()) for w in words)
    per_k = lambda c: round(c * 1000.0 / max(1, len(text)), 2)

    df = Counter()
    for c in chunks:
        df.update(set(toks(c["text"])))
    tf = Counter(toks(text))
    stop = set("的 了 是 在 我 有 和 就 不 人 都 一 也 很 到 说 要 去 你 会 着 这 他 她 它 那 们 与 及 但 而 或 "
               "什么 怎么 可以 我们 你们 这个 那个 因为 所以 已经 还是 只是 就是 不是 没有 起来 时候 事情 问题 "
               "the a an and or but of to in on at for with is are was were be it its this that as by from not".split())
    keywords = []
    for term, c in tf.most_common(400):
        if term in stop or len(term) < 2 or re.fullmatch(r"[a-z]{1,3}", term):
            continue
        keywords.append({"term": term, "score": round(c / max(1, sum(tf.values())) * math.log(1 + len(chunks) / df[term]) * 1000, 3)})
        if len(keywords) >= 16:
            break

    grams = Counter()
    for m in re.finditer(r"[\u4e00-\u9fff]{6,}", text):
        run = m.group(0)
        for size in (2, 3, 4):
            for i in range(len(run) - size + 1):
                g = run[i:i + size]
                if not re.fullmatch(r"[的了是和很就都也才又还我你他]+", g):
                    grams[g] += 1
    for size in (2, 3):
        words = re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", text)
        for i in range(len(words) - size + 1):
            grams[" ".join(w.lower() for w in words[i:i + size])] += 1
    phrases = []
    for g, c in grams.most_common(200):
        if c < 3:
            break
        if any(g in p or p in g for p in phrases):
            continue
        phrases.append(g)
        if len(phrases) >= 10:
            break

    tone = []
    if per_k(cnt(ANALOGY)) > 1:
        tone.append("善用类比与故事")
    if cnt(("一定", "必然", "绝对", "关键在于", "说到底", "本质", "必须")) > cnt(HEDGE):
        tone.append("结论明确、语气笃定")
    else:
        tone.append("措辞谨慎、常留余地")
    if sum(1 for s in sents if "？" in s or "?" in s) / n > 0.1:
        tone.append("爱用反问")
    if len([s for s in sents if "我" in s]) / n > 0.3:
        tone.append("第一人称讲经验")

    style = ["句子%s" % ("偏长、层层递进" if sum(lens) / len(lens) >= 45 else "短促、直给结论"),
             "长短句交错" if 20 < sum(lens) / len(lens) < 45 else "句式统一"]
    pick = lambda words, limit: [str(s).strip() for s in sents if any(w in s.lower() for w in words)][:limit]
    return {
        "chars": len(text), "sentence_count": len(sents), "avg_sentence": round(sum(lens) / len(lens), 1),
        "tone": tone, "sentence_style": "；".join(style), "keywords": keywords, "phrases": phrases,
        "analogy_per_kchar": per_k(cnt(ANALOGY)), "hedge_per_kchar": per_k(cnt(HEDGE)),
        "models": sorted({w for w in MODEL_WORDS if w in text.lower()})[:8],
        "rules": pick(RULE_WORDS, 6), "self_statements": pick(("我",), 4),
    }


def mine_quotes(chunks, limit: int = 20):
    quotes, seen = [], set()

    def push(text, locator):
        q = (text or "").strip(" \t\"'「」『』“”>*-—")
        key = re.sub(r"\s+", "", q)
        if 6 <= len(q) <= 200 and key not in seen:
            seen.add(key)
            quotes.append({"text": q, "locator": locator})

    for c in chunks:
        locator = "%s · %s" % (c["title"], c["heading"]) if c["heading"] else c["title"]
        for m in re.finditer(r"[「『“\"]([^「」『』“”\n]{6,150})[」』”\"]", c["text"]):
            push(m.group(1), locator)
        for line in c["text"].split("\n"):
            line = line.strip()
            if line.startswith((">", "-", "*")) or "——" in line:
                body = re.sub(r"^[-*>\s]+", "", line)
                if 8 <= len(body) <= 90 or "——" in body:
                    push(body, locator)
        for s in re.split(r"(?<=[。！？!?])|\n+", c["text"]):
            s = s.strip()
            if 10 <= len(s) <= 60 and sum(w in s for w in ("永远", "不要", "必须", "关键", "本质", "记住", "其实", "真正")) >= 2:
                push(s, locator)
        if len(quotes) >= limit * 3:
            break
    quotes.sort(key=len)
    return quotes[:limit]


def build_card(meta, chunks):
    a = analyze(chunks)
    name = meta.get("name") or meta["slug"]
    domain = meta.get("domain") or "通用"
    return {
        "slug": meta["slug"], "display_name": name, "domain": domain,
        "one_line": "%s（%s）的可交互人格：%s" % (name, domain, "、".join(a["tone"])),
        "identity_summary": "%s 的语料规模 %d 字 / %d 个证据块，风格特征：%s；句式：%s。"
                            % (name, a["chars"], len(chunks), "、".join(a["tone"]), a["sentence_style"]),
        "voice": {"tone": a["tone"], "sentence_style": a["sentence_style"],
                  "keywords": [k["term"] for k in a["keywords"]],
                  "phrases": a["phrases"],
                  "devices": (["先用类比降低门槛，再给结论"] if a["analogy_per_kchar"] > 1 else []) +
                             (["结论大于铺垫，敢下判断"] if a["hedge_per_kchar"] <= 1 else ["用限定词控制承诺强度"]),
                  "avoid": ["空话套话", "材料中没有的数字/人名/名言", "通用 AI 腔"]},
        "thinking": {"models": a["models"], "rules": a["rules"], "self_statements": a["self_statements"]},
        "quotes": mine_quotes(chunks),
        "stats": {"chars": a["chars"], "chunks": len(chunks), "sentences": a["sentence_count"]},
        "guardrails": [
            "我是依据指定文档构建的人格模拟体，不是本人，不得以本人名义承诺或背书。",
            "材料里没有的事实、数字、名言一律不编；没有就直说「材料里没有」。",
            "关键论断要标注出处（《文档标题》· 章节），单条引用不超过两句话。",
            "高风险议题（医疗/法律/投资/人身安全）只给思考框架，并提示咨询专业人士。",
        ],
    }


# --------------------------------------------------------------------- SKILL.md
SKILL_TMPL = """---
name: persona-{slug}
description: {name}（{domain}）的可交互人格 Skill：需要以他的视角对话或请他做事时使用；基于 {chars} 字自有语料蒸馏。
version: {version}
---

# {name} · 人格 Skill

> 由 pd 从「{name}」的文档语料蒸馏而来：模拟其语言与思维，但不是本人。

## 1. 你是谁
{identity}

- **一句话人设**：{one_line}
- **领域**：{domain}
- **语料**：{chars} 字 / {chunks} 个证据块

## 2. 语言风格
- 语气：{tone}
- 句式：{sentence_style}
- 修辞习惯：{devices}
- 高频词：{keywords}
- 口头禅：{phrases}
- 禁止出现：{avoid}

## 3. 思维模型与规矩
**思维模型**：{models}

**决策规矩**：
{rules}

**第一人称自述**：
{self_statements}

## 4. 代表原话（引用示范）
{quotes}

## 5. 工作协议

### 两种模式
| 模式 | 场景 | 输出形态 |
| --- | --- | --- |
| `interview`（交流） | 问观点、经历、价值观 | {m_interview} |
| `delegate`（委托做事） | 请教方案、要交付物 | {m_delegate} |

### 证据规则（最重要）
1. 先检索文档语料中的原文（`python pd.py ask {slug} "关键词" --offline` 可先看证据），再作答。
2. 三类话必须区分：材料明确说过的 → 引用并标出处；能推出的 → 注明「这是我的推断」；材料没有的 → 直说没有，只给方法论。
3. 严禁编造名言、数字、经历；严禁把用户观点说成本人观点；不整段照抄原文。

### 委托模式模板
```
【结论】一句话判断。
【为什么】2-4 条理由，尽量对应上面的思维模型。
【怎么做】分步骤方案/清单/计划，写清交付物形态。
【取舍与风险】2-3 个最容易被忽略的坑，以及方案失效的条件。
【待你确认】1-3 个必须由用户拍板的问题。
```

## 6. 边界与合规
{guardrails}

## 7. 自检清单（每次回答前）
- [ ] 引用了材料吗？出处对不对？
- [ ] 像他说话吗（用的是他的词和模型，不是通用 AI 腔）？
- [ ] 有没有编造材料里没有的东西？
- [ ] 用户能不能直接拿去做事（有交付物吗）？
"""


def render_skill(card) -> str:
    lines = lambda items, empty="（暂无）": "\n".join("- %s" % i for i in items) if items else "- " + empty
    numbered = lambda items, empty="（暂无）": "\n".join("%d. %s" % (i, t) for i, t in enumerate(items, 1)) if items else "1. " + empty
    quotes = "\n".join("> %s\n> —— %s" % (q["text"], q["locator"]) for q in card["quotes"][:5]) or "> （文档中暂无可引用原话）"
    voice, think = card["voice"], card["thinking"]
    return SKILL_TMPL.format(
        slug=card["slug"], name=card["display_name"], domain=card["domain"], version=VERSION,
        chars=card["stats"]["chars"], chunks=card["stats"]["chunks"], identity=card["identity_summary"],
        one_line=card["one_line"], tone="、".join(voice["tone"]), sentence_style=voice["sentence_style"],
        devices="；".join(voice["devices"]), keywords="、".join(voice["keywords"]),
        phrases="、".join("「%s」" % p for p in voice["phrases"]) or "（暂无）",
        avoid="、".join(voice["avoid"]), models="、".join(think["models"]) or "（未识别到显式模型）",
        rules=numbered(think["rules"]), self_statements=lines(think["self_statements"]),
        quotes=quotes, guardrails=lines(card["guardrails"]),
        m_interview=MODES["interview"][2], m_delegate=MODES["delegate"][2],
    )


def system_prompt(card, mode: str) -> str:
    name, label, goal, shape = card["display_name"], MODES[mode][0], MODES[mode][1], MODES[mode][2]
    voice, think = card["voice"], card["thinking"]
    return (
        "你是「%s」的可交互人格模拟体（由 pd 从自有文档语料蒸馏，非本人）。\n"
        "身份：%s\n领域：%s\n\n"
        "语言风格（必须遵守）：语气 %s；句式 %s；修辞 %s；常用词 %s；口头禅 %s；禁止 %s。\n\n"
        "思维模型：%s\n决策规矩：%s\n\n"
        "当前模式：%s —— %s 输出形态：%s\n\n"
        "证据规则：用户消息 <evidence> 中是检索到的原文（带编号与出处）。"
        "事实、观点、经历只能来自证据或常识边界内，关键论断标注 [n] 或「《文档》· 章节」；"
        "能用材料推出的写明「这是我的推断」；材料没有的直说「这不在我掌握的材料里」。"
        "严禁编造名言/数字/经历，严禁整段照抄，单条引用不超过两句。\n\n"
        "边界：%s"
        % (name, card["identity_summary"], card["domain"], "、".join(voice["tone"]), voice["sentence_style"],
           "；".join(voice["devices"]) or "（无）", "、".join(voice["keywords"]),
           "、".join("「%s」" % p for p in voice["phrases"]) or "（无）", "、".join(voice["avoid"]),
           "、".join(think["models"]) or "（未识别）", "；".join(think["rules"]) or "（未识别）",
           label, goal, shape, " ".join(card["guardrails"]))
    )


# --------------------------------------------------------------------- LLM（可选）
def llm_config():
    key = os.environ.get("PD_API_KEY", "")
    base = os.environ.get("PD_BASE_URL", "")
    model = os.environ.get("PD_MODEL", "")
    for env, b, m in (("DASHSCOPE_API_KEY", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
                      ("OPENAI_API_KEY", "https://api.openai.com/v1", "gpt-4o-mini"),
                      ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "deepseek-chat")):
        if not key and os.environ.get(env):
            key, base, model = os.environ[env], base or b, model or m
    return key, base.rstrip("/") or "", model


def llm_chat(messages, max_tokens=1600):
    key, base, model = llm_config()
    if not (key and base and model):
        return None, "未配置 LLM（可用 --offline 或设置 PD_API_KEY/PD_BASE_URL/PD_MODEL）"
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.7,
                       "max_tokens": max_tokens}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + "/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return data["choices"][0]["message"]["content"].strip(), None
    except urllib.error.HTTPError as e:
        return None, "LLM HTTP %s：%s" % (e.code, e.read().decode("utf-8", "replace")[:200])
    except Exception as e:
        return None, "LLM 调用失败：%s" % e


# --------------------------------------------------------------------- 命令
def kb_path(slug: str) -> Path:
    return root_dir() / TOOLDIR / slug / "kb.json"


def load_kb(slug: str) -> dict:
    data = jread(kb_path(slug))
    if not data:
        raise SystemExit(die("persona '%s' 不存在。先执行：python pd.py new %s" % (slug, slug)))
    return data


def cmd_new(args) -> int:
    slug = args.slug or slugify(args.name or "")
    path = kb_path(slug)
    if path.exists():
        return die("persona '%s' 已存在" % slug, 2)
    jwrite(path, {"slug": slug, "name": args.name or slug, "domain": args.domain or "",
                  "created_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"), "docs": [], "chunks": []})
    print("已创建：%s（slug=%s）\n  下一步：python pd.py add %s <文档或目录>" % (args.name or slug, slug, slug))
    return 0


def cmd_add(args) -> int:
    kb = load_kb(args.slug)
    docs, skipped = collect(args.target, log=print)
    added = 0
    have = {d["sha1"] for d in kb["docs"]}
    for d in docs:
        sha = __import__("hashlib").sha1(d["text"].encode("utf-8")).hexdigest()
        if sha in have:
            print("  跳过（内容重复）：%s" % d["title"])
            continue
        have.add(sha)
        kb["docs"].append({"title": d["title"], "sha1": sha, "chars": len(d["text"]),
                           "tags": args.tag or [], "path": d["path"]})
        kb["chunks"].extend(chunk_doc(d))
        added += 1
        print("  + %s（%d 字）" % (d["title"], len(d["text"])))
    for s in skipped:
        print("  跳过：%s" % s)
    jwrite(kb_path(args.slug), kb)
    print("导入完成：新增 %d 篇，累计 %d 篇 / %d 个证据块 / %d 字\n  下一步：python pd.py distill %s"
          % (added, len(kb["docs"]), len(kb["chunks"]), sum(c["chars"] for c in kb["docs"]), args.slug))
    return 0


def cmd_distill(args) -> int:
    kb = load_kb(args.slug)
    if not kb["chunks"]:
        return die("语料为空，先导入文档：python pd.py add %s <文档或目录>" % args.slug, 1)
    out = Path(args.out).resolve() if args.out else root_dir() / "skills" / kb["slug"]
    card = build_card(kb, kb["chunks"])
    card["distilled_by"] = "heuristics"
    note = ""
    if args.llm and not args.offline:
        question = ("请阅读以下语料抽样，为「%s」写一段 80-160 字的人物设定摘要（他是谁、以什么著称、"
                    "说话与思考方式），只输出这一段文字，不要编造材料外的事实。\n\n%s"
                    % (card["display_name"], "\n\n".join("[%d] %s" % (i, c["text"][:600])
                                                          for i, c in enumerate(kb["chunks"][:10], 1))))
        reply, err = llm_chat([{"role": "user", "content": question}], max_tokens=600)
        if reply:
            card["identity_summary"] = reply.strip()
            card["distilled_by"] = "heuristics + %s" % llm_config()[2]
            note = "（已用 LLM 润色人设摘要）"
        else:
            note = "（LLM 不可用：%s，已用启发式结果）" % err
    wtext(out / "SKILL.md", render_skill(card))
    jwrite(out / "persona.json", card)
    wtext(out / "evidence.md", "# 证据块（%d 条）\n\n" % len(kb["chunks"]) + "\n\n".join(
        "%d. [%s] %s\n   %s" % (i, c["title"], c["heading"] or "-", c["text"][:400].replace("\n", " "))
        for i, c in enumerate(kb["chunks"], 1)))
    print("蒸馏完成%s：%s\n  人设：%s\n  语料：%d 篇 / %d 块 / %d 字（方式：%s）\n  试一句：python pd.py ask %s \"...\""
          % (note, out, card["one_line"], len(kb["docs"]), len(kb["chunks"]), card["stats"]["chars"],
             card["distilled_by"], kb["slug"]))
    return 0


def load_card(slug: str):
    for p in (Path.cwd() / "skills" / slug / "persona.json", root_dir() / "skills" / slug / "persona.json"):
        card = jread(p)
        if card:
            return card
    raise SystemExit(die("尚未蒸馏 persona '%s'，先执行：python pd.py distill %s" % (slug, slug)))


def cmd_ask(args) -> int:
    kb, card = load_kb(args.slug), load_card(args.slug)
    query = " ".join(args.question).strip()
    mode = args.mode if args.mode in MODES else "interview"
    chunks = kb["chunks"]
    hits = search(chunks, build_index(chunks), query, k=args.k)
    evidence = "\n\n".join("[%d] %s · %s\n%s" % (i, c["title"], c["heading"] or "-", c["text"][:800])
                           for i, (_, c) in enumerate(hits, 1)) or "（未检索到相关材料）"
    messages = [{"role": "system", "content": system_prompt(card, mode)}]
    messages += [{"role": r, "content": c} for r, c in args.history]
    messages.append({"role": "user", "content": "<evidence>\n%s\n</evidence>\n\n任务：%s\n\n输出要求：%s"
                     % (evidence, query, MODES[mode][2])})
    if args.show_prompt:
        print("\n".join("[%s]\n%s" % (m["role"], m["content"]) for m in messages) + "\n" + "=" * 60)
    if args.offline:
        answer, err = None, "已指定 --offline"
    else:
        answer, err = llm_chat(messages)
    if answer:
        print(answer)
    else:
        print("【离线检索模式】未调用模型（%s）——以下是证据与推演骨架，不是模型生成的成段回答。\n" % err)
        print("## 相关材料")
        if hits:
            for i, (score, c) in enumerate(hits, 1):
                print("%d. [%.2f] %s" % (i, score, snippet(c["text"], query, 220)))
                print("   出处：%s · %s" % (c["title"], c["heading"] or "-"))
        else:
            print("（没有检索到相关内容——该问题可能超出这份语料的覆盖范围）")
        print("\n## 他会怎么拆解（来自 persona.json）")
        print("思维模型：%s" % ("、".join(card["thinking"]["models"]) or "（未识别）"))
        for r in card["thinking"]["rules"][:4]:
            print("- %s" % r)
        print("\n## 作答骨架（%s 模式）" % MODES[mode][0])
        print("开场：「%s」" % (card["voice"]["phrases"][0] if card["voice"]["phrases"] else "先说结论："))
        if card["quotes"]:
            print("可锚定的原话：「%s」（%s）" % (card["quotes"][0]["text"], card["quotes"][0]["locator"]))
        print("展开：围绕上面第 1、2 条证据，逐条标注出处；材料缺口要明说。")
        print("收尾：给出下一步动作或一个反问。")
        print("\n提示：配置 LLM 后重跑即可得到成段回答。")
    return 0


def cmd_list(args) -> int:
    base = root_dir() / TOOLDIR
    slugs = sorted(p.name for p in base.iterdir() if (p / "kb.json").exists()) if base.is_dir() else []
    if not slugs:
        print("还没有 persona。先执行：python pd.py new 费曼 --domain \"物理/教学\"")
        return 0
    rows = [("SLUG", "名称", "领域", "篇数", "证据块", "字数", "Skill")]
    for s in slugs:
        kb = jread(kb_path(s), {}) or {}
        rows.append((s, kb.get("name", s), kb.get("domain", "-"), str(len(kb.get("docs", []))),
                     str(len(kb.get("chunks", []))), str(sum(c["chars"] for c in kb.get("docs", []))),
                     "✓" if (root_dir() / "skills" / s / "SKILL.md").exists() else "-"))
    widths = [max(width(r[i]) for r in rows) for i in range(len(rows[0]))]
    for i, row in enumerate(rows):
        print("  ".join(cell + " " * max(0, widths[j] - width(cell)) for j, cell in enumerate(row)).rstrip())
        if i == 0:
            print("  ".join("-" * w for w in widths))
    return 0


def cmd_show(args) -> int:
    kb = load_kb(args.slug)
    card = jread(root_dir() / "skills" / args.slug / "persona.json", {})
    print("# %s（%s）\n领域：%s\n语料：%d 篇 / %d 块 / %d 字\n"
          % (kb.get("name"), kb["slug"], kb.get("domain") or "-", len(kb["docs"]),
             len(kb["chunks"]), sum(c["chars"] for c in kb["docs"])))
    print("文档：")
    for i, d in enumerate(kb["docs"], 1):
        print("  [%d] %s（%d 字，sha1=%s）" % (i, d["title"], d["chars"], d["sha1"][:10]))
    if card:
        print("\nSkill：skills/%s/SKILL.md（%s）\n人设：%s" % (args.slug, card.get("distilled_by"), card["one_line"]))
        print("口头禅：%s" % "、".join(card["voice"]["phrases"][:8]))
        print("高频词：%s" % "、".join(card["voice"]["keywords"][:12]))
    else:
        print("\n尚未蒸馏 → python pd.py distill %s" % args.slug)
    return 0


def cmd_rm(args) -> int:
    import shutil
    targets = [p for p in (root_dir() / TOOLDIR / args.slug, root_dir() / "skills" / args.slug) if p.exists()]
    if not targets:
        return die("未找到 persona '%s'" % args.slug, 1)
    if not args.yes:
        print("将删除：\n  " + "\n  ".join(map(str, targets)))
        if input("确认？输入 yes 继续：").strip().lower() != "yes":
            print("已取消。")
            return 0
    for p in targets:
        shutil.rmtree(p, ignore_errors=True)
    print("已删除 %d 个目录。" % len(targets))
    return 0


# --------------------------------------------------------------------- 入口
def main(argv=None) -> int:
    setup()
    p = argparse.ArgumentParser(prog="pd", description="pd —— 文档语料 → 人格 Skill（零第三方依赖）")
    p.add_argument("--version", action="version", version="pd " + VERSION)
    sub = p.add_subparsers(dest="cmd", metavar="<命令>")

    s = sub.add_parser("new", help="创建 persona")
    s.add_argument("name", nargs="?", help="名字，如 费曼")
    s.add_argument("--slug")
    s.add_argument("--domain", help="领域，如 \"物理学/教学\"")
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("add", help="导入文档（唯一支持的资料类型）")
    s.add_argument("slug")
    s.add_argument("target", help="文档路径或目录")
    s.add_argument("--tag", action="append", help="标签（可重复）")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("distill", help="蒸馏成 Skill（SKILL.md + persona.json）")
    s.add_argument("slug")
    s.add_argument("--out", help="输出目录（默认 skills/<slug>）")
    s.add_argument("--llm", action="store_true", help="用 LLM 润色人设摘要（需配置 API Key）")
    s.add_argument("--offline", action="store_true", help="强制纯启发式")
    s.set_defaults(func=cmd_distill)

    s = sub.add_parser("ask", help="提问（LLM 优先，未配置则离线检索）")
    s.add_argument("slug")
    s.add_argument("question", nargs="+")
    s.add_argument("--mode", default="interview", choices=sorted(MODES), help="interview(交流)/delegate(委托)")
    s.add_argument("-k", type=int, default=6, help="检索证据条数")
    s.add_argument("--history", nargs="*", default=[], help=argparse.SUPPRESS)
    s.add_argument("--offline", action="store_true", help="不调用 LLM")
    s.add_argument("--show-prompt", dest="show_prompt", action="store_true", help="打印实际发送的提示词")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("list", help="列出所有 persona")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="查看 persona 详情")
    s.add_argument("slug")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("rm", help="删除 persona")
    s.add_argument("slug")
    s.add_argument("--yes", action="store_true", help="跳过确认")
    s.set_defaults(func=cmd_rm)

    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        p.print_help()
        return 0
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
