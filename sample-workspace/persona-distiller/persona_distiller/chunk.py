"""切片与语录挖掘。

- ``chunk_text``：按 Markdown 标题 / 章节 / 段落聚合，产出 300~900 字的检索单元。
- ``mine_quotes``：从文本中挑出「像语录的句子」（引号、破折号署名、> 引用块、
  '说：' 等模式），为语录库打底。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_MIN = 120
DEFAULT_TARGET = 700
DEFAULT_MAX = 1100

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_CHAPTER_RE = re.compile(
    r"^\s*(第\s*[0-9一二三四五六七八九十百千零两]+\s*[章节回講讲篇部]|"
    r"chapter\s+\d+|part\s+[ivx\d]+|prologue|epilogue|序言|前言|后记|结语|尾声|附录)\b.*$",
    re.I,
)
_SENT_SPLIT = re.compile(r"(?<=[。！？!?…])|(?<=[；;])|(?<=\.\s)|(?<=[!?]\s)|\n+")
_QUOTE_BRACKETS = re.compile(r"[「『“\"]([^「」『』“”\n]{4,220})[」』”\"]")
_ATTR_DASH = re.compile(r"[—-]{1,2}\s*([A-Za-z\u4e00-\u9fff·\.\s]{2,24})\s*$")
_SAID = re.compile(r"(?:说|讲|写道|写道：|said|wrote|remarked)\s*[:：]?\s*[「『“\"](.+?)[」』”\"]", re.I)


def chunk_text(
    text: str,
    kind: str = "doc",
    heading: str = "",
    min_chars: int = DEFAULT_MIN,
    target: int = DEFAULT_TARGET,
    max_chars: int = DEFAULT_MAX,
) -> List[Dict[str, Any]]:
    """把一段文本切成检索单元（保留标题路径）。"""
    text = (text or "").strip()
    if not text:
        return []
    sections = _split_sections(text, heading)
    out: List[Dict[str, Any]] = []
    ordno = 0
    for sec_heading, body in sections:
        for piece in _pack(body, min_chars=min_chars, target=target, max_chars=max_chars):
            out.append({"ord": ordno, "heading": sec_heading, "text": piece, "kind": kind})
            ordno += 1
    return out


def _split_sections(text: str, base_heading: str) -> List[Tuple[str, str]]:
    lines = text.split("\n")
    sections: List[Tuple[str, List[str]]] = []
    stack: List[str] = [base_heading] if base_heading else []
    cur: List[str] = []
    for line in lines:
        m = _HEADING_RE.match(line)
        if m or _CHAPTER_RE.match(line):
            if cur:
                sections.append((" · ".join([s for s in stack if s]), "\n".join(cur)))
                cur = []
            title = (m.group(2) if m else line.strip()).strip()
            level = len(m.group(1)) if m else 2
            stack = stack[: max(0, level - 1 if m else len(stack))]
            stack.append(title)
            continue
        cur.append(line)
    if cur:
        sections.append((" · ".join([s for s in stack if s]), "\n".join(cur)))
    return [(h, b) for h, b in sections if b.strip()]


def _pack(body: str, min_chars: int, target: int, max_chars: int) -> List[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    if not paragraphs:
        paragraphs = [p.strip() for p in body.split("\n") if p.strip()]
    pieces: List[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > max_chars:
            if buf:
                pieces.append(buf)
                buf = ""
            for i in range(0, len(para), max_chars):
                pieces.append(para[i: i + max_chars])
            continue
        candidate = (buf + "\n" + para).strip() if buf else para
        if len(candidate) > target and len(buf) >= min_chars:
            pieces.append(buf)
            buf = para
        else:
            buf = candidate
    if buf:
        pieces.append(buf)
    # 合并过短的碎片，避免噪声块
    merged: List[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) < min_chars and len(merged[-1]) + len(piece) <= max_chars:
            merged[-1] = merged[-1] + "\n" + piece
        else:
            merged.append(piece)
    return [p for p in merged if p.strip()]


# --------------------------------------------------------------------------- 语录
def split_sentences(text: str, min_len: int = 4) -> List[str]:
    raw = _SENT_SPLIT.split(text or "")
    out = []
    for s in raw:
        s = (s or "").strip(" \t\u3000")
        if len(s) >= min_len:
            out.append(s)
    return out


def mine_quotes(text: str, limit: int = 200) -> List[Dict[str, str]]:
    """启发式语录挖掘，返回 [{text, context, pattern}]。"""
    results: List[Dict[str, str]] = []
    seen = set()

    def push(quote: str, context: str, pattern: str) -> None:
        q = (quote or "").strip(" \t\u3000\"'「」『』“”")
        if len(q) < 6 or len(q) > 200:
            return
        key = re.sub(r"\s+", "", q)
        if key in seen:
            return
        seen.add(key)
        results.append({"text": q, "context": context.strip()[:200], "pattern": pattern})

    # 1) 引号包裹
    for m in _QUOTE_BRACKETS.finditer(text or ""):
        start = max(0, m.start() - 60)
        push(m.group(1), (text or "")[start: m.end() + 40], "bracket")

    # 2) 说：…… 模式
    for m in _SAID.finditer(text or ""):
        start = max(0, m.start() - 60)
        push(m.group(1), (text or "")[start: m.end() + 30], "said")

    # 3) 破折号署名（“格言 —— 某人”）
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line or len(line) > 220:
            continue
        if line.startswith(">"):
            push(line.lstrip("> ").strip(), line, "blockquote")
            continue
        if re.match(r"^[-*+]\s+", line) or "——" in line or line.count("—") >= 2:
            m = _ATTR_DASH.search(line)
            if m:
                push(line[: m.start()].strip(" -—\t"), line, "attributed")

    # 4) 无引号的格言式短句（含断言/比喻标记且足够短）
    for sent in split_sentences(text or "", min_len=8):
        if len(sent) > 90:
            continue
        if _looks_aphoristic(sent):
            push(sent, sent, "aphorism")

    return results[:limit]


_APHORISM_MARKERS = (
    "永远", "从来", "不要", "必须", "关键", "本质", "记住", "如果你", "与其", "宁可",
    "只有", "唯一", "最重要", "第一", "绝对", "别把", "别让", "真正", "最好的", "比",
    "never", "always", "the key", "remember", "don't", "the best", "only", "if you",
)


def _looks_aphoristic(sentence: str) -> bool:
    low = sentence.lower()
    hits = sum(1 for m in _APHORISM_MARKERS if m in low)
    if hits == 0:
        return False
    if "," in sentence or "，" in sentence:
        hits += 1
    return hits >= 2 and len(sentence) >= 10
