#!/usr/bin/env python3
"""pick_next — score open questions by epistemic priority.

Scoring combines:
  - gap impact: how many orphan/weak beliefs are semantically near the question
  - centrality proximity: how close to the most-connected existing beliefs
  - staleness: how long the question has been open without progress
  - recency penalty: very recently created questions get a small bump (follow the thread)

Usage:
    pick_next.py next [--top 5]
    pick_next.py explain <id>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

try:
    import numpy as np
except ImportError:
    np = None  # type: ignore

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import VAULT_ROOT as VAULT  # noqa: E402
import retrieve  # noqa: E402
QUESTIONS = VAULT / "questions"
BELIEFS = VAULT / "beliefs"
IDX = VAULT / "_index"


def _parse(path: Path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"): return {}, ""
    try:
        head, body = text[4:].split("\n---\n", 1)
        return yaml.safe_load(head) or {}, body
    except ValueError:
        return {}, ""


def _open_questions():
    out = []
    for p in QUESTIONS.glob("q-*.md"):
        fm, body = _parse(p)
        if fm.get("status", "open") != "open": continue
        out.append({
            "id": fm.get("id", p.stem),
            "created": str(fm.get("created", "")),
            "headline": body.lstrip().split("\n", 1)[0].lstrip("# ").strip(),
            "body": body,
        })
    return out


def _score_question(q, vecs, meta, orphan_ids: set, core_ids: set):
    """Combine components into a single score."""
    model = retrieve._load_model()
    qvec = model.encode([f"{q['headline']}\n\n{q['body']}"], normalize_embeddings=True)[0]
    sims = vecs @ qvec
    # gap impact: avg similarity to top 5 orphans
    orphan_idx = [i for i, m in enumerate(meta) if m["id"] in orphan_ids]
    gap = float(np.mean(sorted([sims[i] for i in orphan_idx], reverse=True)[:5])) if orphan_idx else 0.0
    # centrality proximity: avg sim to top 5 core
    core_idx = [i for i, m in enumerate(meta) if m["id"] in core_ids]
    cent = float(np.mean(sorted([sims[i] for i in core_idx], reverse=True)[:5])) if core_idx else 0.0
    # staleness (days since created)
    try:
        age = (dt.date.today() - dt.date.fromisoformat(q["created"])).days
    except ValueError:
        age = 0
    stale = min(age / 30.0, 1.0)
    # recency bump for last 2 days
    recency = 0.3 if age <= 2 else 0.0
    score = 0.4 * gap + 0.3 * cent + 0.2 * stale + 0.1 * recency
    return score, {"gap": gap, "cent": cent, "stale": stale, "recency": recency, "age_days": age}


def _index_sets() -> tuple[set, set]:
    orphans_md = (IDX / "orphans.md").read_text(encoding="utf-8") if (IDX / "orphans.md").exists() else ""
    core_md = (IDX / "core.md").read_text(encoding="utf-8") if (IDX / "core.md").exists() else ""
    import re
    orphan_ids = {m.group(1).split("/")[-1] for m in re.finditer(r"\[\[beliefs/([^\]]+)\]\]", orphans_md)}
    core_ids = {m.group(1).split("/")[-1] for m in re.finditer(r"\[\[beliefs/([^\]]+)\]\]", core_md)}
    return orphan_ids, core_ids


def cmd_next(args) -> int:
    qs = _open_questions()
    if not qs:
        print("(no open questions — generate some in the next reflect pass)"); return 0
    if np is None or not (IDX / "embeddings.npz").exists():
        # fallback: order by age
        qs.sort(key=lambda q: q["created"])
        for q in qs[: args.top]:
            print(f"{q['id']}\t{q['headline']}")
        return 0
    vecs, meta = retrieve._load_index()
    orphan_ids, core_ids = _index_sets()
    scored = [(q, *_score_question(q, vecs, meta, orphan_ids, core_ids)) for q in qs]
    scored.sort(key=lambda t: t[1], reverse=True)
    for q, score, parts in scored[: args.top]:
        print(f"{score:.3f}\t{q['id']}\t{q['headline']}")
    return 0


def cmd_explain(args) -> int:
    qs = _open_questions()
    q = next((q for q in qs if q["id"] == args.id), None)
    if not q:
        print(f"not found: {args.id}", file=sys.stderr); return 1
    if np is None or not (IDX / "embeddings.npz").exists():
        print("embeddings not available", file=sys.stderr); return 2
    vecs, meta = retrieve._load_index()
    orphan_ids, core_ids = _index_sets()
    score, parts = _score_question(q, vecs, meta, orphan_ids, core_ids)
    print(json.dumps({"id": q["id"], "score": score, **parts}, indent=2))
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("next"); n.add_argument("--top", type=int, default=5); n.set_defaults(fn=cmd_next)
    e = sub.add_parser("explain"); e.add_argument("id"); e.set_defaults(fn=cmd_explain)
    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
