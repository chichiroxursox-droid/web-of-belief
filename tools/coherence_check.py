#!/usr/bin/env python3
"""coherence_check — walk the belief graph, flag intellectual honesty problems.

Emits: orphans, unresolved tensions, suspiciously weak clusters, stale-but-active beliefs.
The nightly reflect routine consumes this and decides what to address.

Usage:
    coherence_check.py run [--since YYYY-MM-DD]
    coherence_check.py clusters
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import VAULT_ROOT as VAULT  # noqa: E402
BELIEFS = VAULT / "beliefs"
TENSIONS = VAULT / "tensions"

WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse(path: Path):
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m: return {}, ""
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, m.group(2)


def _links(body: str) -> tuple[list[str], list[str]]:
    """Return (supports_ids, tension_ids). Section-aware."""
    supports, tensions = [], []
    current = None
    for line in body.splitlines():
        if line.startswith("**Supports:**"):
            current = "s"; continue
        if line.startswith("**In tension with:**"):
            current = "t"; continue
        if line.startswith("**") and line.endswith(":**"):
            current = None; continue
        if current is None:
            continue
        for m in WIKILINK_RE.finditer(line):
            target = m.group(1).split("/")[-1]
            (supports if current == "s" else tensions).append(target)
    return supports, tensions


def _load_beliefs():
    notes = {}
    for p in BELIEFS.glob("b-*.md"):
        fm, body = _parse(p)
        if not fm: continue
        nid = fm.get("id", p.stem)
        supports, tensions = _links(body)
        notes[nid] = {
            "id": nid,
            "fm": fm,
            "path": str(p.relative_to(VAULT.parent)),
            "supports": supports,
            "tensions": tensions,
            "headline": body.lstrip().split("\n", 1)[0].lstrip("# ").strip(),
        }
    return notes


def cmd_run(args) -> int:
    notes = _load_beliefs()
    today = dt.date.today()
    since = dt.date.fromisoformat(args.since) if args.since else today - dt.timedelta(days=1)

    # incoming counts for orphan detection
    incoming = defaultdict(int)
    for n in notes.values():
        for t in n["supports"] + n["tensions"]:
            incoming[t] += 1

    orphans = []
    weak = []
    stale = []
    unresolved_tensions = []
    active = [n for n in notes.values() if n["fm"].get("status", "active") == "active"]

    for n in active:
        total_links = len(n["supports"]) + len(n["tensions"]) + incoming[n["id"]]
        if total_links == 0:
            orphans.append(n)
        elif total_links == 1:
            weak.append(n)
        last_rev = n["fm"].get("last_revised") or n["fm"].get("created")
        try:
            last_rev_date = dt.date.fromisoformat(str(last_rev))
            if (today - last_rev_date).days > 30:
                stale.append(n)
        except (ValueError, TypeError):
            pass

    # unresolved tensions: any belief that lists an "in tension with" that has no
    # corresponding tension note
    tension_notes_exist = {_parse(p)[0].get("id", p.stem) for p in TENSIONS.glob("t-*.md")}
    tension_ids_referenced = set()
    for n in active:
        for t in n["tensions"]:
            pair = tuple(sorted([n["id"], t]))
            tension_ids_referenced.add(pair)

    # naive: any pair not clearly documented in tensions/ is "undocumented"
    undocumented = []
    for a, b in tension_ids_referenced:
        # check if any tension note mentions both
        documented = False
        for p in TENSIONS.glob("t-*.md"):
            text = p.read_text(encoding="utf-8")
            if a in text and b in text:
                documented = True; break
        if not documented:
            undocumented.append((a, b))

    report = {
        "orphans": [{"id": n["id"], "headline": n["headline"]} for n in orphans],
        "weak_clusters": [{"id": n["id"], "headline": n["headline"]} for n in weak],
        "stale_active": [{"id": n["id"], "last_revised": str(n["fm"].get("last_revised")), "headline": n["headline"]} for n in stale],
        "undocumented_tensions": [{"between": list(pair)} for pair in undocumented],
        "summary": {
            "beliefs_total": len(notes),
            "beliefs_active": len(active),
            "orphan_count": len(orphans),
            "weak_count": len(weak),
            "stale_count": len(stale),
            "undocumented_tension_count": len(undocumented),
        },
    }
    print(json.dumps(report, indent=2))
    return 0


def cmd_clusters(_args) -> int:
    """Connected components of the belief graph (undirected over supports+tensions)."""
    notes = _load_beliefs()
    parent = {nid: nid for nid in notes}
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb

    for n in notes.values():
        for t in n["supports"] + n["tensions"]:
            if t in notes:
                union(n["id"], t)

    groups = defaultdict(list)
    for nid in notes:
        groups[find(nid)].append(nid)
    sizes = sorted([(len(members), root, members) for root, members in groups.items()], reverse=True)
    print(json.dumps([{"size": s, "members": m} for s, _, m in sizes], indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run")
    r.add_argument("--since", default="", help="YYYY-MM-DD; default = yesterday")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("clusters").set_defaults(fn=cmd_clusters)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
