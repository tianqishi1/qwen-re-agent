"""检索：BM25 + 中英文混合分词。

中文没有空格，这里用「单字 + 二元组（bigram）」近似分词：
对检索召回非常有效且零依赖（日文假名、韩文同理）。英文按单词切。

打分 = BM25(query, chunk) × chunk.weight × (标题命中加成)
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_LATIN = re.compile(r"[a-z0-9][a-z0-9'\-]+")
_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
_MULTI_HINT = re.compile(r"[\u3400-\u9fff]")


def tokenize(text: str) -> List[str]:
    """中英混合分词。"""
    text = (text or "").lower()
    tokens = _LATIN.findall(text)
    for m in _CJK.finditer(text):
        run = m.group(0)
        tokens.extend(run)
        tokens.extend(run[i: i + 2] for i in range(len(run) - 1))
    return tokens


@dataclass
class Hit:
    chunk: Any
    score: float
    matched: List[str]

    def to_dict(self, snippet_chars: int = 260) -> Dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "kind": getattr(self.chunk, "kind", ""),
            "locator": getattr(self.chunk, "locator", ""),
            "heading": getattr(self.chunk, "heading", ""),
            "url": getattr(self.chunk, "url", ""),
            "text": (getattr(self.chunk, "text", "") or "")[:snippet_chars],
            "matched": self.matched[:8],
        }


class BM25:
    """轻量 BM25（k1/b 可调），语料规模到 10 万块仍然够用。"""

    def __init__(self, k1: float = 1.4, b: float = 0.72) -> None:
        self.k1 = k1
        self.b = b
        self.docs: List[Any] = []
        self.doc_tokens: List[List[str]] = []
        self.doc_freq: List[Dict[str, int]] = []
        self.df: Dict[str, int] = {}
        self.avg_len: float = 0.0

    def build(self, chunks: Sequence[Any]) -> "BM25":
        self.docs = list(chunks)
        self.doc_tokens = []
        self.doc_freq = []
        self.df = {}
        for ch in self.docs:
            text = (getattr(ch, "text", "") or "")
            heading = (getattr(ch, "heading", "") or "")
            title = (getattr(ch, "title", "") or "")
            toks = tokenize(text)
            toks += tokenize(heading)
            toks += tokenize(title) * 2  # 标题词重复以加权
            self.doc_tokens.append(toks)
            freq: Dict[str, int] = {}
            for t in toks:
                freq[t] = freq.get(t, 0) + 1
            self.doc_freq.append(freq)
            for t in freq:
                self.df[t] = self.df.get(t, 0) + 1
        self.avg_len = (sum(len(t) for t in self.doc_tokens) / len(self.doc_tokens)) if self.doc_tokens else 0.0
        return self

    def search(
        self,
        query: str,
        k: int = 8,
        kinds: Optional[Iterable[str]] = None,
        min_score: float = 0.0,
        weighted: bool = True,
    ) -> List[Hit]:
        if not self.docs:
            return []
        q_tokens = tokenize(query)
        if not q_tokens:
            return []
        q_counts: Dict[str, int] = {}
        for t in q_tokens:
            q_counts[t] = q_counts.get(t, 0) + 1
        n_docs = len(self.docs)
        allow = set(kinds) if kinds else None
        hits: List[Hit] = []
        for i, ch in enumerate(self.docs):
            if allow is not None and getattr(ch, "kind", "") not in allow:
                continue
            freq = self.doc_freq[i]
            dl = len(self.doc_tokens[i]) or 1
            score = 0.0
            matched: List[str] = []
            for term, q_weight in q_counts.items():
                tf = freq.get(term, 0)
                if not tf:
                    continue
                df = self.df.get(term, 0)
                idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avg_len or 1))
                score += idf * (tf * (self.k1 + 1) / denom) * (1.0 + 0.15 * (q_weight - 1))
                matched.append(term)
            if score <= 0:
                continue
            if weighted:
                score *= float(getattr(ch, "weight", 1.0) or 1.0)
            if score >= min_score:
                hits.append(Hit(chunk=ch, score=score, matched=matched))
        hits.sort(key=lambda h: (-h.score, getattr(h.chunk, "id", 0)))
        return hits[:k]


def best_snippet(text: str, query: str, chars: int = 220) -> str:
    """从长文本中截出与 query 最相关的窗口。"""
    text = (text or "").strip()
    if len(text) <= chars:
        return text
    q = set(tokenize(query))
    if not q:
        return text[:chars]
    from .chunk import split_sentences

    sents = split_sentences(text, min_len=4)
    best_i, best_score = 0, -1.0
    for i, s in enumerate(sents):
        s_tokens = set(tokenize(s))
        score = len(q & s_tokens) / (1 + math.log(1 + len(s_tokens)))
        if score > best_score:
            best_i, best_score = i, score
    window = ""
    i = best_i
    while i < len(sents) and len(window) < chars:
        window += sents[i]
        i += 1
    return window[:chars]
