"""风格与思维分析：从文本统计出可写进人格卡的「说话方式」与「思维模型」。

全部为可解释的启发式统计，不依赖 LLM：
    - 语言指纹：句长、问句率、感叹率、第一人称、比喻/断言/含糊用词
    - 特征词与口头禅（n-gram 频次 + 覆盖去重）
    - 思维模型、决策启发式、价值观候选句

这些结果既是离线模式下的回答依据，也是喂给 LLM 蒸馏的压缩上下文。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .chunk import split_sentences
from .retrieve import tokenize

# --------------------------------------------------------------------------- 标记词库
ANALOGY_MARKERS = (
    "就像", "好比", "打个比方", "想象一下", "不妨设想", "如同", "类似于", "似的", "仿佛",
    "think of it as", "imagine", "like a", "analogy", "picture this", "such as",
)
HEDGE_MARKERS = (
    "也许", "或许", "可能", "大概", "不一定", "我猜", "似乎", "看起来", "据说", "应该是",
    "maybe", "probably", "perhaps", "i guess", "roughly", "more or less", "it seems",
)
ASSERTIVE_MARKERS = (
    "一定", "必然", "绝对", "毫无疑问", "关键在于", "本质上是", "其实", "说到底", "根本",
    "务必", "必须", "重要的是", "certainly", "definitely", "the key is", "always",
    "never", "in fact", "the truth is",
)
FIRST_PERSON_MARKERS = ("我", "我们", "咱", "我以为", "在我看来", "我觉得", " i ", " my ", " me ", "we ")
QUESTION_MARKERS = ("吗？", "呢？", "？", "为什么", "怎么办", "怎么", "如何", "what if", "why ", "?")
MODEL_MARKERS = (
    "第一性原理", "第一原理", "类比", "心智模型", "模型", "框架", "范式", "反过来想", "逆向",
    "复利", "复利效应", "权衡", "trade-off", "杠杆", "二八", "奥卡姆", "系统", "反馈回路",
    "飞轮", "路径依赖", "机会成本", "边际", "递归", "迭代", "试错", "假设检验", "证伪",
    "概率", "期望值", "不对称", "元认知", "清单", "原则", "本质", "机制", "激励", "冗余",
    "瓶颈", "拆解", "抽象", "最小可行", "mvp", "博弈", "囚徒", "熵", "涌现",
    "first principles", "mental model", "invert", "compound", "leverage", "second-order",
    "systems thinking", "feedback loop", "opportunity cost", "falsif", "asymmetr", "incentive",
)
DECISION_MARKERS = (
    "我的原则", "我的规矩", "我从不", "我总是", "我一向", "一般来说", "如果是我", "遇到这种情况",
    "我的做法", "经验告诉我", "我倾向于", "我的建议是", "记住这一点", "唯一的办法",
    "my rule", "i always", "i never", "i tend to", "my approach", "when in doubt",
)
VALUE_MARKERS = (
    "诚实", "诚信", "好奇", "求真", "求真知", "自由", "责任", "长期", "耐心", "独立", "正直",
    "尊严", "谦逊", "勇气", "勤奋", "简洁", "美", "敬畏", "共赢", "承诺", "信任", "公平",
    "honest", "curious", "integrity", "freedom", "long-term", "patience", "truth", "simplicity",
    "courage", "humility", "trust", "fairness",
)
DOMAIN_MARKERS = {
    "物理与自然科学": ("物理", "量子", "相对论", "熵", "粒子", "实验", "biology", "physics", "chemistry"),
    "工程与技术": ("工程", "架构", "算法", "代码", "系统设计", "性能", "冗余", "software", "engineering"),
    "商业与战略": ("战略", "市场", "客户", "增长", "护城河", "组织", "融资", "商业模式", "strategy", "revenue"),
    "产品与设计": ("产品", "用户", "体验", "设计", "需求", "迭代", "prototype", "user", "design"),
    "投资与决策": ("风险", "收益", "估值", "配置", "复利", "期望值", "portfolio", "valuation"),
    "写作与表达": ("写作", "叙事", "文章", "读者", "故事", "编辑", "writing", "narrative"),
    "管理与领导": ("团队", "管理", "授权", "招聘", "文化", "指标", "leadership", "team"),
    "心理与认知": ("认知", "偏见", "情绪", "动机", "习惯", "认知偏差", "bias", "cognitive"),
    "军事与博弈": ("战场", "兵力", "地形", "后勤", "情报", "战略纵深", "morale", "logistics"),
    "哲学与伦理": ("哲学", "伦理", "意义", "价值", "道德", "存在", "ethics", "philosophy"),
}

_STOP = set("""
的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 自己 这
他 她 它 那 们 与 及 但 而 或 如果 因为 所以 这个 那个 什么 怎么 为什么 可以 我们 你们
他们 一个 一些 这样 那样 时候 事情 问题 东西 非常 已经 还是 只是 就是 不是 没有 起来
the a an and or but if then of to in on at for with without is are was were be been being
it its this that these those as by from not no yes you your we our i my me they their he she
""".split())


# --------------------------------------------------------------------------- 主入口
def analyze(chunks: Sequence[Any], top_terms: int = 24) -> Dict[str, Any]:
    """对已切片的知识库做统计，返回可直接写入 persona.json 的字典。"""
    texts = [c.text if hasattr(c, "text") else str(c) for c in chunks]
    joined = "\n".join(texts)
    sentences = split_sentences(joined, min_len=6)
    sentence_lens = [len(s) for s in sentences] or [0]
    marker_counts = {
        "analogy": _count(joined, ANALOGY_MARKERS),
        "hedge": _count(joined, HEDGE_MARKERS),
        "assertive": _count(joined, ASSERTIVE_MARKERS),
        "first_person": _count(joined, FIRST_PERSON_MARKERS),
        "question": _count(joined, QUESTION_MARKERS),
    }
    n_sent = max(1, len(sentences))
    voice = {
        "avg_sentence_chars": round(sum(sentence_lens) / len(sentence_lens), 1),
        "long_sentence_ratio": round(sum(1 for n in sentence_lens if n > 60) / n_sent, 3),
        "short_sentence_ratio": round(sum(1 for n in sentence_lens if n <= 20) / n_sent, 3),
        "question_ratio": round(_occurrences(joined, QUESTION_MARKERS, per_sentence=True) / n_sent, 3),
        "exclaim_ratio": round((joined.count("！") + joined.count("!")) / n_sent, 3),
        "first_person_ratio": round(_occurrences(joined, FIRST_PERSON_MARKERS, per_sentence=True) / n_sent, 3),
        "analogy_per_kchar": _per_kchar(marker_counts["analogy"], len(joined)),
        "hedge_per_kchar": _per_kchar(marker_counts["hedge"], len(joined)),
        "assertive_per_kchar": _per_kchar(marker_counts["assertive"], len(joined)),
        "tone": _infer_tone(marker_counts, n_sent),
    }
    models = _collect_sentences(sentences, MODEL_MARKERS, limit=8)
    heuristics = _collect_sentences(sentences, DECISION_MARKERS, limit=8)
    values = _collect_sentences(sentences, VALUE_MARKERS, limit=8)
    return {
        "voice": voice,
        "signature_phrases": signature_phrases(joined, limit=14),
        "connectives": connectives(texts, limit=10),
        "keywords": top_keywords(texts, top_terms),
        "marker_counts": marker_counts,
        "mental_models": _keywords_in(models, MODEL_MARKERS, limit=10) or [k for k in top_keywords(texts, 30) if k][:6],
        "heuristics": [s for s in heuristics][:6],
        "value_sentences": [s for s in values][:6],
        "value_terms": _keywords_in(values, VALUE_MARKERS, limit=8),
        "domains": infer_domains(joined),
        "chars": len(joined),
        "sentences": len(sentences),
    }


def _count(text: str, markers: Iterable[str]) -> int:
    low = text.lower()
    return sum(low.count(m.lower()) for m in markers)


def _occurrences(text: str, markers: Iterable[str], per_sentence: bool = False) -> int:
    return _count(text, markers)


def _per_kchar(count: int, chars: int) -> float:
    if chars <= 0:
        return 0.0
    return round(count * 1000.0 / chars, 2)


def _infer_tone(markers: Dict[str, int], n_sent: int) -> List[str]:
    tone: List[str] = []
    if markers["analogy"] * 3 > n_sent:
        tone.append("善用类比与故事")
    if markers["assertive"] * 4 > n_sent:
        tone.append("结论明确、语气笃定")
    if markers["hedge"] > markers["assertive"]:
        tone.append("措辞谨慎、常留余地")
    if markers["question"] * 6 > n_sent:
        tone.append("爱用反问引导思考")
    if markers["first_person"] * 3 > n_sent:
        tone.append("第一人称、经验叙事")
    return tone or ["平实陈述"]


def _collect_sentences(sentences: Sequence[str], markers: Iterable[str], limit: int = 8) -> List[str]:
    hits: List[str] = []
    for sent in sentences:
        low = sent.lower()
        if any(m in low for m in markers):
            s = sent.strip()
            if 12 <= len(s) <= 240:
                hits.append(s)
        if len(hits) >= limit * 3:
            break
    # 去重并优先短句
    seen = set()
    out: List[str] = []
    for s in sorted(hits, key=len):
        key = re.sub(r"\s+", "", s)[:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _keywords_in(sentences: Sequence[str], markers: Iterable[str], limit: int = 8) -> List[str]:
    low = "\n".join(s.lower() for s in sentences)
    found = []
    for m in markers:
        if m in low:
            found.append(m.strip())
        if len(found) >= limit:
            break
    return found


# --------------------------------------------------------------------------- 词与短语
def top_keywords(texts: Sequence[str], limit: int = 24) -> List[Dict[str, Any]]:
    """TF-IDF 近似：跨切片计算词权重，取 top-N。"""
    docs = [tokenize(t) for t in texts]
    n_docs = max(1, len(docs))
    df: Counter = Counter()
    tf: Counter = Counter()
    for toks in docs:
        for tok in set(toks):
            df[tok] += 1
        for tok in toks:
            tf[tok] += 1
    total_tf = sum(tf.values()) or 1
    scored: List[Tuple[float, str]] = []
    for tok, count in tf.items():
        if tok in _STOP or len(tok) < 2:
            continue
        if re.fullmatch(r"[a-z]{1,3}", tok) and tok not in {"ai", "ml", "vc", "ok"}:
            continue
        idf = math.log(1.0 + n_docs / (1.0 + df[tok]))
        score = (count / total_tf) * idf * (1.0 + min(len(tok), 6) / 10.0)
        scored.append((score, tok))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out: List[Dict[str, Any]] = []
    for score, tok in scored[: limit * 3]:
        out.append({"term": tok, "score": round(score * 1000, 3), "count": tf[tok]})
        if len(out) >= limit:
            break
    return out


def signature_phrases(text: str, limit: int = 14, min_freq: int = 3) -> List[Dict[str, Any]]:
    """高频口头禅：2~5 字中文 n-gram 与 2~3 词英文 n-gram，贪心去重叠。"""
    text = text or ""
    counts: Counter = Counter()
    for m in re.finditer(r"[\u4e00-\u9fff]{6,}", text):
        run = m.group(0)
        for n in (2, 3, 4, 5):
            for i in range(len(run) - n + 1):
                gram = run[i: i + n]
                if _is_boring_gram(gram):
                    continue
                counts[gram] += 1
    words = re.findall(r"[A-Za-z][A-Za-z'\-]{2,}", text)
    for n in (2, 3):
        for i in range(len(words) - n + 1):
            gram = " ".join(w.lower() for w in words[i: i + n])
            counts[gram] += 1
    picked: List[Tuple[str, int]] = []
    for gram, freq in counts.most_common(400):
        if freq < min_freq:
            break
        if any(gram in p or p in gram for p, _ in picked):
            continue
        picked.append((gram, freq))
        if len(picked) >= limit:
            break
    return [{"phrase": g, "count": f} for g, f in picked]


def _is_boring_gram(gram: str) -> bool:
    if gram in _STOP:
        return True
    if re.search(r"(的的|了了|是是|一一|。。)", gram):
        return True
    if all(ch in "的了是和很就都也才又还" for ch in gram):
        return True
    return False


def connectives(texts: Sequence[str], limit: int = 10) -> List[str]:
    """句首连接词/口头起手式，如「所以，」「你看，」「说白了，」。"""
    counter: Counter = Counter()
    for text in texts:
        for sent in split_sentences(text, min_len=6):
            m = re.match(r"^([\u4e00-\u9fffA-Za-z]{1,6})[，,：:]", sent)
            if m:
                counter[m.group(1)] += 1
                continue
            m2 = re.match(r"^([A-Za-z]{2,12})\b[,:]", sent + ",")
            if m2:
                counter[m2.group(1).lower()] += 1
    out = [w for w, c in counter.most_common(limit * 3) if c >= 2 and len(w) >= 2][:limit]
    return out


def infer_domains(text: str, limit: int = 4) -> List[str]:
    low = (text or "").lower()
    scored: List[Tuple[int, str]] = []
    for domain, markers in DOMAIN_MARKERS.items():
        score = sum(low.count(m.lower()) for m in markers)
        if score > 0:
            scored.append((score, domain))
    scored.sort(reverse=True)
    return [d for _, d in scored[:limit]] or ["通用"]
