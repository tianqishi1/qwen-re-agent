"""文本抽取：本地文档 / 电子书 / 网页链接 → 纯文本。

支持（全部基于标准库）：
    .txt .md .markdown .rst .text   直接读取
    .csv .tsv .json .jsonl          表格/结构化数据转文本
    .html .htm                       去标签
    .docx                            zipfile + XML（w:p / w:t）
    .epub                            zipfile + container.xml + OPF spine
    .pdf                             需要可选依赖 pypdf（缺失时给出明确提示）
    目录                             递归抽取其中所有受支持的文件
    http(s):// URL                   下载后按内容类型处理
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .util import html_to_text, html_title, norm_space

TEXT_EXTS = {".txt", ".md", ".markdown", ".rst", ".text", ".log"}
DATA_EXTS = {".csv", ".tsv", ".json", ".jsonl", ".ndjson"}
HTML_EXTS = {".html", ".htm", ".xhtml"}
DOC_EXTS = {".docx", ".epub", ".pdf"}
SUPPORTED_EXTS = TEXT_EXTS | DATA_EXTS | HTML_EXTS | DOC_EXTS

USER_AGENT = "Mozilla/5.0 (compatible; persona-distiller/1.0)"
MAX_DOWNLOAD = 8 * 1024 * 1024  # 8MB


class ExtractError(RuntimeError):
    pass


@dataclass
class Doc:
    """抽取结果：一段可入库的文本。"""

    title: str
    text: str
    author: str = ""
    url: str = ""
    kind: str = "doc"
    meta: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- 单文件
def kind_for(path: Path, hint: Optional[str] = None) -> str:
    if hint:
        return hint
    ext = path.suffix.lower()
    if ext in {".epub"}:
        return "book"
    if ext in HTML_EXTS:
        return "link"
    return "doc"


def extract_file(path: Path, kind: Optional[str] = None) -> List[Doc]:
    path = Path(path)
    if not path.exists():
        raise ExtractError("文件不存在：%s" % path)
    ext = path.suffix.lower()
    k = kind_for(path, kind)
    if ext in TEXT_EXTS:
        text = _read_text(path)
        return [Doc(title=path.stem, text=norm_space(text), kind=k, meta={"path": str(path)})]
    if ext in DATA_EXTS:
        return [_extract_data(path, k)]
    if ext in HTML_EXTS:
        raw = _read_text(path)
        return [Doc(title=html_title(raw, path.stem), text=html_to_text(raw), kind=k,
                    url="file:///" + str(path).replace("\\", "/"),
                    meta={"path": str(path)})]
    if ext == ".docx":
        return [_extract_docx(path, k)]
    if ext == ".epub":
        return _extract_epub(path, k)
    if ext == ".pdf":
        return [_extract_pdf(path, k)]
    raise ExtractError(
        "不支持的文件类型：%s（支持：%s）" % (ext or path.name, ", ".join(sorted(SUPPORTED_EXTS)))
    )


def extract_target(target: str, kind: Optional[str] = None, author: str = "") -> List[Doc]:
    """抽取文件、目录或 URL。"""
    if re.match(r"^https?://", target, re.I):
        return [extract_url(target, kind=kind)]
    p = Path(target).expanduser()
    if p.is_dir():
        docs: List[Doc] = []
        for child in sorted(p.rglob("*")):
            if child.is_file() and child.suffix.lower() in SUPPORTED_EXTS and not child.name.startswith("."):
                try:
                    docs.extend(extract_file(child, kind=kind))
                except ExtractError:
                    continue
        if not docs:
            raise ExtractError("目录中没有受支持的文件：%s" % p)
        return docs
    docs = extract_file(p, kind=kind)
    for d in docs:
        d.author = d.author or author
    return docs


# --------------------------------------------------------------------------- 各格式
def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def _extract_data(path: Path, kind: str) -> Doc:
    text = _read_text(path)
    ext = path.suffix.lower()
    lines: List[str] = []
    if ext in {".json", ".jsonl", ".ndjson"}:
        if ext == ".json":
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ExtractError("JSON 解析失败：%s（%s）" % (path, exc))
            records = data if isinstance(data, list) else [data]
        else:
            records = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        for rec in records:
            lines.append(_flatten(rec))
    else:
        delim = "\t" if ext == ".tsv" else ","
        reader = csv.reader(io.StringIO(text), delimiter=delim)
        rows = list(reader)
        if rows:
            header = rows[0]
            for row in rows[1:]:
                pairs = ["%s: %s" % (h, v) for h, v in zip(header, row) if str(v).strip()]
                if pairs:
                    lines.append("；".join(pairs))
    return Doc(title=path.stem, text=norm_space("\n".join(lines)), kind=kind, meta={"path": str(path)})


def _flatten(obj: Any, prefix: str = "") -> str:
    if isinstance(obj, dict):
        parts = []
        for k, v in obj.items():
            key = "%s.%s" % (prefix, k) if prefix else str(k)
            if isinstance(v, (dict, list)):
                parts.append(_flatten(v, key))
            else:
                parts.append("%s: %s" % (key, v))
        return "；".join(p for p in parts if p)
    if isinstance(obj, list):
        return "；".join(_flatten(v, prefix) for v in obj)
    return str(obj)


def _extract_docx(path: Path, kind: str) -> Doc:
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise ExtractError("docx 解析失败：%s（%s）" % (path, exc))
    root = ET.fromstring(xml)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paras: List[str] = []
    for p in root.iter("{%s}p" % ns["w"]):
        texts = [t.text or "" for t in p.iter("{%s}t" % ns["w"])]
        line = "".join(texts).strip()
        style = p.find("w:pPr/w:pStyle", ns)
        if line:
            if style is not None and (style.get("{%s}val" % ns["w"]) or "").lower().startswith("heading"):
                paras.append("## " + line)
            else:
                paras.append(line)
    title = path.stem
    try:
        with zipfile.ZipFile(path) as zf:
            core = zf.read("docProps/core.xml").decode("utf-8", "replace")
        m = re.search(r"<dc:title>(.*?)</dc:title>", core, re.S)
        if m and m.group(1).strip():
            title = m.group(1).strip()
    except (zipfile.BadZipFile, KeyError, OSError):
        pass
    return Doc(title=title, text=norm_space("\n\n".join(paras)), kind=kind, meta={"path": str(path)})


def _extract_epub(path: Path, kind: str) -> List[Doc]:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ExtractError("epub 解析失败：%s（%s）" % (path, exc))
    with zf:
        try:
            container = zf.read("META-INF/container.xml").decode("utf-8", "replace")
        except KeyError as exc:
            raise ExtractError("epub 缺少 META-INF/container.xml：%s" % exc)
        m = re.search(r'full-path="([^"]+)"', container)
        if not m:
            raise ExtractError("epub container.xml 中没有 rootfile")
        opf_path = m.group(1)
        opf = zf.read(opf_path).decode("utf-8", "replace")
        book_title = ""
        mt = re.search(r"<dc:title[^>]*>(.*?)</dc:title>", opf, re.S | re.I)
        if mt:
            book_title = html_to_text(mt.group(1))
        author = ""
        ma = re.search(r"<dc:creator[^>]*>(.*?)</dc:creator>", opf, re.S | re.I)
        if ma:
            author = html_to_text(ma.group(1))
        base = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
        manifest = {i: h for i, h in re.findall(r'<item\b[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf)}
        manifest.update({i: h for h, i in re.findall(r'<item\b[^>]*href="([^"]+)"[^>]*id="([^"]+)"', opf)})
        spine = re.findall(r'<itemref\b[^>]*idref="([^"]+)"', opf)
        docs: List[Doc] = []
        for idx, idref in enumerate(spine):
            href = manifest.get(idref)
            if not href:
                continue
            full = "%s/%s" % (base, href) if base else href
            full = full.replace("../", "")
            try:
                html = zf.read(full).decode("utf-8", "replace")
            except KeyError:
                continue
            text = html_to_text(html)
            if len(text) < 20:
                continue
            chapter = html_title(html, "") or re.sub(r"\.x?html?$", "", href.rsplit("/", 1)[-1])
            docs.append(Doc(
                title="%s / %s" % (book_title or path.stem, chapter),
                text=text, author=author, kind=kind,
                meta={"path": str(path), "spine_index": idx, "chapter": chapter,
                      "book": book_title or path.stem},
            ))
        if not docs:
            raise ExtractError("epub 未解析出正文：%s" % path)
        return docs


def _extract_pdf(path: Path, kind: str) -> Doc:
    reader = None
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # type: ignore

            reader = PdfReader(str(path))
        except ImportError:
            raise ExtractError(
                "PDF 需要可选依赖：pip install pypdf（或先用 pdftotext/在线工具转成 .txt 再导入）"
            )
    pages: List[str] = []
    for i, page in enumerate(reader.pages):  # type: ignore[union-attr]
        try:
            txt = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - 单页失败不应中断整体
            txt = ""
        if txt.strip():
            pages.append("## Page %d\n%s" % (i + 1, txt.strip()))
    if not pages:
        raise ExtractError("PDF 未提取到文本（可能是扫描件，请先 OCR）：%s" % path)
    meta = {}
    try:
        info = getattr(reader, "metadata", None) or {}
        meta = {"pdf_title": str(info.get("/Title", "") or ""), "path": str(path)}
    except Exception:  # noqa: BLE001
        meta = {"path": str(path)}
    title = (meta.get("pdf_title") or path.stem).strip()
    return Doc(title=title, text=norm_space("\n\n".join(pages)), kind=kind, meta=meta)


# --------------------------------------------------------------------------- 网络
def fetch_url(url: str, timeout: int = 30) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 用户显式指定 URL
            raw = resp.read(MAX_DOWNLOAD + 1)
            if len(raw) > MAX_DOWNLOAD:
                raw = raw[:MAX_DOWNLOAD]
            ctype = (resp.headers.get("Content-Type") or "").lower()
            return {"url": resp.geturl(), "content_type": ctype, "bytes": raw}
    except urllib.error.HTTPError as exc:
        raise ExtractError("下载失败 HTTP %s：%s" % (exc.code, url))
    except urllib.error.URLError as exc:
        raise ExtractError("下载失败（网络不可达？）：%s（%s）" % (url, exc.reason))
    except TimeoutError:
        raise ExtractError("下载超时：%s" % url)


def extract_url(url: str, kind: Optional[str] = None, timeout: int = 30) -> Doc:
    info = fetch_url(url, timeout=timeout)
    raw: bytes = info["bytes"]
    ctype: str = info["content_type"] or mimetypes.guess_type(url)[0] or ""
    text = _decode(raw, ctype)
    if "html" in ctype or re.search(r"(?i)<(html|body|article)\b", text[:3000]):
        return Doc(
            title=html_title(text, url.rstrip("/").rsplit("/", 1)[-1] or url),
            text=html_to_text(text),
            url=info["url"],
            kind=kind or "link",
            meta={"content_type": ctype, "source_url": url},
        )
    return Doc(
        title=url.rstrip("/").rsplit("/", 1)[-1] or url,
        text=norm_space(text),
        url=info["url"],
        kind=kind or "link",
        meta={"content_type": ctype, "source_url": url},
    )


def _decode(raw: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w\-]+)", ctype or "", re.I)
    candidates = []
    if m:
        candidates.append(m.group(1))
    head = raw[:4096].decode("latin-1", "ignore")
    m2 = re.search(r'charset=["\']?([\w\-]+)', head, re.I)
    if m2:
        candidates.append(m2.group(1))
    candidates += ["utf-8", "gb18030", "latin-1"]
    for enc in candidates:
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")
