#!/usr/bin/env python3
"""daily_digest — compile today's (or any day's) belief-work into a readable digest.

Collects: new beliefs, revised beliefs, new sources, new questions, new tensions,
the journal entry itself. Writes to vault/journal/<date>-digest.md.

Usage:
    daily_digest.py run [--date YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import VAULT_ROOT as VAULT  # noqa: E402
KINDS = ["beliefs", "questions", "sources", "tensions", "goals"]
PREFIXES = {"beliefs": "b", "questions": "q", "sources": "s", "tensions": "t", "goals": "g"}
FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _parse(path: Path):
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m: return {}, ""
    try: fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError: fm = {}
    return fm, m.group(2)


def _headline(body: str) -> str:
    return body.lstrip().split("\n", 1)[0].lstrip("# ").strip()


def cmd_run(args) -> int:
    date = args.date or dt.date.today().isoformat()
    sections: list[str] = [f"# Digest — {date}", ""]

    # New things created today
    for kind in KINDS:
        kdir = VAULT / kind
        if not kdir.exists(): continue
        new_items = []
        for p in sorted(kdir.glob(f"{PREFIXES[kind]}-*.md")):
            fm, body = _parse(p)
            if str(fm.get("created", "")) == date:
                new_items.append((fm.get("id", p.stem), _headline(body)))
        if new_items:
            sections.append(f"## New {kind} ({len(new_items)})")
            for nid, hl in new_items:
                sections.append(f"- [[{kind}/{nid}]] — {hl}")
            sections.append("")

    # Revisions today (beliefs whose revision_log has a line starting with today)
    revised = []
    for p in (VAULT / "beliefs").glob("b-*.md"):
        fm, body = _parse(p)
        if str(fm.get("created", "")) == date:
            continue  # already listed in "new"
        if str(fm.get("last_revised", "")) == date:
            # find today's line
            m = re.search(rf"- {re.escape(date)}: (.+)", body)
            note = m.group(1) if m else "(no note)"
            revised.append((fm.get("id", p.stem), _headline(body), note))
    if revised:
        sections.append(f"## Revised beliefs ({len(revised)})")
        for nid, hl, note in revised:
            sections.append(f"- [[beliefs/{nid}]] — {hl}")
            sections.append(f"  - {note}")
        sections.append("")

    # Journal entry itself
    jpath = VAULT / "journal" / f"j-{date}-001.md"
    if jpath.exists():
        _, body = _parse(jpath)
        sections.append("## Journal")
        sections.append("")
        sections.append(body.strip())
        sections.append("")

    if len(sections) <= 2:
        sections.append("*(no belief-work recorded for this date)*")

    out = VAULT / "journal" / f"digest-{date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(sections), encoding="utf-8")
    print(out)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--date", default=""); r.set_defaults(fn=cmd_run)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
