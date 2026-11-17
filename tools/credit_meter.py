#!/usr/bin/env python3
"""credit_meter — token budget enforcement for session loop and nightly routine.

Tracks daily token usage in a per-day ledger (.credit_ledger.json). Returns
`ok` if under budget, `stop` if over. The loop must honor `stop`.

Usage:
    credit_meter.py check [--kind session|nightly]   # returns ok|stop + usage
    credit_meter.py record --tokens N [--kind session|nightly]
    credit_meter.py report [--days 7]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import CREDIT_LEDGER as LEDGER, ENV_FILE  # noqa: E402


def _env(key: str, default: str) -> str:
    # lightweight .env loader (avoid python-dotenv dependency)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return os.environ.get(key, default)


def _load() -> dict:
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save(data: dict) -> None:
    tmp = LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(LEDGER)


def _budget(kind: str) -> int:
    key = "NIGHTLY_TOKEN_BUDGET" if kind == "nightly" else "DAILY_TOKEN_BUDGET"
    default = "500000" if kind == "nightly" else "2000000"
    try:
        return int(_env(key, default))
    except ValueError:
        return int(default)


def _today() -> str:
    return dt.date.today().isoformat()


def cmd_check(args: argparse.Namespace) -> int:
    data = _load()
    today = _today()
    used = int(data.get(today, {}).get(args.kind, 0))
    budget = _budget(args.kind)
    status = "stop" if used >= budget else "ok"
    remaining = max(0, budget - used)
    print(f"{status} used={used} budget={budget} remaining={remaining} kind={args.kind}")
    return 0 if status == "ok" else 1


def cmd_record(args: argparse.Namespace) -> int:
    if args.tokens <= 0:
        print("tokens must be > 0", file=sys.stderr); return 2
    data = _load()
    today = _today()
    day = data.setdefault(today, {})
    day[args.kind] = int(day.get(args.kind, 0)) + int(args.tokens)
    _save(data)
    print(f"ok added={args.tokens} total={day[args.kind]} kind={args.kind}")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    data = _load()
    today = dt.date.today()
    rows = []
    for i in range(args.days):
        d = (today - dt.timedelta(days=i)).isoformat()
        rec = data.get(d, {})
        rows.append((d, int(rec.get("session", 0)), int(rec.get("nightly", 0))))
    print(f"{'date':<12} {'session':>10} {'nightly':>10}")
    for d, s, n in rows:
        print(f"{d:<12} {s:>10} {n:>10}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check")
    c.add_argument("--kind", choices=["session", "nightly"], default="session")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("record")
    r.add_argument("--tokens", type=int, required=True)
    r.add_argument("--kind", choices=["session", "nightly"], default="session")
    r.set_defaults(fn=cmd_record)

    rp = sub.add_parser("report")
    rp.add_argument("--days", type=int, default=7)
    rp.set_defaults(fn=cmd_report)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
