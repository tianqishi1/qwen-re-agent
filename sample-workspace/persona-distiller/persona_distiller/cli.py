"""命令行入口：``pd <子命令>``。

子命令
------
new      创建 persona
add      导入资料（文件 / 目录 / URL / 经历 / 语录）
list     列出所有 persona
show     查看 persona 详情与蒸馏状态
stats    语料统计
search   只看检索证据
distill  蒸馏成 Skill 包
ask      单轮提问（interview / delegate / critique / teach）
chat     多轮对话
export   导出 Skill 包为 zip
rm       删除 persona
doctor   环境与 LLM 自检
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .agent import PersonaRuntime
from .config import load_config
from .distill import (
    MODE_SPECS,
    distill,
    import_docs_to_kb,
    is_stale,
    load_skill,
    normalize_mode,
)
from .extract import ExtractError, extract_target, extract_url, Doc
from .llm import LLM, LLMError
from .store import KIND_LABELS, KB, KBError, list_personas
from .util import (
    eprint,
    find_root,
    pad,
    persona_dir,
    setup_console,
    skills_dir,
    slugify,
    strip_control,
    truncate,
)

EXIT_OK = 0
EXIT_BIZ = 1
EXIT_USAGE = 2
EXIT_DATA = 3


# --------------------------------------------------------------------------- 通用
def out_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def emit(args: argparse.Namespace, data: Dict[str, Any], human: str) -> int:
    if getattr(args, "json", False):
        out_json(data)
    else:
        print(strip_control(human))
    return EXIT_OK


def get_root(args: argparse.Namespace) -> Path:
    return Path(args.root).expanduser().resolve() if args.root else find_root()


def build_llm(args: argparse.Namespace, root: Path) -> LLM:
    cfg = load_config(
        root,
        {
            "api_key": getattr(args, "api_key", None),
            "base_url": getattr(args, "base_url", None),
            "model": getattr(args, "model", None),
        },
    )
    return LLM(cfg.llm)


def want_llm(args: argparse.Namespace) -> bool:
    if getattr(args, "offline", False):
        return False
    return True


# --------------------------------------------------------------------------- new
def cmd_new(args: argparse.Namespace) -> int:
    root = get_root(args)
    slug = args.slug or slugify(args.name or "")
    if not slug:
        eprint("错误：请提供 persona 名称（--name）或 slug")
        return EXIT_USAGE
    display = args.name or slug
    try:
        with KB(root, slug, create=True) as kb:
            profile = kb.upsert_persona(
                display_name=display,
                aliases=args.alias or [],
                domain=args.domain or "",
                era=args.era or "",
                language=args.lang or "zh",
                style_hint=args.style_hint or "",
                meta={"persona_type": args.persona_type or ""},
            )
    except KBError as exc:
        eprint("错误：%s" % exc)
        return EXIT_BIZ
    human = (
        "已创建 persona：%s（slug=%s）\n"
        "  资料目录：%s\n"
        "  下一步：pd add %s <文件|目录|URL> [--type book|doc|link] 或 --note/--quote 直接录入经历与语录"
        % (display, slug, persona_dir(root, slug), slug)
    )
    return emit(args, {"slug": slug, "profile": profile, "dir": str(persona_dir(root, slug))}, human)


# --------------------------------------------------------------------------- add
def cmd_add(args: argparse.Namespace) -> int:
    root = get_root(args)
    slug = args.slug
    kind = args.type or None
    if kind and kind not in KIND_LABELS:
        eprint("错误：--type 只能是 %s" % "/".join(sorted(KIND_LABELS)))
        return EXIT_USAGE
    if not (args.targets or args.note or args.quote or args.url):
        eprint("错误：没有要导入的内容。用法：pd add <persona> <文件|目录|URL> "
               "或 pd add <persona> --note \"...\" / --quote \"...\"")
        return EXIT_USAGE
    try:
        kb = KB(root, slug)
    except KBError as exc:
        eprint("错误：%s" % exc)
        return EXIT_BIZ

    summary: Dict[str, Any] = {"slug": slug, "sources": 0, "skipped": 0, "chunks": 0, "items": []}
    try:
        if not kb.get_persona():
            kb.upsert_persona(display_name=slug)

        # 1) 文件 / 目录
        targets: List[str] = list(args.targets or []) + list(args.url or [])
        for target in targets:
            try:
                docs = extract_target(target, kind=kind, author=args.author or "")
            except ExtractError as exc:
                eprint("错误：%s" % exc)
                continue
            if args.title and len(docs) == 1:
                docs[0].title = args.title
            stats = import_docs_to_kb(kb, docs, kind=kind or "doc", tags=args.tag or [],
                                     force=args.force, log=lambda m: None if args.json else print(m))
            _accum(summary, stats)
            for d in docs:
                summary["items"].append({"title": d.title, "kind": d.kind, "chars": len(d.text)})

        # 2) 经历
        for note in args.note or []:
            title = "经历：" + truncate(note, 24)
            if args.title:
                title = args.title
            doc = Doc(title=title, text=note, kind="experience", author=args.author or "",
                      meta={"persona_note": True})
            stats = import_docs_to_kb(kb, [doc], kind="experience", tags=args.tag or [],
                                      force=args.force, log=lambda m: print(m) if not args.json else None)
            _accum(summary, stats)

        # 3) 语录
        for quote in args.quote or []:
            text = quote if args.title is None else "%s\n—— %s" % (quote, args.title)
            doc = Doc(title="语录", text=text, kind="quote", author=args.author or "",
                      meta={"persona_quote": True})
            stats = import_docs_to_kb(kb, [doc], kind="quote", tags=args.tag or [],
                                      force=args.force, log=lambda m: print(m) if not args.json else None)
            _accum(summary, stats)

        stats_now = kb.stats()
    finally:
        kb.close()

    human = (
        "导入完成：新增 %d 个来源 / %d 个证据块，跳过 %d 个（重复或过短）。\n"
        "  当前语料：%d 来源 / %d 证据块 / %d 字\n"
        "  下一步：pd distill %s（生成人格 Skill）"
        % (summary["sources"], summary["chunks"], summary["skipped"],
           stats_now["sources"], stats_now["chunks"], stats_now["chars"], slug)
    )
    summary["stats"] = stats_now
    return emit(args, summary, human)


def _accum(summary: Dict[str, Any], stats: Dict[str, int]) -> None:
    summary["sources"] += stats.get("sources", 0)
    summary["skipped"] += stats.get("skipped", 0)
    summary["chunks"] += stats.get("chunks", 0)


# --------------------------------------------------------------------------- list / show / stats
def cmd_list(args: argparse.Namespace) -> int:
    root = get_root(args)
    items = list_personas(root)
    for it in items:
        skill_md = skills_dir(root, args.out) / it["slug"] / "SKILL.md"
        it["has_skill"] = skill_md.exists()
    if args.json:
        return emit(args, {"count": len(items), "personas": items}, "")
    if not items:
        print("还没有任何 persona。先执行：pd new 费曼 --name \"费曼\" --domain \"物理\"")
        return EXIT_OK
    rows = [("SLUG", "名称", "领域", "来源", "证据块", "字数", "Skill")]
    for it in items:
        rows.append((
            it["slug"], it["display_name"], it["domain"] or "-", str(it["sources"]),
            str(it["chunks"]), str(it["chars"]), "✓" if it["has_skill"] else "-",
        ))
    print(_table(rows))
    return EXIT_OK


def _table(rows: Sequence[Sequence[str]]) -> str:
    widths = [max(_w(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for idx, row in enumerate(rows):
        lines.append("  ".join(pad(str(cell), widths[i]) for i, cell in enumerate(row)).rstrip())
        if idx == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def _w(text: str) -> int:
    from .util import display_width

    return display_width(str(text))


def cmd_show(args: argparse.Namespace) -> int:
    root = get_root(args)
    slug = args.slug
    try:
        kb = KB(root, slug)
    except KBError as exc:
        eprint("错误：%s" % exc)
        return EXIT_BIZ
    try:
        profile = kb.get_persona() or {}
        stats = kb.stats()
        sources = [s.to_dict() for s in kb.sources()]
    finally:
        kb.close()
    out_dir = skills_dir(root, args.out) / slug
    skill_md = out_dir / "SKILL.md"
    meta = {}
    stale = False
    if (out_dir / "meta.json").exists():
        meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
        try:
            with KB(root, slug) as kb2:
                stale = is_stale(kb2, meta)
        except KBError:
            stale = False

    payload = {
        "profile": profile,
        "stats": stats,
        "sources": sources,
        "skill": {
            "path": str(skill_md) if skill_md.exists() else None,
            "built_at": meta.get("built_at"),
            "llm": meta.get("llm"),
            "stale": stale,
        },
    }
    if args.json:
        return emit(args, payload, "")

    lines = ["# %s（%s）" % (profile.get("display_name", slug), slug)]
    if profile.get("domain"):
        lines.append("领域：%s%s" % (profile["domain"], " · 时期：%s" % profile["era"] if profile.get("era") else ""))
    if profile.get("style_hint"):
        lines.append("风格提示：%s" % profile["style_hint"])
    lines.append("语料：%d 来源 / %d 证据块 / %d 字" % (stats["sources"], stats["chunks"], stats["chars"]))
    if stats["by_kind"]:
        lines.append("分布：" + "、".join(
            "%s×%d" % (KIND_LABELS.get(k, k), v) for k, v in sorted(stats["by_kind"].items())
        ))
    lines.append("")
    lines.append("来源清单：")
    for s in sources:
        lines.append("  [%d] %-4s %s（%d 字）%s" % (
            s["id"], KIND_LABELS.get(s["kind"], s["kind"]), s["title"], s["chars"],
            "  " + s["url"] if s["url"] else "",
        ))
    lines.append("")
    if skill_md.exists():
        lines.append("Skill：%s（%s 生成，%s）" % (skill_md, meta.get("built_at", "?"), meta.get("llm", "?")))
        if stale:
            lines.append("  ⚠ 知识库在蒸馏后有变化，建议重跑：pd distill %s" % slug)
    else:
        lines.append("Skill：尚未生成 → pd distill %s" % slug)
    return emit(args, payload, "\n".join(lines))


def cmd_stats(args: argparse.Namespace) -> int:
    root = get_root(args)
    with KB(root, args.slug) as kb:
        stats = kb.stats()
    if args.json:
        return emit(args, stats, "")
    human = "语料统计（%s）\n  来源：%d\n  证据块：%d\n  字数：%d\n  分布：%s\n  数据库：%s" % (
        args.slug, stats["sources"], stats["chunks"], stats["chars"],
        "、".join("%s×%d" % (KIND_LABELS.get(k, k), v) for k, v in sorted(stats["by_kind"].items())) or "-",
        stats["db"],
    )
    return emit(args, stats, human)


# --------------------------------------------------------------------------- search
def cmd_search(args: argparse.Namespace) -> int:
    root = get_root(args)
    with PersonaRuntime(root, args.slug, args.out) as rt:
        hits = rt.search(args.query, k=args.k, kinds=[args.kind] if args.kind else None)
        payload = {
            "query": args.query,
            "count": len(hits),
            "results": [h.to_dict() for h in hits],
        }
    if args.json:
        return emit(args, payload, "")
    if not hits:
        print("没有检索到相关内容。该问题可能超出这份语料的覆盖范围。")
        return EXIT_OK
    lines = ["检索：%s（%d 条）" % (args.query, len(hits))]
    for i, h in enumerate(hits, 1):
        ch = h.chunk
        lines.append("")
        lines.append("%d. [%s %.2f] %s" % (i, KIND_LABELS.get(ch.kind, ch.kind), h.score, ch.locator))
        if ch.url:
            lines.append("   %s" % ch.url)
        lines.append("   " + strip_control(ch.text[:400]).replace("\n", "\n   "))
    print("\n".join(lines))
    return EXIT_OK


# --------------------------------------------------------------------------- distill
def cmd_distill(args: argparse.Namespace) -> int:
    root = get_root(args)
    llm = build_llm(args, root)
    use_llm = args.llm and not args.offline
    try:
        result = distill(
            root, args.slug, out=args.out, use_llm=use_llm, force=args.force,
            k_quotes=args.k_quotes, llm=llm,
            log=(lambda m: None) if args.json else print,
        )
    except (KBError, RuntimeError) as exc:
        eprint("错误：%s" % exc)
        return EXIT_BIZ
    card = result["card"]
    human = (
        "蒸馏完成：%s\n"
        "  人设：%s\n"
        "  语料：%d 来源 / %d 证据块 / %d 字\n"
        "  蒸馏方式：%s\n"
        "  产物：SKILL.md / playbook.md / persona.json / evidence/ / prompts/\n\n"
        "  试一句：pd ask %s \"%s\" --mode delegate"
        % (result["out_dir"], card["one_line"], card["stats"]["sources"], card["stats"]["chunks"],
           card["stats"]["chars"], card.get("distilled_by"), args.slug,
           truncate(card["quotes"][0]["text"], 20) if card.get("quotes") else "我该怎么用你")
    )
    for w in result["warnings"]:
        human += "\n  ⚠ %s" % w
    payload = {
        "out_dir": result["out_dir"],
        "persona": {k: card[k] for k in ("slug", "display_name", "one_line", "domain", "distilled_by")},
        "stats": card["stats"],
        "warnings": result["warnings"],
        "meta": result["meta"],
    }
    return emit(args, payload, human)


# --------------------------------------------------------------------------- ask / chat
def _make_runtime(args: argparse.Namespace) -> PersonaRuntime:
    root = get_root(args)
    llm = build_llm(args, root)
    return PersonaRuntime(root, args.slug, args.out, llm=llm)


def cmd_ask(args: argparse.Namespace) -> int:
    task = " ".join(args.task).strip()
    if not task:
        eprint("错误：请给出要问的问题。例如：pd ask 费曼 \"为什么不建议过早优化\"")
        return EXIT_USAGE
    try:
        with _make_runtime(args) as rt:
            result = rt.answer(
                task, mode=args.mode, use_llm=want_llm(args), k=args.k,
                show_prompt=args.show_prompt,
            )
    except (KBError, RuntimeError) as exc:
        eprint("错误：%s" % exc)
        return EXIT_BIZ

    if args.json:
        return emit(args, result, "")
    print(strip_control(result["answer"]))
    for w in result["warnings"]:
        eprint("⚠ %s" % w)
    if args.evidence and result["model"] != "offline":
        print("\n" + "-" * 60)
        print("证据（前 %d 条）：" % len(result["evidence"]))
        for i, e in enumerate(result["evidence"], 1):
            print("%d. [%s] %s: %s" % (i, e["kind"], e["locator"], truncate(e["text"], 160)))
    if args.show_prompt:
        print("\n" + "=" * 60)
        print("本次实际发送的提示词：")
        for msg in result.get("prompt", []):
            print("\n[%s]\n%s" % (msg["role"], msg["content"]))
    return EXIT_OK


CHAT_HELP = """可用命令：
  /mode interview|delegate|critique|teach   切换模式（当前模式会显示在提示符里）
  /k N                                     调整检索证据条数（默认 8）
  /evidence                                显示最近一次回答用到的证据
  /prompt                                  显示最近一次实际发送的提示词
  /save FILE                               把整段对话存成 markdown
  /help  /exit"""


def cmd_chat(args: argparse.Namespace) -> int:
    try:
        rt = _make_runtime(args)
    except (KBError, RuntimeError) as exc:
        eprint("错误：%s" % exc)
        return EXIT_BIZ
    mode = normalize_mode(args.mode)
    k = args.k
    history: List[Any] = []
    log: List[Any] = []
    last_result: Optional[Dict[str, Any]] = None
    name = rt.card["display_name"]
    print("=" * 66)
    print("与「%s」对话中（%s 模式）。输入 /help 看命令，/exit 退出。" % (name, MODE_SPECS[mode]["name"]))
    print("提示：这是基于语料蒸馏的人格模拟体，不是本人。")
    print("=" * 66)
    for w in rt.warnings:
        eprint("⚠ %s" % w)
    try:
        while True:
            try:
                line = input("\n[%s|%s] 你> " % (name, mode)).strip()
            except EOFError:
                print()
                break
            if not line:
                continue
            if line.startswith("/"):
                cmd, _, rest = line[1:].partition(" ")
                cmd = cmd.lower().strip()
                rest = rest.strip()
                if cmd in ("exit", "quit", "q"):
                    break
                if cmd == "help":
                    print(CHAT_HELP)
                elif cmd == "mode":
                    mode = normalize_mode(rest)
                    print("已切换到 %s 模式。" % MODE_SPECS[mode]["name"])
                elif cmd == "k":
                    try:
                        k = max(1, min(30, int(rest)))
                        print("证据条数 = %d" % k)
                    except ValueError:
                        print("用法：/k 8")
                elif cmd == "evidence":
                    if not last_result:
                        print("还没有回答。")
                    else:
                        for i, e in enumerate(last_result["evidence"], 1):
                            print("%d. [%s] %s: %s" % (i, e["kind"], e["locator"], truncate(e["text"], 200)))
                elif cmd == "prompt":
                    if not last_result or "prompt" not in last_result:
                        print("先执行 /prompt 前需要一次提问（本命令只显示上一次的提示词）。")
                    else:
                        for msg in last_result["prompt"]:
                            print("\n[%s]\n%s" % (msg["role"], msg["content"]))
                elif cmd == "save":
                    path = Path(rest or ("%s-chat.md" % rt.slug))
                    text = ["# 与 %s 的对话\n" % name]
                    for role, content in log:
                        text.append("## %s\n\n%s\n" % ("我问" if role == "user" else name, content))
                    path.write_text("\n".join(text), encoding="utf-8")
                    print("已保存：%s" % path)
                else:
                    print("未知命令。\n" + CHAT_HELP)
                continue

            result = rt.answer(line, mode=mode, use_llm=want_llm(args), k=k,
                               history=[(r, c) for r, c in history], show_prompt=True)
            last_result = result
            print("\n%s> %s" % (name, strip_control(result["answer"])))
            for w in result["warnings"]:
                eprint("⚠ %s" % w)
            history.append(("user", line))
            history.append(("assistant", result["answer"]))
            log.append(("user", line))
            log.append(("assistant", result["answer"]))
    except KeyboardInterrupt:
        print("\n（已中断）")
    finally:
        rt.close()
    return EXIT_OK


# --------------------------------------------------------------------------- export / rm / doctor
def cmd_export(args: argparse.Namespace) -> int:
    root = get_root(args)
    src = skills_dir(root, args.out) / args.slug
    if not (src / "SKILL.md").exists():
        eprint("错误：尚未蒸馏 → pd distill %s" % args.slug)
        return EXIT_BIZ
    target = Path(args.output) if args.output else Path("%s-skill.zip" % args.slug)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            if path.is_file():
                zf.write(path, arcname="%s/%s" % (args.slug, path.relative_to(src).as_posix()))
    size = target.stat().st_size
    return emit(args, {"output": str(target), "bytes": size, "files": _count_files(src)},
                "已导出：%s（%d 个文件，%.1f KB）" % (target, _count_files(src), size / 1024.0))


def _count_files(path: Path) -> int:
    return sum(1 for p in path.rglob("*") if p.is_file())


def cmd_rm(args: argparse.Namespace) -> int:
    root = get_root(args)
    kb_dir = persona_dir(root, args.slug)
    skill_dir = skills_dir(root, args.out) / args.slug
    targets = [p for p in (kb_dir, skill_dir) if p.exists()]
    if not targets:
        eprint("错误：未找到 persona '%s'" % args.slug)
        return EXIT_BIZ
    if not args.yes:
        print("将删除：")
        for p in targets:
            print("  %s" % p)
        try:
            answer = input("确认删除？输入 yes 继续：").strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("已取消。")
            return EXIT_OK
    for p in targets:
        shutil.rmtree(p, ignore_errors=True)
    return emit(args, {"removed": [str(p) for p in targets]}, "已删除 %d 个目录。" % len(targets))


def cmd_doctor(args: argparse.Namespace) -> int:
    root = get_root(args)
    llm = build_llm(args, root)
    info: Dict[str, Any] = {
        "tool_version": __version__,
        "python": sys.version.split()[0],
        "root": str(root),
        "tool_dir": str(root / ".persona-distiller"),
        "skills_dir": str(skills_dir(root, args.out)),
        "llm": {
            "available": llm.available,
            "model": llm.cfg.model,
            "base_url": llm.cfg.base_url,
            "key_source": llm.cfg.source,
            "key_set": bool(llm.cfg.api_key),
        },
        "personas": len(list_personas(root)),
    }
    if args.ping and llm.available:
        try:
            reply = llm.chat([{"role": "user", "content": "只回复两个字：可用"}], max_tokens=16)
            info["llm"]["ping"] = "OK: " + reply.strip()[:40]
        except LLMError as exc:
            info["llm"]["ping"] = "FAIL: %s" % exc

    human = (
        "persona-distiller %s\n"
        "  Python      : %s\n"
        "  工作区      : %s\n"
        "  知识库目录  : %s\n"
        "  Skill 输出  : %s\n"
        "  已有 persona: %d\n"
        "  LLM         : %s\n"
        % (__version__, info["python"], info["root"], info["tool_dir"], info["skills_dir"],
           info["personas"], llm.cfg.describe())
    )
    if llm.available and llm.cfg.source:
        human += "  Key 来源    : %s\n" % llm.cfg.source
    if not llm.available:
        human += (
            "\n  未配置 LLM：仍可使用 pd add/distill/search，ask/chat 会走离线检索模式。\n"
            "  启用方式（任选其一）：\n"
            "    set DASHSCOPE_API_KEY=sk-xxx && set PD_MODEL=qwen-plus      （Windows）\n"
            "    export OPENAI_API_KEY=sk-xxx && export PD_MODEL=gpt-4o-mini （macOS/Linux）\n"
            "    或在 %s 写入：{\"llm\": {\"api_key\": \"...\", \"base_url\": \"...\", \"model\": \"...\"}}\n"
            % (root / ".persona-distiller" / "config.json")
        )
    if args.ping:
        human += "  LLM 连通性  : %s\n" % info["llm"].get("ping", "（未测试：未配置 LLM）")
    return emit(args, info, human)


# --------------------------------------------------------------------------- 解析
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pd",
        description="persona-distiller：把一个人的文档、书籍、链接、经历与语录，蒸馏成可直接使用的人格 Skill。",
        epilog="示例：pd new 费曼 --name 费曼 --domain 物理 && pd add 费曼 ./books --type book && "
               "pd distill 费曼 && pd ask 费曼 \"如何给新人讲清楚一个概念\" --mode delegate",
    )
    parser.add_argument("--version", action="version", version="persona-distiller %s" % __version__)
    parser.add_argument("--root", help="工作区根目录（默认自动向上查找 .persona-distiller / .git）")
    parser.add_argument("--out", help="Skill 输出目录（默认 <root>/skills）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于脚本消费）")
    sub = parser.add_subparsers(dest="command", metavar="<命令>")

    def add_llm_flags(p: argparse.ArgumentParser, default_llm: bool = False) -> None:
        p.add_argument("--llm", action="store_true", default=default_llm,
                       help="强制使用 LLM（未配置则回退离线）")
        p.add_argument("--offline", action="store_true", help="强制离线检索模式（不调用任何模型）")
        p.add_argument("--model", help="覆盖模型名")
        p.add_argument("--base-url", dest="base_url", help="覆盖 OpenAI 兼容 base_url")
        p.add_argument("--api-key", dest="api_key", help="覆盖 API Key（不建议写在命令历史里）")

    # new
    p = sub.add_parser("new", help="创建 persona")
    p.add_argument("slug", nargs="?", help="标识（默认由 --name 生成）")
    p.add_argument("--name", help="显示名，如「费曼」")
    p.add_argument("--alias", action="append", help="别名（可重复）")
    p.add_argument("--domain", help="领域，如「物理学 / 教学」")
    p.add_argument("--era", help="活跃时期，如「1918-1988」")
    p.add_argument("--lang", default="zh", help="语言（默认 zh）")
    p.add_argument("--style-hint", dest="style_hint", help="一句话风格提示")
    p.add_argument("--persona-type", dest="persona_type", help="类型：科学家/企业家/作家/军事家/政治家…")
    p.set_defaults(func=cmd_new)

    # add
    p = sub.add_parser("add", help="导入资料（文件 / 目录 / URL / 经历 / 语录）")
    p.add_argument("slug", help="persona 标识")
    p.add_argument("targets", nargs="*", help="文件、目录或 URL")
    p.add_argument("--url", action="append", help="网页链接（可重复）")
    p.add_argument("--note", action="append", help="一段经历/事件（可重复）")
    p.add_argument("--quote", action="append", help="一句原话（可重复）")
    p.add_argument("--type", choices=sorted(KIND_LABELS), help="指定类型（默认按扩展名推断）")
    p.add_argument("--tag", action="append", help="标签（可重复）")
    p.add_argument("--author", help="作者/出处")
    p.add_argument("--title", help="覆盖标题")
    p.add_argument("--force", action="store_true", help="即使内容重复也重新导入")
    p.set_defaults(func=cmd_add)

    # list
    p = sub.add_parser("list", help="列出所有 persona")
    p.set_defaults(func=cmd_list)

    # show
    p = sub.add_parser("show", help="查看 persona 详情与蒸馏状态")
    p.add_argument("slug")
    p.set_defaults(func=cmd_show)

    # stats
    p = sub.add_parser("stats", help="语料统计")
    p.add_argument("slug")
    p.set_defaults(func=cmd_stats)

    # search
    p = sub.add_parser("search", help="只查看检索证据（不调用模型）")
    p.add_argument("slug")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=8, help="返回条数（默认 8）")
    p.add_argument("--kind", choices=sorted(KIND_LABELS), help="只看某类来源")
    p.set_defaults(func=cmd_search)

    # distill
    p = sub.add_parser("distill", help="蒸馏成人格 Skill 包")
    p.add_argument("slug")
    p.add_argument("--llm", action="store_true", help="用 LLM 增强蒸馏（需配置 API Key）")
    p.add_argument("--offline", action="store_true", help="强制离线启发式蒸馏")
    p.add_argument("--force", action="store_true", help="覆盖已有产物（overrides 仍会合并）")
    p.add_argument("--k-quotes", dest="k_quotes", type=int, default=30, help="提取语录条数上限")
    p.add_argument("--model", help="覆盖模型名")
    p.add_argument("--base-url", dest="base_url")
    p.add_argument("--api-key", dest="api_key")
    p.set_defaults(func=cmd_distill)

    # ask
    p = sub.add_parser("ask", help="单轮提问")
    p.add_argument("slug")
    p.add_argument("task", nargs="+", help="你的问题或委托（建议加引号）")
    p.add_argument("--mode", default="interview",
                   choices=sorted(MODE_SPECS) + sorted(k for k in ("访谈", "委托", "挑剔", "讲解")),
                   help="interview(交流) / delegate(委托做事) / critique(挑剔) / teach(讲解)")
    p.add_argument("-k", type=int, default=8, help="检索证据条数")
    p.add_argument("--evidence", action="store_true", help="回答后附上证据清单")
    p.add_argument("--show-prompt", dest="show_prompt", action="store_true", help="打印实际发送的提示词")
    add_llm_flags(p)
    p.set_defaults(func=cmd_ask)

    # chat
    p = sub.add_parser("chat", help="多轮对话")
    p.add_argument("slug")
    p.add_argument("--mode", default="interview")
    p.add_argument("-k", type=int, default=8)
    add_llm_flags(p)
    p.set_defaults(func=cmd_chat)

    # export
    p = sub.add_parser("export", help="导出 Skill 包为 zip")
    p.add_argument("slug")
    p.add_argument("-o", "--output", help="输出 zip 路径")
    p.set_defaults(func=cmd_export)

    # rm
    p = sub.add_parser("rm", help="删除 persona（知识库 + Skill）")
    p.add_argument("slug")
    p.add_argument("--yes", action="store_true", help="跳过确认")
    p.set_defaults(func=cmd_rm)

    # doctor
    p = sub.add_parser("doctor", help="环境与 LLM 自检")
    p.add_argument("--ping", action="store_true", help="真实调用一次 LLM 验证连通性")
    p.add_argument("--model")
    p.add_argument("--base-url", dest="base_url")
    p.add_argument("--api-key", dest="api_key")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK
    try:
        return int(args.func(args) or EXIT_OK)
    except KeyboardInterrupt:
        eprint("\n已中断。")
        return 130
    except BrokenPipeError:
        return EXIT_OK
    except (KBError, RuntimeError) as exc:
        eprint("错误：%s" % exc)
        return EXIT_BIZ


if __name__ == "__main__":
    sys.exit(main())
