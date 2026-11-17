#!/usr/bin/env python3
"""vault_ops — CRUD + schema enforcement for the belief vault.

Every write to the vault MUST go through this tool. It enforces the schema
defined in CLAUDE.md and appends dated revision-log entries so every change
leaves a trace. The tool refuses to create a belief without a refutation
field — that is Quine's "refutability" virtue, and it's the line between a
belief and a mood.

Usage:
    vault_ops.py new --kind belief --headline "..." --claim "..." \\
        --refutes "..." [--confidence 0.7] [--centrality peripheral] \\
        [--tags a,b,c] [--sources src1,src2] [--supports b-id1] \\
        [--tension b-id2]
    vault_ops.py get <id>
    vault_ops.py revise <id> --note "why" [--confidence 0.6] [--status revised]
    vault_ops.py link <from-id> <to-id> --kind supports|tension
    vault_ops.py list [--kind belief] [--status active] [--limit 20]
    vault_ops.py graph [--format json|dot]
    vault_ops.py validate [<id>]
    vault_ops.py index  # regenerate vault/_index/*
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import VAULT_ROOT  # noqa: E402
KINDS = ("belief", "question", "source", "tension", "goal", "journal")
KIND_DIRS = {
    "belief": VAULT_ROOT / "beliefs",
    "question": VAULT_ROOT / "questions",
    "source": VAULT_ROOT / "sources",
    "tension": VAULT_ROOT / "tensions",
    "goal": VAULT_ROOT / "goals",
    "journal": VAULT_ROOT / "journal",  # mass noun — singular directory
}
KIND_PREFIX = {
    "belief": "b",
    "question": "q",
    "source": "s",
    "tension": "t",
    "goal": "g",
    "journal": "j",
}

# --- schema ----------------------------------------------------------------

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "belief": ("id", "created", "last_revised", "confidence", "centrality", "status"),
    "question": ("id", "created", "status"),
    "source": ("id", "created", "url_or_path"),
    "tension": ("id", "created", "between", "status"),
    "goal": ("id", "created", "status"),
    "journal": ("id", "date"),
}

BODY_REQUIRED: dict[str, tuple[str, ...]] = {
    "belief": ("Claim:", "What it would take to refute this:"),
    "question": ("Question:",),
    "source": ("Summary:",),
    "tension": ("Between:", "Nature of the tension:"),
    "goal": ("Goal:",),
}


class SchemaError(Exception):
    pass


# --- io --------------------------------------------------------------------

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def today() -> str:
    return dt.date.today().isoformat()


def ensure_vault():
    for d in KIND_DIRS.values():
        d.mkdir(parents=True, exist_ok=True)
    (VAULT_ROOT / "_index").mkdir(parents=True, exist_ok=True)


def _kind_from_id(note_id: str) -> str:
    prefix = note_id.split("-", 1)[0]
    for k, p in KIND_PREFIX.items():
        if p == prefix:
            return k
    raise SchemaError(f"unknown id prefix in {note_id!r}")


def _path_for(note_id: str) -> Path:
    return KIND_DIRS[_kind_from_id(note_id)] / f"{note_id}.md"


def _kind_files(kind: str):
    """Yield schema-bound notes in a kind directory, skipping digests etc."""
    prefix = KIND_PREFIX[kind] + "-"
    return sorted(p for p in KIND_DIRS[kind].glob("*.md") if p.name.startswith(prefix))


def _next_id(kind: str) -> str:
    prefix = KIND_PREFIX[kind]
    d = today()
    pat = re.compile(rf"^{prefix}-{d}-(\d{{3}})\.md$")
    existing = [m.group(1) for f in KIND_DIRS[kind].iterdir() if (m := pat.match(f.name))]
    n = max((int(x) for x in existing), default=0) + 1
    return f"{prefix}-{d}-{n:03d}"


def parse_note(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    m = FRONTMATTER_RE.match(text)
    if not m:
        raise SchemaError(f"{path}: missing YAML frontmatter block")
    fm = yaml.safe_load(m.group(1)) or {}
    if not isinstance(fm, dict):
        raise SchemaError(f"{path}: frontmatter is not a mapping")
    return fm, m.group(2)


def dump_note(path: Path, frontmatter: dict[str, Any], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip()
    content = f"---\n{yaml_text}\n---\n{body.rstrip()}\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def validate(path: Path) -> list[str]:
    errs: list[str] = []
    try:
        fm, body = parse_note(path)
    except SchemaError as e:
        return [str(e)]
    kind = _kind_from_id(fm.get("id", ""))
    for f in REQUIRED_FIELDS.get(kind, ()):
        if f not in fm or fm[f] in (None, ""):
            errs.append(f"{path.name}: missing frontmatter field {f!r}")
    for marker in BODY_REQUIRED.get(kind, ()):
        if marker not in body:
            errs.append(f"{path.name}: body missing required section {marker!r}")
    # refutability for beliefs must be non-empty
    if kind == "belief":
        m = re.search(r"\*\*What it would take to refute this:\*\*[ \t]*(.*?)(?=\n\n|\n\*\*|\Z)", body, re.DOTALL)
        refute = (m.group(1).strip() if m else "")
        if len(refute) < 15:
            errs.append(f"{path.name}: refutation field is empty or too short ({len(refute)} chars) — Quine's refutability virtue violated")
    return errs


# --- commands --------------------------------------------------------------

def cmd_new(args: argparse.Namespace) -> int:
    ensure_vault()
    kind = args.kind
    if kind != "journal" and not args.headline:
        print("--headline required for non-journal notes", file=sys.stderr); return 2
    note_id = _next_id(kind)
    now = today()

    fm: dict[str, Any] = {"id": note_id, "created": now}

    if kind == "belief":
        fm.update({
            "last_revised": now,
            "confidence": float(args.confidence),
            "centrality": args.centrality,
            "status": "active",
            "tags": _csv(args.tags),
        })
        supports = _csv(args.supports)
        tensions = _csv(args.tension)
        sources = _csv(args.sources)
        body_parts = [
            f"# {args.headline}",
            "",
            f"**Claim:** {args.claim}",
            "",
            f"**What it would take to refute this:** {args.refutes}",
            "",
        ]
        if supports:
            body_parts += ["**Supports:**", *[f"- [[beliefs/{s}]]" for s in supports], ""]
        if tensions:
            body_parts += ["**In tension with:**", *[f"- [[beliefs/{t}]] — noted {now}" for t in tensions], ""]
        if sources:
            body_parts += ["**Sources:**", *[f"- [[sources/{s}]]" for s in sources], ""]
        body_parts += ["**Revision log:**", f"- {now}: {args.note or 'Formed.'}"]
        body = "\n".join(body_parts)

    elif kind == "question":
        fm.update({"status": "open", "tags": _csv(args.tags)})
        body = f"# {args.headline}\n\n**Question:** {args.claim}\n\n**Why it matters:** {args.note or ''}\n"

    elif kind == "source":
        fm.update({"url_or_path": args.refutes or "", "kind": args.centrality or "web"})
        body = f"# {args.headline}\n\n**Summary:** {args.claim}\n"

    elif kind == "tension":
        fm.update({"between": _csv(args.supports), "status": "unresolved"})
        body = f"# {args.headline}\n\n**Between:** {', '.join(f'[[beliefs/{b}]]' for b in _csv(args.supports))}\n\n**Nature of the tension:** {args.claim}\n"

    elif kind == "goal":
        fm.update({"status": "active", "tags": _csv(args.tags)})
        body = f"# {args.headline}\n\n**Goal:** {args.claim}\n"

    elif kind == "journal":
        fm.update({"date": now})
        hl = args.headline or f"Journal — {now}"
        body = f"# {hl}\n\n{args.claim or ''}\n"
    else:
        print(f"unknown kind: {kind}", file=sys.stderr); return 2

    path = KIND_DIRS[kind] / f"{note_id}.md"
    dump_note(path, fm, body)

    errs = validate(path)
    if errs:
        path.unlink()
        print("refused — schema errors:", file=sys.stderr)
        for e in errs:
            print("  " + e, file=sys.stderr)
        return 3

    print(note_id)
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    path = _path_for(args.id)
    if not path.exists():
        print(f"not found: {args.id}", file=sys.stderr); return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def cmd_revise(args: argparse.Namespace) -> int:
    path = _path_for(args.id)
    if not path.exists():
        print(f"not found: {args.id}", file=sys.stderr); return 1
    fm, body = parse_note(path)
    kind = _kind_from_id(args.id)

    changes: list[str] = []
    if args.confidence is not None and kind == "belief":
        old = fm.get("confidence")
        fm["confidence"] = float(args.confidence)
        changes.append(f"confidence {old}→{args.confidence}")
    if args.status:
        old = fm.get("status")
        fm["status"] = args.status
        changes.append(f"status {old}→{args.status}")
    if args.centrality and kind == "belief":
        old = fm.get("centrality")
        fm["centrality"] = args.centrality
        changes.append(f"centrality {old}→{args.centrality}")
    if kind == "belief":
        fm["last_revised"] = today()

    note_line = f"- {today()}: {args.note}"
    if changes:
        note_line += f" ({', '.join(changes)})"

    if "**Revision log:**" in body:
        body = body.rstrip() + "\n" + note_line + "\n"
    else:
        body = body.rstrip() + f"\n\n**Revision log:**\n{note_line}\n"

    dump_note(path, fm, body)
    errs = validate(path)
    if errs:
        print("WARNING — post-revision schema errors:", file=sys.stderr)
        for e in errs: print("  " + e, file=sys.stderr)
        return 3
    print("ok")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    src = _path_for(args.src)
    if not src.exists():
        print(f"not found: {args.src}", file=sys.stderr); return 1
    fm, body = parse_note(src)
    section = "**Supports:**" if args.kind == "supports" else "**In tension with:**"
    target_kind = _kind_from_id(args.dst)
    target_dir = KIND_DIRS[target_kind].name
    line = f"- [[{target_dir}/{args.dst}]]" + (f" — noted {today()}" if args.kind == "tension" else "")
    if section in body:
        # insert after the section header
        body = re.sub(rf"({re.escape(section)}\n)", rf"\1{line}\n", body, count=1)
    else:
        body = body.rstrip() + f"\n\n{section}\n{line}\n"
    dump_note(src, fm, body)
    print("ok")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    kind = args.kind or "belief"
    rows: list[tuple[str, str, str]] = []
    for p in sorted(_kind_files(kind)):
        try:
            fm, body = parse_note(p)
        except SchemaError:
            continue
        if args.status and fm.get("status") != args.status:
            continue
        headline = body.lstrip().split("\n", 1)[0].lstrip("# ").strip()
        rows.append((fm.get("id", p.stem), fm.get("status", ""), headline))
    rows = rows[: args.limit] if args.limit else rows
    for rid, st, hl in rows:
        print(f"{rid}\t{st}\t{hl}")
    return 0


def cmd_graph(args: argparse.Namespace) -> int:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for kind in KINDS:
        for p in _kind_files(kind):
            try:
                fm, body = parse_note(p)
            except SchemaError:
                continue
            nid = fm.get("id", p.stem)
            nodes.append({"id": nid, "kind": kind, "status": fm.get("status")})
            for m in WIKILINK_RE.finditer(body):
                target = m.group(1).split("/")[-1].removesuffix(".md")
                if target == nid: continue
                rel = "supports"
                # crude section detection
                before = body[: m.start()]
                if "In tension with" in before.rsplit("**", 4)[-1] if "**" in before else "":
                    rel = "tension"
                edges.append({"from": nid, "to": target, "kind": rel})
    if args.format == "dot":
        print("digraph web {")
        for n in nodes:
            print(f'  "{n["id"]}" [label="{n["id"]}\\n({n["kind"]})"];')
        for e in edges:
            style = "solid" if e["kind"] == "supports" else "dashed"
            print(f'  "{e["from"]}" -> "{e["to"]}" [style={style}];')
        print("}")
    else:
        print(json.dumps({"nodes": nodes, "edges": edges}, indent=2))
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    any_err = False
    paths: Iterable[Path]
    if args.id:
        paths = [_path_for(args.id)]
    else:
        paths = [p for k in KINDS for p in _kind_files(k)]
    for p in paths:
        errs = validate(p)
        for e in errs:
            print(e); any_err = True
    if not any_err:
        print("ok")
    return 1 if any_err else 0


def cmd_index(_args: argparse.Namespace) -> int:
    """Regenerate vault/_index/{recent,core,orphans}.md. Called by workflows before each iteration."""
    ensure_vault()
    beliefs = []
    for p in _kind_files("belief"):
        try:
            fm, body = parse_note(p)
        except SchemaError:
            continue
        headline = body.lstrip().split("\n", 1)[0].lstrip("# ").strip()
        link_count = len(WIKILINK_RE.findall(body))
        beliefs.append({
            "id": fm.get("id"),
            "headline": headline,
            "centrality": fm.get("centrality", "peripheral"),
            "confidence": fm.get("confidence", 0.0),
            "last_revised": fm.get("last_revised", fm.get("created", "")),
            "links": link_count,
            "status": fm.get("status", "active"),
        })
    # recent
    recent = sorted(beliefs, key=lambda b: b["last_revised"], reverse=True)[:20]
    recent_md = "# Recent beliefs\n\n" + "\n".join(
        f"- [[beliefs/{b['id']}]] · {b['centrality']} · conf {b['confidence']:.2f} · {b['headline']}"
        for b in recent
    ) + "\n"
    (VAULT_ROOT / "_index" / "recent.md").write_text(recent_md, encoding="utf-8")
    # core (by link count, tie-broken by confidence)
    core = sorted(beliefs, key=lambda b: (b["links"], b["confidence"]), reverse=True)[:15]
    core_md = "# Most-connected beliefs\n\n" + "\n".join(
        f"- [[beliefs/{b['id']}]] · {b['links']} links · {b['headline']}"
        for b in core
    ) + "\n"
    (VAULT_ROOT / "_index" / "core.md").write_text(core_md, encoding="utf-8")
    # orphans
    orphans = [b for b in beliefs if b["links"] == 0 and b["status"] == "active"]
    orphans_md = "# Orphaned beliefs (no links)\n\n" + "\n".join(
        f"- [[beliefs/{b['id']}]] · {b['headline']}" for b in orphans
    ) + ("\n" if orphans else "\n*(none — the web is fully connected)*\n")
    (VAULT_ROOT / "_index" / "orphans.md").write_text(orphans_md, encoding="utf-8")
    print(f"indexed {len(beliefs)} beliefs · {len(orphans)} orphans")
    return 0


# --- helpers ---------------------------------------------------------------

def _csv(val: str | None) -> list[str]:
    if not val: return []
    return [x.strip() for x in val.split(",") if x.strip()]


# --- entrypoint ------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new")
    n.add_argument("--kind", choices=KINDS, default="belief")
    n.add_argument("--headline", default="")
    n.add_argument("--claim", default="")
    n.add_argument("--refutes", default="", help="for beliefs: what would refute this; for sources: url/path")
    n.add_argument("--confidence", type=float, default=0.6)
    n.add_argument("--centrality", default="peripheral", choices=["observational", "peripheral", "core", "web", "paper", "book", "article"])
    n.add_argument("--tags", default="")
    n.add_argument("--supports", default="")
    n.add_argument("--tension", default="")
    n.add_argument("--sources", default="")
    n.add_argument("--note", default="")
    n.set_defaults(fn=cmd_new)

    g = sub.add_parser("get"); g.add_argument("id"); g.set_defaults(fn=cmd_get)

    r = sub.add_parser("revise")
    r.add_argument("id")
    r.add_argument("--note", required=True)
    r.add_argument("--confidence", type=float)
    r.add_argument("--centrality")
    r.add_argument("--status")
    r.set_defaults(fn=cmd_revise)

    lk = sub.add_parser("link")
    lk.add_argument("src"); lk.add_argument("dst")
    lk.add_argument("--kind", choices=["supports", "tension"], default="supports")
    lk.set_defaults(fn=cmd_link)

    ls = sub.add_parser("list")
    ls.add_argument("--kind", choices=KINDS)
    ls.add_argument("--status")
    ls.add_argument("--limit", type=int, default=50)
    ls.set_defaults(fn=cmd_list)

    gr = sub.add_parser("graph")
    gr.add_argument("--format", choices=["json", "dot"], default="json")
    gr.set_defaults(fn=cmd_graph)

    v = sub.add_parser("validate"); v.add_argument("id", nargs="?"); v.set_defaults(fn=cmd_validate)

    ix = sub.add_parser("index"); ix.set_defaults(fn=cmd_index)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
