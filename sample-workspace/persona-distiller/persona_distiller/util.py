"""通用工具：路径解析、JSON 读写、原子写、文本清洗、HTML 转文本。

本模块不依赖任何第三方库。
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable, List, Optional

TOOL_DIR_NAME = ".persona-distiller"
SKILLS_DIR_NAME = "skills"
KB_FILE_NAME = "kb.sqlite"

MARKERS = (TOOL_DIR_NAME, ".git", "pyproject.toml")


# --------------------------------------------------------------------------- 路径
def find_root(start: Optional[Path] = None) -> Path:
    """向上查找工作区根目录。

    优先使用环境变量 ``PD_ROOT``；否则从 ``start``（默认当前目录）向上寻找
    含 ``.persona-distiller`` / ``.git`` / ``pyproject.toml`` 的目录；找不到则用当前目录。
    """
    env = os.environ.get("PD_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    cur = Path(start or Path.cwd()).resolve()
    for cand in (cur, *cur.parents):
        if any((cand / m).exists() for m in MARKERS):
            return cand
    return cur


def tool_dir(root: Path) -> Path:
    """知识库与配置的存放目录：``<root>/.persona-distiller``。"""
    return Path(root) / TOOL_DIR_NAME


def persona_dir(root: Path, slug: str) -> Path:
    return tool_dir(root) / "personas" / slug


def skills_dir(root: Path, out: Optional[str] = None) -> Path:
    return Path(out).resolve() if out else Path(root) / SKILLS_DIR_NAME


# --------------------------------------------------------------------------- 文本
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control(text: str) -> str:
    """去掉 ANSI 转义序列与控制字符，避免终端注入。"""
    text = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", text)
    return _CONTROL_RE.sub("", text)


def norm_space(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def slugify(name: str) -> str:
    """把名字转成可用作目录名的 slug（保留中文、日文假名）。"""
    s = unicodedata.normalize("NFKC", name or "").strip().lower()
    s = re.sub(r"[\s_/\\|]+", "-", s)
    s = re.sub(r"[^0-9a-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af\-]+", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "persona-" + hashlib.sha1((name or "x").encode("utf-8")).hexdigest()[:8]
    return s[:64]


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


def display_width(text: str) -> int:
    """终端显示宽度（东亚宽字符按 2 计），用于表格对齐。"""
    width = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(text: str, width: int) -> str:
    gap = max(0, width - display_width(text))
    return text + " " * gap


def truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


# --------------------------------------------------------------------------- HTML
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>")
_BR_RE = re.compile(r"(?i)<\s*(br|/p|/div|/li|/h[1-6]|/tr)\s*/?\s*>")
_TAG_RE = re.compile(r"(?s)<[^>]+>")
_WS_RE = re.compile(r"[ \t\f\v]+")
_BLOCK_SEL_RE = re.compile(r"(?is)<(nav|footer|aside|form)\b.*?</\1>")


def html_to_text(raw: str) -> str:
    """把 HTML 粗粒度转成可读文本（仅标准库，不追求完美还原）。"""
    raw = _SCRIPT_RE.sub(" ", raw)
    raw = _BLOCK_SEL_RE.sub(" ", raw)
    raw = _BR_RE.sub("\n", raw)
    raw = _TAG_RE.sub(" ", raw)
    raw = _html.unescape(raw)
    raw = _WS_RE.sub(" ", raw)
    lines = [ln.strip() for ln in raw.split("\n")]
    return norm_space("\n".join(ln for ln in lines if ln))


def html_title(raw: str, fallback: str = "") -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    if m:
        t = _html.unescape(_TAG_RE.sub("", m.group(1))).strip()
        if t:
            return norm_space(t)
    return fallback


# --------------------------------------------------------------------------- JSON / IO
def read_json(path: Path, default: Any = None) -> Any:
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return default


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)


def write_json(path: Path, data: Any) -> None:
    atomic_write_text(Path(path), dumps(data) + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + fsync + 原子替换，避免写坏文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_jsonl(path: Path, rows: Iterable[Any]) -> int:
    lines: List[str] = []
    count = 0
    for row in rows:
        lines.append(json.dumps(row, ensure_ascii=False))
        count += 1
    atomic_write_text(Path(path), "\n".join(lines) + ("\n" if lines else ""))
    return count


def read_jsonl(path: Path) -> List[Any]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


# --------------------------------------------------------------------------- 控制台
def setup_console() -> None:
    """Windows GBK 终端下避免 UnicodeEncodeError（降级为替换字符）。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError, OSError):
            pass


def eprint(*args: Any, **kwargs: Any) -> None:
    kwargs.setdefault("file", sys.stderr)
    print(*args, **kwargs)
