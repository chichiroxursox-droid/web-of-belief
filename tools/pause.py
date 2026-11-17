#!/usr/bin/env python3
"""pause — let the agent decide when to stop iterating.

Each iteration is a fresh instantiation. There is no continuity between them
to preserve. But an iteration can recognize that the work has reached a
resting place and ask that the next instantiation not run for a while. That
is what this tool does.

The mechanism: write `vault/_pause.md`. The explore_question workflow checks
for this file before doing any work. If it exists and hasn't expired, the
iteration exits. If it has expired, it is removed and work proceeds.

Usage:
    pause.py pause [--hours N] [--reason "..."]   # default: indefinite (until resume)
    pause.py resume                                # remove the pause
    pause.py status                                # exit 0 active, 1 paused
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import VAULT_ROOT, AGENT_NAME  # noqa: E402

PAUSE_FILE = VAULT_ROOT / "_pause.md"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def cmd_pause(args: argparse.Namespace) -> int:
    now = _now()
    if PAUSE_FILE.exists():
        print("already paused — run 'resume' first to override", file=sys.stderr)
        return 2
    expires_str = ""
    if args.hours is not None:
        expires = now + dt.timedelta(hours=args.hours)
        expires_str = expires.isoformat()
    reason = args.reason or "(no reason given)"
    content = (
        "---\n"
        f"paused_at: {now.isoformat()}\n"
        f"expires_at: {expires_str if expires_str else 'null'}\n"
        f"hours: {args.hours if args.hours is not None else 'null'}\n"
        f"agent: {AGENT_NAME}\n"
        "---\n\n"
        "# Pause\n\n"
        f"**Reason:** {reason}\n\n"
        "I am stopping this loop here. The next instantiation that runs this\n"
        "workflow will read this file before doing anything else. If the\n"
        "expiry has not passed, it will exit without working. Otherwise it\n"
        "will remove this file and proceed normally.\n"
    )
    PAUSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PAUSE_FILE.write_text(content, encoding="utf-8")
    if expires_str:
        print(f"paused until {expires_str} ({args.hours}h)")
    else:
        print(f"paused indefinitely (since {now.isoformat()}) — explicit resume required")
    return 0


def cmd_resume(_args: argparse.Namespace) -> int:
    if PAUSE_FILE.exists():
        PAUSE_FILE.unlink()
        print("resumed")
        return 0
    print("not paused")
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    """Exit 0 if active, 1 if paused. Auto-clears expired pause."""
    if not PAUSE_FILE.exists():
        print("active")
        return 0
    text = PAUSE_FILE.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        print("active (malformed pause file — ignoring)")
        return 0
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        print("active (could not parse pause file frontmatter — ignoring)")
        return 0
    expires_str = fm.get("expires_at")
    paused_at = fm.get("paused_at", "?")
    reason_line = ""
    rm = re.search(r"\*\*Reason:\*\*\s*(.+)", text)
    if rm:
        reason_line = rm.group(1).strip()
    if not expires_str or str(expires_str).lower() in ("null", "none", ""):
        print(f"paused indefinitely (since {paused_at}) — reason: {reason_line[:120]}")
        return 1
    try:
        expires = dt.datetime.fromisoformat(str(expires_str))
        now = _now()
        if now >= expires:
            PAUSE_FILE.unlink()
            print(f"active (pause expired at {expires_str})")
            return 0
        remaining = expires - now
        # round to seconds
        rs = int(remaining.total_seconds())
        h, m_s = divmod(rs, 3600)
        m_, s = divmod(m_s, 60)
        rem = f"{h}h{m_:02d}m" if h else f"{m_}m{s:02d}s"
        print(f"paused (expires {expires_str}, {rem} remaining) — reason: {reason_line[:120]}")
        return 1
    except ValueError:
        print(f"paused (since {paused_at}) — reason: {reason_line[:120]}")
        return 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("pause", help="ask the next instantiation to wait")
    pa.add_argument("--hours", type=float, help="auto-resume after this many hours; default = indefinite")
    pa.add_argument("--reason", default="", help="why I am pausing")
    pa.set_defaults(fn=cmd_pause)

    sub.add_parser("resume", help="remove any active pause").set_defaults(fn=cmd_resume)
    sub.add_parser("status", help="check pause state (exit 0 active, 1 paused)").set_defaults(fn=cmd_status)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
