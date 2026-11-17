#!/usr/bin/env python3
"""peer_read — read-only access to a paired agent's vault.

For paired agents (Castor/Pollux), each one's wob wrapper sets WOB_PEER_VAULT
to the OTHER agent's vault/ directory. This tool reads from there — it NEVER
writes. Write-access to the peer vault is denied by construction.

Usage:
    peer_read.py journal [--last N]
    peer_read.py since <YYYY-MM-DD>          # items created/revised since date
    peer_read.py belief <id>                 # read a peer belief
    peer_read.py list [--kind belief]        # list peer notes
    peer_read.py search "<query>" [--k 5]    # semantic search over peer vault
"""

from __future__ import annotations

import argparse
import datetime as dt
import json as _json
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import PEER_VAULT, PEER_NAME, AGENT_NAME, VAULT_ROOT  # noqa: E402


def _log_peer_read(action: str, detail: str = "") -> None:
    """Append a jsonl entry the dashboard can tail for the 'talking' indicator."""
    idx = VAULT_ROOT / "_index"
    idx.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
        "agent": AGENT_NAME,
        "peer": PEER_NAME,
        "action": action,
        "detail": detail,
    }
    try:
        with (idx / "peer_reads.jsonl").open("a", encoding="utf-8") as f:
            f.write(_json.dumps(entry) + "\n")
    except OSError:
        pass  # logging must never break the read itself

FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
KIND_DIRS = {
    "belief": "beliefs",
    "question": "questions",
    "source": "sources",
    "tension": "tensions",
    "goal": "goals",
    "journal": "journal",
}
PREFIX = {"belief": "b", "question": "q", "source": "s", "tension": "t", "goal": "g", "journal": "j"}


def _require_peer() -> Path:
    if PEER_VAULT is None:
        print("error: WOB_PEER_VAULT is not set — this agent has no peer", file=sys.stderr)
        sys.exit(2)
    if not PEER_VAULT.exists():
        print(f"error: peer vault not found at {PEER_VAULT}", file=sys.stderr)
        sys.exit(2)
    return PEER_VAULT


def _parse(path: Path):
    text = path.read_text(encoding="utf-8")
    m = FM_RE.match(text)
    if not m: return {}, text
    try: fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError: fm = {}
    return fm, m.group(2)


def _files(vault: Path, kind: str):
    prefix = PREFIX[kind] + "-"
    d = vault / KIND_DIRS[kind]
    if not d.exists(): return []
    return sorted(p for p in d.glob("*.md") if p.name.startswith(prefix))


def cmd_journal(args) -> int:
    _log_peer_read("journal", f"--last {args.last}")
    vault = _require_peer()
    entries = _files(vault, "journal")
    entries = entries[-args.last :] if args.last else entries
    for p in entries:
        fm, body = _parse(p)
        print(f"━━━ {PEER_NAME}: {p.name} ({fm.get('date', '?')}) ━━━")
        print(body.strip())
        print()
    return 0


def cmd_since(args) -> int:
    _log_peer_read("since", args.date)
    vault = _require_peer()
    try:
        cutoff = dt.date.fromisoformat(args.date)
    except ValueError:
        print(f"bad date: {args.date}", file=sys.stderr); return 2
    hits = []
    for kind in ("belief", "question", "source", "tension", "goal"):
        for p in _files(vault, kind):
            fm, body = _parse(p)
            created = fm.get("created")
            revised = fm.get("last_revised", created)
            try:
                d = dt.date.fromisoformat(str(revised))
            except (TypeError, ValueError):
                continue
            if d >= cutoff:
                headline = body.lstrip().split("\n", 1)[0].lstrip("# ").strip()
                hits.append((str(d), kind, fm.get("id", p.stem), headline))
    hits.sort(reverse=True)
    for d, k, i, h in hits:
        print(f"{d}\t{k}\t{i}\t{h}")
    return 0


def cmd_belief(args) -> int:
    _log_peer_read("belief", args.id)
    vault = _require_peer()
    for k, dirname in KIND_DIRS.items():
        p = vault / dirname / f"{args.id}.md"
        if p.exists():
            print(p.read_text(encoding="utf-8"))
            return 0
    print(f"not found in peer vault: {args.id}", file=sys.stderr); return 1


def cmd_list(args) -> int:
    _log_peer_read("list", args.kind or "belief")
    vault = _require_peer()
    kind = args.kind or "belief"
    for p in _files(vault, kind):
        fm, body = _parse(p)
        headline = body.lstrip().split("\n", 1)[0].lstrip("# ").strip()
        print(f"{fm.get('id', p.stem)}\t{fm.get('status', '')}\t{headline}")
    return 0


def cmd_search(args) -> int:
    """Semantic search over the peer's vault using the peer's embedding index."""
    _log_peer_read("search", args.query)
    vault = _require_peer()
    emb = vault / "_index" / "embeddings.npz"
    meta_path = vault / "_index" / "embeddings_meta.json"
    if not emb.exists() or not meta_path.exists():
        print(f"error: peer has no embedding index at {emb}. Ask the peer to run retrieve.py embed.", file=sys.stderr)
        return 2
    import json
    import numpy as np
    from sentence_transformers import SentenceTransformer
    vecs = np.load(emb)["vectors"]
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    model = SentenceTransformer("all-MiniLM-L6-v2")
    q = model.encode([args.query], normalize_embeddings=True)[0]
    sims = vecs @ q
    order = np.argsort(-sims)
    for i in order[: args.k]:
        m = meta[int(i)]
        print(f"{sims[int(i)]:.3f}\t{m['id']}\t{m['kind']}\t{m['headline']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    j = sub.add_parser("journal"); j.add_argument("--last", type=int, default=3); j.set_defaults(fn=cmd_journal)
    s = sub.add_parser("since"); s.add_argument("date"); s.set_defaults(fn=cmd_since)
    b = sub.add_parser("belief"); b.add_argument("id"); b.set_defaults(fn=cmd_belief)
    l = sub.add_parser("list"); l.add_argument("--kind"); l.set_defaults(fn=cmd_list)
    se = sub.add_parser("search"); se.add_argument("query"); se.add_argument("--k", type=int, default=5); se.set_defaults(fn=cmd_search)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
