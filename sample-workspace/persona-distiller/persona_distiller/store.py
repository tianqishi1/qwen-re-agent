"""知识库存储层（SQLite）。

表结构
------
personas(slug PK, display_name, aliases, domain, era, language, style_hint,
         created_at, updated_at, meta)
sources (id PK, slug, kind, title, author, url, tags, sha1, chars, raw_path,
         added_at, meta)
chunks  (id PK, source_id, slug, ord, kind, heading, text, weight, added_at)

约定：``kind`` 取值 doc / book / link / experience / quote / note，
不同来源类型带不同检索权重（语录、亲身经历 > 正文 > 链接）。
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .util import KB_FILE_NAME, atomic_write_text, persona_dir, read_json, write_json

KIND_WEIGHTS: Dict[str, float] = {
    "quote": 1.60,
    "experience": 1.35,
    "book": 1.15,
    "note": 1.10,
    "doc": 1.00,
    "link": 0.95,
}
KIND_LABELS: Dict[str, str] = {
    "doc": "文档",
    "book": "书籍",
    "link": "链接",
    "experience": "经历",
    "quote": "语录",
    "note": "笔记",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS personas (
    slug TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    domain TEXT DEFAULT '',
    era TEXT DEFAULT '',
    language TEXT DEFAULT 'zh',
    style_hint TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT DEFAULT '',
    url TEXT DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    sha1 TEXT NOT NULL,
    chars INTEGER NOT NULL DEFAULT 0,
    raw_path TEXT DEFAULT '',
    added_at TEXT NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sources_slug ON sources(slug);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sources_slug_sha ON sources(slug, sha1);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    ord INTEGER NOT NULL,
    kind TEXT NOT NULL,
    heading TEXT DEFAULT '',
    text TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    added_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source_id);
CREATE INDEX IF NOT EXISTS idx_chunks_slug ON chunks(slug);
"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class Chunk:
    """检索与蒸馏的最小单元。"""

    id: int
    source_id: int
    kind: str
    heading: str
    text: str
    weight: float = 1.0
    ord: int = 0
    title: str = ""
    author: str = ""
    url: str = ""
    tags: List[str] = field(default_factory=list)

    @property
    def locator(self) -> str:
        bits = [b for b in (self.title, self.heading) if b]
        return " · ".join(bits) if bits else (self.title or "未命名来源")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "kind": self.kind,
            "heading": self.heading,
            "text": self.text,
            "weight": self.weight,
            "locator": self.locator,
            "url": self.url,
        }


@dataclass
class Source:
    id: int
    kind: str
    title: str
    author: str
    url: str
    tags: List[str]
    sha1: str
    chars: int
    added_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "author": self.author,
            "url": self.url, "tags": self.tags, "sha1": self.sha1, "chars": self.chars,
            "added_at": self.added_at, "kind_label": KIND_LABELS.get(self.kind, self.kind),
        }


class KBError(RuntimeError):
    pass


class KB:
    """单个 persona 的知识库。"""

    def __init__(self, root: Path, slug: str, create: bool = False) -> None:
        self.root = Path(root)
        self.slug = slug
        self.dir = persona_dir(self.root, slug)
        if not create and not (self.dir / KB_FILE_NAME).exists():
            raise KBError(
                "persona '%s' 不存在（%s 未找到）。先执行：pd new %s --name \"...\""
                % (slug, self.dir / KB_FILE_NAME, slug)
            )
        if create:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "raw").mkdir(exist_ok=True)
        self.db_path = self.dir / KB_FILE_NAME
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------- 生命周期
    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "KB":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------- persona
    def upsert_persona(
        self,
        display_name: str,
        aliases: Optional[Sequence[str]] = None,
        domain: str = "",
        era: str = "",
        language: str = "zh",
        style_hint: str = "",
        meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        ts = now_iso()
        row = self.get_persona()
        if row:
            merged_aliases = sorted({*(row.get("aliases") or []), *(aliases or [])})
            self.conn.execute(
                "UPDATE personas SET display_name=?, aliases=?, domain=?, era=?, language=?,"
                " style_hint=?, updated_at=?, meta=? WHERE slug=?",
                (
                    display_name or row["display_name"],
                    json.dumps(merged_aliases, ensure_ascii=False),
                    domain or row.get("domain", ""),
                    era or row.get("era", ""),
                    language or row.get("language", "zh"),
                    style_hint or row.get("style_hint", ""),
                    ts,
                    json.dumps({**(row.get("meta") or {}), **(meta or {})}, ensure_ascii=False),
                    self.slug,
                ),
            )
        else:
            self.conn.execute(
                "INSERT INTO personas (slug, display_name, aliases, domain, era, language,"
                " style_hint, created_at, updated_at, meta)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    self.slug, display_name or self.slug, json.dumps(list(aliases or []), ensure_ascii=False),
                    domain, era, language or "zh", style_hint, ts, ts,
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
        self.conn.commit()
        out = self.get_persona() or {}
        write_json(self.dir / "profile.json", out)
        return out

    def get_persona(self) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM personas WHERE slug=?", (self.slug,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["aliases"] = json.loads(data.get("aliases") or "[]")
        data["meta"] = json.loads(data.get("meta") or "{}")
        return data

    # ------------------------------------------------------------- sources
    def add_source(
        self,
        kind: str,
        title: str,
        text: str,
        author: str = "",
        url: str = "",
        tags: Optional[Sequence[str]] = None,
        digest: str = "",
        meta: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Optional[int]:
        """写入来源文本；内容重复（sha1 相同）时返回 None（除非 force）。"""
        digest = digest or _sha1(text)
        if not force:
            hit = self.conn.execute(
                "SELECT id FROM sources WHERE slug=? AND sha1=?", (self.slug, digest)
            ).fetchone()
            if hit:
                return None
        raw_rel = ""
        try:
            raw_dir = self.dir / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            raw_name = "%s-%s.txt" % (digest[:10], kind)
            atomic_write_text(raw_dir / raw_name, text)
            raw_rel = "raw/" + raw_name
        except OSError:
            raw_rel = ""
        cur = self.conn.execute(
            "INSERT INTO sources (slug, kind, title, author, url, tags, sha1, chars,"
            " raw_path, added_at, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.slug, kind, title or "(未命名)", author, url,
                json.dumps(list(tags or []), ensure_ascii=False), digest, len(text),
                raw_rel, now_iso(), json.dumps(meta or {}, ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def add_chunks(self, source_id: int, chunks: Iterable[Dict[str, str]]) -> int:
        rows = []
        for i, ch in enumerate(chunks):
            kind = ch.get("kind") or "doc"
            weight = float(ch.get("weight") or KIND_WEIGHTS.get(kind, 1.0))
            rows.append(
                (source_id, self.slug, int(ch.get("ord", i)), kind, ch.get("heading", ""),
                 ch.get("text", ""), weight, now_iso())
            )
        if not rows:
            return 0
        self.conn.executemany(
            "INSERT INTO chunks (source_id, slug, ord, kind, heading, text, weight, added_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def sources(self, kind: Optional[str] = None) -> List[Source]:
        sql = "SELECT * FROM sources WHERE slug=?"
        args: List[Any] = [self.slug]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        sql += " ORDER BY id"
        out = []
        for row in self.conn.execute(sql, args):
            out.append(Source(
                id=row["id"], kind=row["kind"], title=row["title"], author=row["author"] or "",
                url=row["url"] or "", tags=json.loads(row["tags"] or "[]"), sha1=row["sha1"],
                chars=row["chars"], added_at=row["added_at"],
            ))
        return out

    def delete_source(self, source_id: int) -> int:
        cur = self.conn.execute("DELETE FROM chunks WHERE source_id=? AND slug=?", (source_id, self.slug))
        removed = cur.rowcount or 0
        self.conn.execute("DELETE FROM sources WHERE id=? AND slug=?", (source_id, self.slug))
        self.conn.commit()
        return removed

    # ------------------------------------------------------------- chunks
    def chunks(self, kind: Optional[str] = None, limit: Optional[int] = None) -> List[Chunk]:
        sql = (
            "SELECT c.*, s.title, s.author, s.url, s.tags FROM chunks c"
            " JOIN sources s ON s.id = c.source_id WHERE c.slug=?"
        )
        args: List[Any] = [self.slug]
        if kind:
            sql += " AND c.kind=?"
            args.append(kind)
        sql += " ORDER BY c.source_id, c.ord, c.id"
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        out = []
        for row in self.conn.execute(sql, args):
            out.append(Chunk(
                id=row["id"], source_id=row["source_id"], kind=row["kind"],
                heading=row["heading"] or "", text=row["text"], weight=row["weight"],
                ord=row["ord"], title=row["title"], author=row["author"] or "",
                url=row["url"] or "", tags=json.loads(row["tags"] or "[]"),
            ))
        return out

    def stats(self) -> Dict[str, Any]:
        total_chunks = self.conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE slug=?", (self.slug,)
        ).fetchone()[0]
        total_chars = self.conn.execute(
            "SELECT COALESCE(SUM(LENGTH(text)),0) FROM chunks WHERE slug=?", (self.slug,)
        ).fetchone()[0]
        by_kind: Dict[str, int] = {}
        for row in self.conn.execute(
            "SELECT kind, COUNT(*) c FROM chunks WHERE slug=? GROUP BY kind", (self.slug,)
        ):
            by_kind[row["kind"]] = row["c"]
        return {
            "slug": self.slug,
            "sources": len(self.sources()),
            "chunks": total_chunks,
            "chars": total_chars,
            "by_kind": by_kind,
            "db": str(self.db_path),
        }

    # ------------------------------------------------------------- 导出
    def export_chunks(self, path: Path) -> int:
        from .util import write_jsonl

        rows = []
        for ch in self.chunks():
            d = ch.to_dict()
            d["weight"] = ch.weight
            rows.append(d)
        path.parent.mkdir(parents=True, exist_ok=True)
        return write_jsonl(path, rows)


def _sha1(text: str) -> str:
    from .util import sha1

    return sha1(text)


def list_personas(root: Path) -> List[Dict[str, Any]]:
    """列出工作区内所有 persona 及其统计信息。"""
    base = persona_dir(Path(root), "_").parent
    out: List[Dict[str, Any]] = []
    if not base.exists():
        return out
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        db = d / KB_FILE_NAME
        profile = read_json(d / "profile.json", {}) or {}
        entry: Dict[str, Any] = {
            "slug": d.name,
            "display_name": profile.get("display_name", d.name),
            "domain": profile.get("domain", ""),
            "created_at": profile.get("created_at", ""),
            "updated_at": profile.get("updated_at", ""),
            "sources": 0,
            "chunks": 0,
            "chars": 0,
            "has_skill": False,
        }
        if db.exists():
            try:
                conn = sqlite3.connect(str(db))
                conn.row_factory = sqlite3.Row
                entry["sources"] = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
                entry["chunks"] = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
                entry["chars"] = conn.execute(
                    "SELECT COALESCE(SUM(LENGTH(text)),0) FROM chunks"
                ).fetchone()[0]
                conn.close()
            except sqlite3.Error:
                pass
        out.append(entry)
    return out
