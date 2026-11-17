#!/usr/bin/env python3
"""retrieve — semantic search over the vault via sentence-transformers embeddings.

Usage:
    retrieve.py embed                           # rebuild/update embeddings
    retrieve.py search "<query>" [--k 10] [--kinds belief,question,source]
    retrieve.py neighbors <id> [--k 10]         # beliefs closest to a given note
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import VAULT_ROOT as VAULT  # noqa: E402
INDEX_DIR = VAULT / "_index"
EMB_PATH = INDEX_DIR / "embeddings.npz"
META_PATH = INDEX_DIR / "embeddings_meta.json"
DEFAULT_MODEL = "all-MiniLM-L6-v2"

KINDS = ("beliefs", "questions", "sources", "tensions", "goals")


def _load_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(DEFAULT_MODEL)


def _iter_notes():
    for kdir in (VAULT / k for k in KINDS):
        if not kdir.exists():
            continue
        for p in sorted(kdir.glob("*.md")):
            text = p.read_text(encoding="utf-8")
            if not text.startswith("---\n"):
                continue
            _, _, rest = text[4:].partition("\n---\n")
            fm_block, body = text[4:].split("\n---\n", 1) if "\n---\n" in text[4:] else ("", rest)
            try:
                fm = yaml.safe_load(fm_block) or {}
            except yaml.YAMLError:
                fm = {}
            headline = body.lstrip().split("\n", 1)[0].lstrip("# ").strip()
            yield {
                "id": fm.get("id", p.stem),
                "kind": kdir.name.rstrip("s"),
                "path": str(p.relative_to(VAULT.parent)),
                "headline": headline,
                "body": body.strip(),
                "status": fm.get("status"),
            }


def cmd_embed(_args) -> int:
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    notes = list(_iter_notes())
    if not notes:
        print("no notes to embed"); return 0
    model = _load_model()
    texts = [f"{n['headline']}\n\n{n['body']}" for n in notes]
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    np.savez_compressed(EMB_PATH, vectors=vecs)
    META_PATH.write_text(json.dumps(notes, indent=2), encoding="utf-8")
    print(f"embedded {len(notes)} notes · dim={vecs.shape[1]}")
    return 0


def _load_index():
    if not EMB_PATH.exists() or not META_PATH.exists():
        print("no embeddings. run: retrieve.py embed", file=sys.stderr); sys.exit(2)
    vecs = np.load(EMB_PATH)["vectors"]
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))
    return vecs, meta


def _search(query_vec: np.ndarray, k: int, kinds: list[str] | None) -> list[tuple[float, dict]]:
    vecs, meta = _load_index()
    sims = vecs @ query_vec
    order = np.argsort(-sims)
    hits: list[tuple[float, dict]] = []
    for i in order:
        m = meta[int(i)]
        if kinds and m["kind"] not in kinds:
            continue
        hits.append((float(sims[int(i)]), m))
        if len(hits) >= k:
            break
    return hits


def cmd_search(args) -> int:
    model = _load_model()
    q = model.encode([args.query], normalize_embeddings=True)[0]
    kinds = [k.strip().rstrip("s") for k in args.kinds.split(",")] if args.kinds else None
    for score, m in _search(q, args.k, kinds):
        print(f"{score:.3f}\t{m['id']}\t{m['kind']}\t{m['headline']}")
    return 0


def cmd_neighbors(args) -> int:
    vecs, meta = _load_index()
    idx = next((i for i, m in enumerate(meta) if m["id"] == args.id), None)
    if idx is None:
        print(f"not found: {args.id}", file=sys.stderr); return 1
    hits = _search(vecs[idx], args.k + 1, None)
    for score, m in hits:
        if m["id"] == args.id:
            continue
        print(f"{score:.3f}\t{m['id']}\t{m['kind']}\t{m['headline']}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("embed").set_defaults(fn=cmd_embed)

    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--k", type=int, default=10)
    s.add_argument("--kinds", default="", help="comma-separated: belief,question,source,tension,goal")
    s.set_defaults(fn=cmd_search)

    n = sub.add_parser("neighbors")
    n.add_argument("id")
    n.add_argument("--k", type=int, default=10)
    n.set_defaults(fn=cmd_neighbors)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
