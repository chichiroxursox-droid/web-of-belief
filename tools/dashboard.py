#!/usr/bin/env python3
"""dashboard — live browser dashboard for all Web of Belief agents.

Scans the parent Desktop/CLAUDE CODE directory for sibling projects that have a
.env file with WOB_AGENT_NAME, then serves a small HTTP endpoint with an
embedded HTML page that polls agent state every few seconds.

The dashboard is observational only. It never writes to any vault, never
starts or stops agents. It reads file mtimes, counts vault notes, parses
credit ledgers, and tails each agent's peer_reads.jsonl.

Usage:
    ./wob dashboard
    python3 tools/dashboard.py --port 8765 \\
        --scan-root "/Users/chichi/Desktop/CLAUDE CODE"

Open http://localhost:8765 in a browser.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import traceback
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

# --- .env parsing ----------------------------------------------------------

ENV_LINE_RE = re.compile(r'^\s*([A-Z_][A-Z0-9_]*)=\s*"?([^"\n]*)"?\s*$')


def _parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = ENV_LINE_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


# --- discovery -------------------------------------------------------------

@dataclass
class AgentInfo:
    name: str
    path: Path
    vault: Path
    peer_vault: Path | None
    peer_name: str | None
    credit_ledger: Path


def discover_agents(scan_root: Path) -> list[AgentInfo]:
    agents: list[AgentInfo] = []
    if not scan_root.exists():
        return agents
    for sub in sorted(scan_root.iterdir()):
        if not sub.is_dir():
            continue
        env = _parse_env(sub / ".env")
        name = env.get("WOB_AGENT_NAME")
        if not name:
            continue
        vault = Path(env.get("WOB_VAULT_ROOT") or (sub / "vault"))
        peer_vault_str = env.get("WOB_PEER_VAULT", "").strip()
        peer_vault = Path(peer_vault_str) if peer_vault_str else None
        peer_name = env.get("WOB_PEER_NAME") or None
        credit = Path(env.get("WOB_CREDIT_LEDGER") or (sub / "tools" / ".credit_ledger.json"))
        agents.append(AgentInfo(
            name=name, path=sub, vault=vault,
            peer_vault=peer_vault, peer_name=peer_name,
            credit_ledger=credit,
        ))
    # sort: solos first (alpha), then paired
    agents.sort(key=lambda a: (a.peer_name is not None, a.name))
    return agents


# --- snapshot helpers ------------------------------------------------------

FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
KIND_DIRS = {
    "beliefs": "b",
    "questions": "q",
    "sources": "s",
    "tensions": "t",
    "goals": "g",
    "journal": "j",
}


def _parse_note(path: Path) -> tuple[dict, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}, ""
    m = FM_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return fm, m.group(2)


def _kind_files(vault: Path, kind: str) -> list[Path]:
    prefix = KIND_DIRS[kind] + "-"
    d = vault / kind
    if not d.exists():
        return []
    return sorted(p for p in d.glob("*.md") if p.name.startswith(prefix))


def _latest_activity(vault: Path) -> dt.datetime | None:
    if not vault.exists():
        return None
    latest = 0.0
    for p in vault.rglob("*.md"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > latest:
            latest = m
    if latest == 0.0:
        return None
    return dt.datetime.fromtimestamp(latest, tz=dt.timezone.utc)


def _alive_status(last: dt.datetime | None) -> str:
    if last is None:
        return "gray"
    age = (dt.datetime.now(dt.timezone.utc) - last).total_seconds()
    if age < 120:
        return "green"
    if age < 1800:
        return "yellow"
    return "gray"


def _credit_today(ledger_path: Path, default_budget: int = 2_000_000) -> dict:
    used = 0
    try:
        data = json.loads(ledger_path.read_text(encoding="utf-8"))
        today = dt.date.today().isoformat()
        used = int(data.get(today, {}).get("session", 0))
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"used": used, "budget": default_budget, "kind": "session"}


def _latest_belief(vault: Path) -> dict | None:
    best = None
    best_rev = ""
    for p in _kind_files(vault, "beliefs"):
        fm, body = _parse_note(p)
        rev = str(fm.get("last_revised", fm.get("created", "")))
        if rev >= best_rev:
            best_rev = rev
            best = (fm, body, p)
    if best is None:
        return None
    fm, body, p = best
    headline = body.lstrip().split("\n", 1)[0].lstrip("# ").strip()
    try:
        conf = float(fm.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "id": fm.get("id", p.stem),
        "headline": headline,
        "confidence": conf,
        "last_revised": str(fm.get("last_revised", "")),
    }


def _latest_journal(vault: Path) -> dict | None:
    entries = _kind_files(vault, "journal")
    if not entries:
        return None
    p = entries[-1]
    fm, body = _parse_note(p)
    # strip headline line from preview
    lines = body.strip().splitlines()
    body_lines = [l for l in lines if not l.startswith("#")]
    preview = " ".join(body_lines).strip()
    return {
        "id": fm.get("id", p.stem),
        "date": str(fm.get("date", "")),
        "preview": preview[:240],
    }


def _peer_activity(vault: Path, peer_vault: Path | None, peer_name: str | None) -> dict:
    base = {
        "reading_peer_now": False,
        "last_peer_read_ts": None,
        "last_peer_read_action": None,
        "peer_refs_in_vault": 0,
    }
    if peer_vault is None:
        return base
    log = vault / "_index" / "peer_reads.jsonl"
    if log.exists():
        try:
            lines = log.read_text(encoding="utf-8").splitlines()
            if lines:
                entry = json.loads(lines[-1])
                base["last_peer_read_ts"] = entry.get("ts")
                base["last_peer_read_action"] = f"{entry.get('action','')} {entry.get('detail','')}".strip()
                try:
                    t = dt.datetime.fromisoformat(str(base["last_peer_read_ts"]).replace("Z", "+00:00"))
                    base["reading_peer_now"] = (dt.datetime.now(dt.timezone.utc) - t).total_seconds() < 30
                except ValueError:
                    pass
        except (OSError, json.JSONDecodeError):
            pass
    # cross-references: count of notes in this vault whose body mentions peer_name
    if peer_name:
        refs = 0
        for kind in ("beliefs", "questions", "tensions", "goals", "journal"):
            for p in _kind_files(vault, kind):
                try:
                    if peer_name in p.read_text(encoding="utf-8"):
                        refs += 1
                except OSError:
                    pass
        base["peer_refs_in_vault"] = refs
    return base


NOTES_TO_CHICHI_TEMPLATE = """# Notes to Chichi

A channel for me to write to Chichi when I want to ask for something or
say something I'd like them to read. Append-only. Date-stamped entries.

Format is loose: a date heading, then plain prose. No required schema.
Use it when there's a real wish or a real notice. Otherwise leave it alone.

---
"""


def _artifacts(agent: AgentInfo) -> list[dict]:
    """Files the agent has built outside the standard vault flow:
    .tmp/* (excluding the staged-source dump), vault/_artifacts/*, and any
    new top-level files in the project root that aren't part of the scaffold."""
    items: list[dict] = []
    project = agent.path
    skip_root = {".env", ".env.example", ".gitignore", "CLAUDE.md", "wob",
                 "notes-to-chichi.md", ".credit_ledger.json", ".credit_ledger.json.tmp"}
    skip_dirs = {"vault", "tools", "tasks", "workflows", ".tmp"}

    # 1. .tmp/ contents (excluding the agent's fetched-source staging area)
    tmp = project / ".tmp"
    if tmp.exists():
        for p in tmp.rglob("*"):
            if not p.is_file(): continue
            rel_parts = p.relative_to(project).parts
            if "sources" in rel_parts: continue
            try: st = p.stat()
            except OSError: continue
            items.append({
                "path": str(p.relative_to(project)),
                "kind": "scratch",
                "size": st.st_size,
                "mtime": dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc).isoformat(),
            })

    # 2. vault/_artifacts/ — visualizations, plots, any agent-generated artifact
    artifacts_dir = agent.vault / "_artifacts"
    if artifacts_dir.exists():
        for p in artifacts_dir.rglob("*"):
            if not p.is_file(): continue
            try: st = p.stat()
            except OSError: continue
            items.append({
                "path": str(p.relative_to(project)),
                "kind": "artifact",
                "size": st.st_size,
                "mtime": dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc).isoformat(),
            })

    # 3. Any new files at project root that aren't part of the scaffold
    for p in project.iterdir():
        if not p.is_file(): continue
        if p.name in skip_root: continue
        try: st = p.stat()
        except OSError: continue
        items.append({
            "path": p.name,
            "kind": "root-file",
            "size": st.st_size,
            "mtime": dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc).isoformat(),
        })

    # 4. Any new subdirectories at project root that aren't scaffold (e.g. site/)
    for p in project.iterdir():
        if not p.is_dir(): continue
        if p.name in skip_dirs: continue
        if p.name.startswith("."): continue
        for f in p.rglob("*"):
            if not f.is_file(): continue
            try: st = f.stat()
            except OSError: continue
            items.append({
                "path": str(f.relative_to(project)),
                "kind": "root-dir",
                "size": st.st_size,
                "mtime": dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc).isoformat(),
            })

    items.sort(key=lambda i: i["mtime"], reverse=True)
    return items


def _pause_status(agent: AgentInfo) -> dict:
    """Read vault/_pause.md if present. Auto-clears the file if expired."""
    p = agent.vault / "_pause.md"
    if not p.exists():
        return {"paused": False}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {"paused": False}
    m = FM_RE.match(text) if (m := re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)) else None
    fm: dict = {}
    if m:
        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            fm = {}
    expires_str = fm.get("expires_at")
    paused_at = fm.get("paused_at")
    rm = re.search(r"\*\*Reason:\*\*\s*(.+)", text)
    reason = rm.group(1).strip() if rm else ""
    if expires_str and str(expires_str).lower() not in ("null", "none", ""):
        try:
            expires = dt.datetime.fromisoformat(str(expires_str))
            now = dt.datetime.now(dt.timezone.utc)
            if now >= expires:
                # auto-clear expired pause
                try: p.unlink()
                except OSError: pass
                return {"paused": False, "just_expired_at": str(expires_str)}
            return {
                "paused": True,
                "paused_at": str(paused_at) if paused_at else None,
                "expires_at": str(expires_str),
                "reason": reason[:200],
                "indefinite": False,
            }
        except ValueError:
            pass
    return {
        "paused": True,
        "paused_at": str(paused_at) if paused_at else None,
        "expires_at": None,
        "reason": reason[:200],
        "indefinite": True,
    }


def _notes_status(agent: AgentInfo) -> dict:
    p = agent.path / "notes-to-chichi.md"
    if not p.exists():
        return {"exists": False, "has_content": False, "size": 0, "mtime": None}
    try:
        st = p.stat()
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {"exists": True, "has_content": False, "size": 0, "mtime": None}
    # Strip the seeded template if present, then check for substantive content.
    # If the agent has rewritten the template entirely, treat the whole file
    # as content beyond the first 50 chars.
    if text.startswith(NOTES_TO_CHICHI_TEMPLATE):
        after = text[len(NOTES_TO_CHICHI_TEMPLATE):].strip()
    else:
        # Heuristic: split on `---` and take everything after the last divider
        parts = text.split("---")
        after = parts[-1].strip() if len(parts) >= 2 else text.strip()
    has_content = len(after) > 5
    return {
        "exists": True,
        "has_content": has_content,
        "size": st.st_size,
        "mtime": dt.datetime.fromtimestamp(st.st_mtime, tz=dt.timezone.utc).isoformat(),
        "preview": after[:240] if has_content else "",
    }


def snapshot(agent: AgentInfo) -> dict:
    stats = {kind: len(_kind_files(agent.vault, kind)) for kind in KIND_DIRS}
    last = _latest_activity(agent.vault)
    arts = _artifacts(agent)
    notes = _notes_status(agent)
    pause = _pause_status(agent)
    # consider artifact mtimes when computing alive status
    if arts:
        try:
            art_latest = max(dt.datetime.fromisoformat(a["mtime"]) for a in arts)
            if last is None or art_latest > last:
                last = art_latest
        except ValueError:
            pass
    return {
        "name": agent.name,
        "path": str(agent.path),
        "peer": agent.peer_name,
        "last_activity": last.isoformat() if last else None,
        "alive_status": "paused" if pause.get("paused") else _alive_status(last),
        "pause": pause,
        "credit": _credit_today(agent.credit_ledger),
        "stats": {**stats, "artifacts": len(arts)},
        "latest_belief": _latest_belief(agent.vault),
        "latest_journal": _latest_journal(agent.vault),
        "peer_activity": _peer_activity(agent.vault, agent.peer_vault, agent.peer_name),
        "artifacts": arts[:20],   # most recent 20
        "notes_to_chichi": notes,
    }


WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
SECTION_RE = re.compile(r"^\*\*([^*]+):\*\*\s*$")

PREFIX_TO_KIND = {"b": "beliefs", "q": "questions", "s": "sources", "t": "tensions", "g": "goals", "j": "journal"}


def _find_note(vault: Path, note_id: str) -> Path | None:
    """Resolve a note id (e.g. 'b-2026-04-24-001') to a vault file path."""
    if not note_id or "-" not in note_id:
        return None
    prefix = note_id.split("-", 1)[0]
    kind = PREFIX_TO_KIND.get(prefix)
    if not kind:
        return None
    p = vault / kind / f"{note_id}.md"
    return p if p.exists() else None


def _find_note_anywhere(agents: list[AgentInfo], agent: AgentInfo, note_id: str) -> tuple[Path | None, AgentInfo | None]:
    """Try the agent's own vault first, then peer's. Lets wikilinks in paired
    agents transparently resolve to the peer's vault."""
    p = _find_note(agent.vault, note_id)
    if p is not None:
        return p, agent
    if agent.peer_name:
        peer = next((a for a in agents if a.name == agent.peer_name), None)
        if peer is not None:
            p = _find_note(peer.vault, note_id)
            if p is not None:
                return p, peer
    return None, None


def _build_graph(agent: AgentInfo) -> dict:
    """Nodes = all schema-bound notes in this agent's vault. Edges = wikilinks
    between them, kind-classified by the body section the link appears in
    (Supports → supports, In tension with → tension, Sources → source)."""
    nodes: list[dict] = []
    edges: list[dict] = []
    node_ids: set[str] = set()

    for kind in KIND_DIRS:
        for p in _kind_files(agent.vault, kind):
            fm, body = _parse_note(p)
            nid = fm.get("id", p.stem)
            node_ids.add(nid)
            headline = body.lstrip().split("\n", 1)[0].lstrip("# ").strip()[:140]
            node = {
                "id": nid,
                "kind": kind.rstrip("s") if kind != "journal" else "journal",
                "headline": headline,
                "status": fm.get("status", "active"),
            }
            if kind == "beliefs":
                try:
                    node["confidence"] = float(fm.get("confidence", 0.0))
                except (TypeError, ValueError):
                    node["confidence"] = 0.0
                node["centrality"] = fm.get("centrality", "peripheral")
            nodes.append(node)

            # Parse body line-by-line, tracking which section each link is in
            section: str | None = None
            for line in body.splitlines():
                s = SECTION_RE.match(line.strip())
                if s:
                    section = s.group(1).strip().lower()
                    continue
                for m in WIKILINK_RE.finditer(line):
                    target = m.group(1).split("/")[-1].removesuffix(".md").strip()
                    if not target or target == nid:
                        continue
                    if section == "supports":
                        edge_kind = "supports"
                    elif section and section.startswith("in tension"):
                        edge_kind = "tension"
                    elif section == "sources":
                        edge_kind = "source"
                    elif section == "revision log":
                        continue  # historical references aren't edges
                    else:
                        edge_kind = "reference"
                    edges.append({"source": nid, "target": target, "kind": edge_kind})

    # Drop edges pointing at nodes that don't exist (e.g. stale or peer-pointers)
    edges = [e for e in edges if e["target"] in node_ids]

    # Degree counts for centrality hints
    degree: dict[str, int] = {n["id"]: 0 for n in nodes}
    for e in edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    for n in nodes:
        n["degree"] = degree.get(n["id"], 0)

    return {
        "agent": agent.name,
        "peer": agent.peer_name,
        "nodes": nodes,
        "edges": edges,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def recent_events(agents: list[AgentInfo], n: int = 30) -> list[dict]:
    events: list[dict] = []
    now = dt.datetime.now(dt.timezone.utc)
    day_ago = now - dt.timedelta(days=1)
    for a in agents:
        log = a.vault / "_index" / "peer_reads.jsonl"
        if log.exists():
            try:
                for line in log.read_text(encoding="utf-8").splitlines()[-50:]:
                    try:
                        e = json.loads(line)
                        events.append({
                            "ts": e.get("ts"),
                            "agent": e.get("agent") or a.name,
                            "kind": f"peer_read → {e.get('peer', '?')}",
                            "detail": f"{e.get('action','')} {e.get('detail','')}".strip(),
                        })
                    except json.JSONDecodeError:
                        pass
            except OSError:
                pass
        # note activity (last 24h by mtime)
        for kind in KIND_DIRS:
            for p in _kind_files(a.vault, kind):
                try:
                    t = dt.datetime.fromtimestamp(p.stat().st_mtime, tz=dt.timezone.utc)
                except OSError:
                    continue
                if t < day_ago:
                    continue
                fm, body = _parse_note(p)
                headline = body.lstrip().split("\n", 1)[0].lstrip("# ").strip()[:100]
                events.append({
                    "ts": t.isoformat(),
                    "agent": a.name,
                    "kind": f"note:{kind}",
                    "detail": f"{fm.get('id', p.stem)} — {headline}",
                })
        # artifact creation events (built things, last 24h)
        for art in _artifacts(a):
            try:
                t = dt.datetime.fromisoformat(art["mtime"])
            except ValueError:
                continue
            if t < day_ago: continue
            events.append({
                "ts": art["mtime"],
                "agent": a.name,
                "kind": f"built:{art['kind']}",
                "detail": f"{art['path']} ({art['size']:,} bytes)",
            })
        # notes-to-chichi update event (if non-empty and recent)
        ns = _notes_status(a)
        if ns.get("has_content") and ns.get("mtime"):
            try:
                t = dt.datetime.fromisoformat(ns["mtime"])
                if t >= day_ago:
                    events.append({
                        "ts": ns["mtime"],
                        "agent": a.name,
                        "kind": "→ chichi",
                        "detail": ns.get("preview", "")[:120],
                    })
            except ValueError:
                pass
    events.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return events[:n]


# --- HTTP server -----------------------------------------------------------

SCAN_ROOT: Path = Path.home() / "Desktop" / "CLAUDE CODE"


def build_payload() -> dict:
    agents = discover_agents(SCAN_ROOT)
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scan_root": str(SCAN_ROOT),
        "agents": [snapshot(a) for a in agents],
        "recent_events": recent_events(agents),
    }


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Web of Belief — live</title>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
  :root {
    --bg: #0e1116;
    --bg-card: #161b22;
    --bg-card-hover: #1c2129;
    --border: #30363d;
    --text: #e6edf3;
    --text-dim: #8b949e;
    --text-dimmer: #6e7681;
    --green: #3fb950;
    --yellow: #d29922;
    --gray: #6e7681;
    --accent: #58a6ff;
    --danger: #f85149;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    min-height: 100vh;
  }
  header {
    display: flex; justify-content: space-between; align-items: baseline;
    padding: 1rem 2rem;
    border-bottom: 1px solid var(--border);
    position: sticky; top: 0;
    background: var(--bg);
    z-index: 10;
  }
  header h1 { font-size: 1rem; font-weight: 500; margin: 0; letter-spacing: 0.01em; }
  header .meta { color: var(--text-dim); font-size: 0.85rem; font-variant-numeric: tabular-nums; }
  header .conn-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--gray); margin-left: 0.5rem; vertical-align: middle; }
  main { padding: 1.5rem 2rem 3rem; max-width: 1400px; margin: 0 auto; }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 1rem;
    margin-bottom: 1rem;
  }
  .card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 1rem;
    transition: background 0.2s, border-color 0.2s;
    cursor: pointer;
    position: relative;
  }
  .card:hover { background: var(--bg-card-hover); border-color: var(--accent); }
  .card::after {
    content: "graph ↗";
    position: absolute;
    top: 0.6rem; right: 0.75rem;
    font-size: 0.7rem;
    color: var(--text-dimmer);
    opacity: 0;
    transition: opacity 0.2s;
    pointer-events: none;
  }
  .card:hover::after { opacity: 1; }
  .card-head { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.75rem; }
  .card-head .name { font-weight: 600; font-size: 0.95rem; }
  .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: var(--gray); }
  .dot.green { background: var(--green); box-shadow: 0 0 8px rgba(63, 185, 80, 0.6); animation: pulse-green 2s ease-in-out infinite; }
  .dot.yellow { background: var(--yellow); }
  .dot.gray { background: var(--gray); }
  .dot.paused { background: #bc8cff; box-shadow: 0 0 6px rgba(188, 140, 255, 0.5); }
  .pause-badge {
    background: rgba(188, 140, 255, 0.18);
    color: #bc8cff;
    border: 1px solid rgba(188, 140, 255, 0.4);
    padding: 0.05rem 0.45rem;
    border-radius: 3px;
    font-size: 0.7rem;
    font-weight: 500;
    cursor: pointer;
    margin-left: 0.25rem;
    font-variant-numeric: tabular-nums;
  }
  .pause-badge:hover { background: rgba(188, 140, 255, 0.32); }
  @keyframes pulse-green {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
  }
  .card-head .ts { color: var(--text-dim); font-size: 0.8rem; margin-left: auto; font-variant-numeric: tabular-nums; }
  .bar-label { font-size: 0.72rem; color: var(--text-dim); display: flex; justify-content: space-between; margin-bottom: 0.2rem; font-variant-numeric: tabular-nums; }
  .bar { height: 4px; background: var(--border); border-radius: 2px; overflow: hidden; margin: 0 0 0.65rem; }
  .bar .fill { height: 100%; background: var(--accent); transition: width 0.4s; }
  .stats {
    display: grid; grid-template-columns: repeat(4, 1fr);
    gap: 0.35rem 0.5rem;
    font-size: 0.78rem;
    margin: 0.75rem 0;
    padding: 0.6rem 0;
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
  }
  .chichi-badge {
    background: rgba(210, 153, 34, 0.18);
    color: var(--yellow);
    border: 1px solid rgba(210, 153, 34, 0.4);
    padding: 0.05rem 0.45rem;
    border-radius: 3px;
    font-size: 0.7rem;
    font-weight: 500;
    cursor: pointer;
    margin-left: 0.25rem;
    transition: background 0.2s;
  }
  .chichi-badge:hover { background: rgba(210, 153, 34, 0.32); }
  .feed-row[data-clickable="true"] { cursor: pointer; }
  .feed-row[data-clickable="true"]:hover .detail { color: var(--accent); }
  .stat { color: var(--text-dim); }
  .stat b { color: var(--text); font-weight: 600; margin-right: 0.3em; font-variant-numeric: tabular-nums; }
  .latest { font-size: 0.85rem; color: var(--text-dim); line-height: 1.4; margin-top: 0.5rem; }
  .latest .label { color: var(--text-dimmer); font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.2rem; font-weight: 500; }
  .latest .headline { color: var(--text); font-style: italic; }
  .conf { color: var(--accent); font-variant-numeric: tabular-nums; font-style: normal; margin-left: 0.5em; }
  .peer-row {
    display: grid;
    grid-template-columns: 1fr 180px 1fr;
    gap: 1rem;
    align-items: stretch;
    margin-bottom: 1rem;
  }
  .peer-connector {
    display: flex; align-items: center; justify-content: center; text-align: center;
    padding: 1rem 0.5rem;
    font-size: 0.82rem;
    color: var(--text-dimmer);
    border: 1px dashed var(--border);
    border-radius: 6px;
    background: transparent;
    transition: all 0.3s;
  }
  .peer-connector.active {
    color: var(--green);
    border-color: var(--green);
    border-style: solid;
    background: rgba(63, 185, 80, 0.06);
    animation: peer-pulse 1.4s ease-in-out infinite;
  }
  @keyframes peer-pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(63, 185, 80, 0.35); }
    50% { box-shadow: 0 0 0 10px rgba(63, 185, 80, 0); }
  }
  h2 { font-size: 0.75rem; color: var(--text-dim); text-transform: uppercase; letter-spacing: 0.1em; margin: 2rem 0 0.75rem; font-weight: 600; }
  .feed {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.25rem 0;
    max-height: 380px;
    overflow-y: auto;
  }
  .feed-row {
    display: grid; grid-template-columns: 85px 110px 170px 1fr;
    gap: 0.75rem;
    padding: 0.4rem 1rem;
    font-size: 0.8rem;
    border-bottom: 1px solid transparent;
    font-variant-numeric: tabular-nums;
    align-items: baseline;
  }
  .feed-row:hover { background: var(--bg-card-hover); }
  .feed-row .time { color: var(--text-dimmer); }
  .feed-row .agent { color: var(--accent); font-weight: 500; }
  .feed-row .kind { color: var(--text-dim); font-size: 0.75rem; }
  .feed-row .detail { color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .empty { color: var(--text-dimmer); font-style: italic; padding: 1rem; text-align: center; font-size: 0.85rem; }
  @media (max-width: 860px) {
    .peer-row { grid-template-columns: 1fr; }
    .peer-connector { padding: 0.5rem; }
    header { padding: 1rem; }
    main { padding: 1rem; }
  }

  /* --- graph modal --- */
  .modal-backdrop {
    position: fixed; inset: 0;
    background: rgba(0, 0, 0, 0.75);
    display: none;
    z-index: 100;
    backdrop-filter: blur(2px);
  }
  .modal-backdrop.open { display: flex; }
  .modal {
    margin: auto;
    width: 92vw; height: 88vh; max-width: 1400px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    display: flex; flex-direction: column;
    overflow: hidden;
  }
  .modal-head {
    display: flex; align-items: center; gap: 1rem;
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border);
  }
  .modal-head .graph-title { font-weight: 600; font-size: 0.95rem; }
  .modal-head .graph-meta { color: var(--text-dim); font-size: 0.8rem; }
  .modal-head .close {
    margin-left: auto;
    background: transparent; border: 1px solid var(--border);
    color: var(--text-dim);
    padding: 0.25rem 0.6rem;
    border-radius: 4px;
    cursor: pointer; font-size: 0.85rem;
  }
  .modal-head .close:hover { color: var(--text); border-color: var(--text-dim); }
  .modal-body {
    flex: 1; display: flex; min-height: 0;
  }
  .graph-container { flex: 1; background: #0a0d12; position: relative; overflow: hidden; }
  .graph-container svg { display: block; width: 100%; height: 100%; }
  .legend {
    width: 180px;
    border-left: 1px solid var(--border);
    padding: 1rem;
    background: var(--bg-card);
    overflow-y: auto;
    font-size: 0.8rem;
  }
  .legend h3 { font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-dim); margin: 0 0 0.5rem; font-weight: 600; }
  .legend-item { display: flex; align-items: center; gap: 0.5rem; padding: 0.2rem 0; color: var(--text-dim); }
  .legend-swatch { width: 14px; height: 14px; border-radius: 50%; flex: 0 0 auto; }
  .legend-line { width: 20px; height: 0; border-top: 2px solid; }
  .legend .stats-summary { margin-top: 1rem; padding-top: 0.75rem; border-top: 1px solid var(--border); font-variant-numeric: tabular-nums; color: var(--text); }
  .legend .hint { margin-top: 0.75rem; color: var(--text-dimmer); font-size: 0.72rem; line-height: 1.4; }
  .node-label { font-size: 9px; fill: var(--text); pointer-events: none; user-select: none; }
  .node-label.hidden { display: none; }

  /* --- note side panel --- */
  .note-panel {
    position: fixed;
    top: 0; right: 0; bottom: 0;
    width: 520px; max-width: 100vw;
    background: var(--bg-card);
    border-left: 1px solid var(--border);
    transform: translateX(100%);
    transition: transform 0.22s ease-out;
    z-index: 200;
    display: flex; flex-direction: column;
    overflow: hidden;
    box-shadow: -8px 0 24px rgba(0, 0, 0, 0.4);
  }
  .note-panel.open { transform: translateX(0); }
  .note-panel-head {
    display: flex; align-items: baseline; gap: 0.75rem;
    padding: 0.85rem 1rem;
    border-bottom: 1px solid var(--border);
    flex: 0 0 auto;
  }
  .note-panel-head .note-id { font-family: ui-monospace, "SF Mono", Menlo, monospace; color: var(--text-dim); font-size: 0.78rem; }
  .note-panel-head .note-kind {
    font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--bg);
    background: var(--accent);
    padding: 0.1rem 0.5rem;
    border-radius: 3px;
    font-weight: 600;
  }
  .note-panel-head .note-kind.belief { background: #58a6ff; }
  .note-panel-head .note-kind.question { background: #bc8cff; }
  .note-panel-head .note-kind.source { background: #8b949e; }
  .note-panel-head .note-kind.tension { background: #f85149; }
  .note-panel-head .note-kind.goal { background: #3fb950; }
  .note-panel-head .note-kind.journal { background: #d29922; }
  .note-panel-head .peer-tag {
    font-size: 0.7rem; color: var(--text-dim);
    background: rgba(139, 148, 158, 0.15);
    border: 1px solid var(--border);
    padding: 0.1rem 0.4rem; border-radius: 3px;
  }
  .note-panel-head .close-note {
    margin-left: auto;
    background: transparent; border: 1px solid var(--border); color: var(--text-dim);
    padding: 0.2rem 0.55rem; border-radius: 4px; cursor: pointer; font-size: 1rem; line-height: 1;
  }
  .note-panel-head .close-note:hover { color: var(--text); border-color: var(--text-dim); }
  .note-meta {
    padding: 0.5rem 1rem;
    background: rgba(88, 166, 255, 0.04);
    border-bottom: 1px solid var(--border);
    font-size: 0.78rem;
    color: var(--text-dim);
    display: flex; flex-wrap: wrap; gap: 0.75rem;
    font-variant-numeric: tabular-nums;
  }
  .note-meta b { color: var(--text); font-weight: 500; margin-right: 0.25em; }
  .note-body {
    flex: 1; overflow-y: auto;
    padding: 1rem 1.25rem 2rem;
    font-size: 0.92rem;
    line-height: 1.65;
    color: var(--text);
  }
  .note-body h1 { font-size: 1.25rem; margin: 0 0 1rem; font-weight: 600; line-height: 1.3; }
  .note-body h2 { font-size: 1rem; margin: 1.25rem 0 0.5rem; font-weight: 600; }
  .note-body h3 { font-size: 0.9rem; margin: 1rem 0 0.4rem; font-weight: 600; }
  .note-body p { margin: 0.6rem 0; }
  .note-body strong { color: var(--text); font-weight: 600; }
  .note-body em { color: var(--text); }
  .note-body ul { margin: 0.4rem 0; padding-left: 1.25rem; }
  .note-body li { margin: 0.2rem 0; }
  .note-body a.wikilink {
    color: var(--accent);
    text-decoration: none;
    cursor: pointer;
    border-bottom: 1px dashed rgba(88, 166, 255, 0.4);
  }
  .note-body a.wikilink:hover { border-bottom-style: solid; background: rgba(88, 166, 255, 0.08); }
  .note-body .section-label { color: var(--text-dimmer); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; margin-top: 1rem; }
  .note-body .empty { color: var(--text-dimmer); font-style: italic; }
  .note-history-back {
    border-top: 1px solid var(--border);
    padding: 0.5rem 1rem;
    background: var(--bg-card);
    font-size: 0.78rem;
    color: var(--text-dim);
    display: none;
  }
  .note-history-back.visible { display: block; }
  .note-history-back button {
    background: transparent; border: 1px solid var(--border); color: var(--text-dim);
    padding: 0.15rem 0.5rem; border-radius: 3px; cursor: pointer; font-size: 0.78rem;
  }
  .note-history-back button:hover { color: var(--text); border-color: var(--text-dim); }
  @media (max-width: 600px) { .note-panel { width: 100vw; } }
</style>
</head>
<body>
<header>
  <h1>Web of Belief — live status</h1>
  <div class="meta">
    <span id="generated">—</span>
    <span id="conn" class="conn-dot" title="connecting…"></span>
  </div>
</header>
<main>
  <div id="solo-grid" class="grid"></div>
  <div id="paired-section"></div>
  <h2>Recent activity (last 24h)</h2>
  <div id="feed" class="feed"><div class="empty">waiting for events…</div></div>
</main>

<aside id="note-panel" class="note-panel" role="dialog" aria-modal="false" aria-label="Note detail">
  <div class="note-panel-head">
    <span class="note-kind" id="note-kind">—</span>
    <span class="note-id" id="note-id">—</span>
    <span class="peer-tag" id="note-peer-tag" style="display:none">peer</span>
    <button class="close-note" id="note-close" title="close (esc)">×</button>
  </div>
  <div class="note-meta" id="note-meta"></div>
  <div class="note-body" id="note-body"><div class="empty">loading…</div></div>
  <div class="note-history-back" id="note-history-back">
    <button id="note-back">← back</button>
  </div>
</aside>

<div id="graph-modal" class="modal-backdrop" role="dialog" aria-modal="true">
  <div class="modal">
    <div class="modal-head">
      <div class="graph-title">—</div>
      <div class="graph-meta"></div>
      <button class="close" id="graph-close">close (esc)</button>
    </div>
    <div class="modal-body">
      <div class="graph-container" id="graph-container"></div>
      <div class="legend">
        <h3>Node kind</h3>
        <div class="legend-item"><span class="legend-swatch" style="background:#58a6ff"></span>belief</div>
        <div class="legend-item"><span class="legend-swatch" style="background:#bc8cff"></span>question</div>
        <div class="legend-item"><span class="legend-swatch" style="background:#8b949e"></span>source</div>
        <div class="legend-item"><span class="legend-swatch" style="background:#f85149"></span>tension</div>
        <div class="legend-item"><span class="legend-swatch" style="background:#3fb950"></span>goal</div>
        <h3 style="margin-top:1rem">Edge kind</h3>
        <div class="legend-item"><span class="legend-line" style="border-color:#58a6ff"></span>supports</div>
        <div class="legend-item"><span class="legend-line" style="border-color:#f85149; border-style:dashed"></span>tension</div>
        <div class="legend-item"><span class="legend-line" style="border-color:#8b949e"></span>source-ref</div>
        <div class="stats-summary" id="graph-summary">—</div>
        <div class="hint">Drag nodes to rearrange. Scroll to zoom. Hover for details.</div>
      </div>
    </div>
  </div>
</div>
<script>
const POLL_MS = 3000;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function fmtAgo(iso) {
  if (!iso) return 'never';
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 0) return 'just now';
  if (s < 5) return 'just now';
  if (s < 60) return s + 's ago';
  if (s < 3600) return Math.round(s / 60) + 'm ago';
  if (s < 86400) return Math.round(s / 3600) + 'h ago';
  return Math.round(s / 86400) + 'd ago';
}

function fmtTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function fmtUntil(iso) {
  if (!iso) return '';
  const s = Math.round((new Date(iso).getTime() - Date.now()) / 1000);
  if (s <= 0) return 'expired';
  if (s < 3600) return Math.round(s / 60) + 'm';
  if (s < 86400) return Math.round(s / 3600) + 'h';
  return Math.round(s / 86400) + 'd';
}

function fmtPct(used, budget) {
  if (!budget) return '0%';
  return (Math.round((used / budget) * 1000) / 10) + '%';
}

function cardHTML(a) {
  const c = a.credit;
  const lb = a.latest_belief;
  const lj = a.latest_journal;
  const pa = a.peer_activity;
  const pctWidth = Math.min(100, (c.used / Math.max(1, c.budget)) * 100);
  return `
    <div class="card" data-agent="${esc(a.name)}" onclick="openGraph('${esc(a.name)}')">
      <div class="card-head">
        <span class="dot ${esc(a.alive_status)}"></span>
        <span class="name">${esc(a.name)}</span>
        ${a.pause && a.pause.paused ? `
          <span class="pause-badge" title="${esc(a.pause.reason || 'paused')}"
                onclick="event.stopPropagation(); openFile('${esc(a.name)}', 'vault/_pause.md')">paused${a.pause.expires_at ? ' · ' + fmtUntil(a.pause.expires_at) : ''}</span>
        ` : ''}
        ${a.notes_to_chichi && a.notes_to_chichi.has_content ? `
          <span class="chichi-badge" title="open notes-to-chichi.md"
                onclick="event.stopPropagation(); openFile('${esc(a.name)}', 'notes-to-chichi.md')">→ chichi</span>
        ` : ''}
        <span class="ts">${esc(fmtAgo(a.last_activity))}</span>
      </div>
      <div class="bar-label">
        <span>credit (session)</span>
        <span>${c.used.toLocaleString()} / ${c.budget.toLocaleString()} · ${fmtPct(c.used, c.budget)}</span>
      </div>
      <div class="bar"><div class="fill" style="width: ${pctWidth}%"></div></div>
      <div class="stats">
        <div class="stat"><b>${a.stats.beliefs || 0}</b>beliefs</div>
        <div class="stat"><b>${a.stats.questions || 0}</b>questions</div>
        <div class="stat"><b>${a.stats.sources || 0}</b>sources</div>
        <div class="stat"><b>${a.stats.tensions || 0}</b>tensions</div>
        <div class="stat"><b>${a.stats.goals || 0}</b>goals</div>
        <div class="stat"><b>${a.stats.journal || 0}</b>journals</div>
        <div class="stat" title="things they have built outside the vault"><b>${a.stats.artifacts || 0}</b>built</div>
      </div>
      ${lb ? `
        <div class="latest" style="cursor:pointer" title="open belief"
             onclick="event.stopPropagation(); openNote('${esc(a.name)}', '${esc(lb.id)}')">
          <div class="label">latest belief <span class="conf">conf ${lb.confidence.toFixed(2)}</span></div>
          <div class="headline">"${esc(lb.headline)}"</div>
        </div>
      ` : `<div class="latest"><div class="label">latest belief</div><div style="color: var(--text-dimmer); font-style: italic">none yet</div></div>`}
      ${lj ? `
        <div class="latest" style="cursor:pointer" title="open journal"
             onclick="event.stopPropagation(); openNote('${esc(a.name)}', '${esc(lj.id)}')">
          <div class="label">latest journal — ${esc(lj.date)}</div>
          <div>${esc(lj.preview) || '—'}</div>
        </div>
      ` : ''}
      ${a.peer ? `
        <div class="latest">
          <div class="label">peer</div>
          <div>${esc(a.peer)} · ${pa.peer_refs_in_vault} refs in my vault${pa.last_peer_read_ts ? ` · last read ${esc(fmtAgo(pa.last_peer_read_ts))}` : ''}</div>
        </div>
      ` : ''}
    </div>
  `;
}

function pairedRowHTML(a, b) {
  const aReading = a.peer_activity.reading_peer_now;
  const bReading = b.peer_activity.reading_peer_now;
  const connected = aReading || bReading;
  let msg;
  if (aReading && bReading) msg = `${a.name} ↔ ${b.name}<br><small style="color: var(--text-dim)">reading each other</small>`;
  else if (aReading) msg = `${a.name} → ${b.name}<br><small style="color: var(--text-dim)">reading peer</small>`;
  else if (bReading) msg = `${a.name} ← ${b.name}<br><small style="color: var(--text-dim)">reading peer</small>`;
  else msg = `${esc(a.name)} · ${esc(b.name)}<br><small style="color: var(--text-dimmer)">quiet</small>`;
  return `
    <div class="peer-row">
      ${cardHTML(a)}
      <div class="peer-connector ${connected ? 'active' : ''}">${msg}</div>
      ${cardHTML(b)}
    </div>
  `;
}

function feedHTML(events) {
  if (!events || !events.length) return '<div class="empty">no recent events</div>';
  return events.map(e => {
    const kind = (e.kind || '').replace('note:', '');
    let clickable = '';
    if (e.kind && e.kind.startsWith('built:')) {
      // detail is "<path> (N bytes)" — extract the path
      const m = (e.detail || '').match(/^(.+?) \(/);
      const path = m ? m[1] : (e.detail || '');
      clickable = `data-clickable="true" onclick="openFile('${esc(e.agent)}', '${esc(path)}')"`;
    } else if (e.kind === '→ chichi') {
      clickable = `data-clickable="true" onclick="openFile('${esc(e.agent)}', 'notes-to-chichi.md')"`;
    } else if (e.kind && e.kind.startsWith('note:')) {
      // existing vault notes — open via /api/note
      const m = (e.detail || '').match(/^([a-z]-\d{4}-\d{2}-\d{2}-\d{3})/);
      if (m) clickable = `data-clickable="true" onclick="openNote('${esc(e.agent)}', '${esc(m[1])}')"`;
    }
    return `
      <div class="feed-row" ${clickable}>
        <div class="time">${esc(fmtTime(e.ts))}</div>
        <div class="agent">${esc(e.agent || '—')}</div>
        <div class="kind">${esc(kind)}</div>
        <div class="detail" title="${esc(e.detail || '')}">${esc(e.detail || '—')}</div>
      </div>
    `;
  }).join('');
}

async function render() {
  try {
    const r = await fetch('/api/status', { cache: 'no-store' });
    if (!r.ok) throw new Error('http ' + r.status);
    const d = await r.json();
    document.getElementById('generated').textContent = 'updated ' + fmtTime(d.generated_at);
    const cd = document.getElementById('conn');
    cd.style.background = 'var(--green)';
    cd.title = 'connected — scan root: ' + (d.scan_root || '?');

    const solos = d.agents.filter(a => !a.peer);
    const paired = d.agents.filter(a => a.peer);

    document.getElementById('solo-grid').innerHTML = solos.length ? solos.map(cardHTML).join('') : '<div class="empty">no solo agents discovered</div>';

    const ps = document.getElementById('paired-section');
    if (paired.length === 2) {
      ps.innerHTML = pairedRowHTML(paired[0], paired[1]);
    } else if (paired.length > 2) {
      // More than one pair: chunk by 2
      const rows = [];
      for (let i = 0; i + 1 < paired.length; i += 2) {
        rows.push(pairedRowHTML(paired[i], paired[i+1]));
      }
      if (paired.length % 2) rows.push(`<div class="grid">${cardHTML(paired[paired.length - 1])}</div>`);
      ps.innerHTML = rows.join('');
    } else if (paired.length === 1) {
      ps.innerHTML = `<div class="grid">${cardHTML(paired[0])}</div>`;
    } else {
      ps.innerHTML = '';
    }

    document.getElementById('feed').innerHTML = feedHTML(d.recent_events);
  } catch (e) {
    const cd = document.getElementById('conn');
    cd.style.background = 'var(--danger)';
    cd.title = 'disconnected: ' + e.message;
  }
}

render();
setInterval(render, POLL_MS);

// --- graph modal ---------------------------------------------------------

const NODE_COLOR = {
  belief: '#58a6ff', question: '#bc8cff', source: '#8b949e',
  tension: '#f85149', goal: '#3fb950', journal: '#d29922'
};
const EDGE_COLOR = { supports: '#58a6ff', tension: '#f85149', source: '#8b949e', reference: '#6e7681' };

let currentGraphAgent = null;

async function openGraph(agentName) {
  const modal = document.getElementById('graph-modal');
  currentGraphAgent = agentName;
  modal.classList.add('open');
  modal.querySelector('.graph-title').textContent = agentName;
  modal.querySelector('.graph-meta').textContent = 'loading…';
  document.getElementById('graph-container').innerHTML = '';
  document.getElementById('graph-summary').textContent = '';
  try {
    const r = await fetch(`/api/graph/${encodeURIComponent(agentName)}`, { cache: 'no-store' });
    if (!r.ok) throw new Error('http ' + r.status);
    const d = await r.json();
    if (d.error) throw new Error(d.error);
    modal.querySelector('.graph-meta').textContent = `${d.nodes.length} nodes · ${d.edges.length} edges`;
    const counts = {};
    for (const n of d.nodes) counts[n.kind] = (counts[n.kind] || 0) + 1;
    const summary = Object.entries(counts).map(([k, v]) => `${v} ${k}${v === 1 ? '' : 's'}`).join(' · ');
    document.getElementById('graph-summary').textContent = summary || 'empty vault';
    renderGraph(d);
  } catch (e) {
    modal.querySelector('.graph-meta').textContent = 'error: ' + e.message;
  }
}

function closeGraph() {
  document.getElementById('graph-modal').classList.remove('open');
  document.getElementById('graph-container').innerHTML = '';
}

function renderGraph(data) {
  const container = document.getElementById('graph-container');
  const W = container.clientWidth || 800;
  const H = container.clientHeight || 600;

  const svg = d3.select(container).append('svg').attr('viewBox', `0 0 ${W} ${H}`);
  const root = svg.append('g');

  svg.call(d3.zoom().scaleExtent([0.2, 4]).on('zoom', (e) => root.attr('transform', e.transform)));

  if (!data.nodes.length) {
    svg.append('text').attr('x', W / 2).attr('y', H / 2).attr('text-anchor', 'middle')
      .attr('fill', '#6e7681').attr('font-style', 'italic').attr('font-size', 14)
      .text('no notes yet');
    return;
  }

  // Copy nodes/edges because d3 mutates them
  const nodes = data.nodes.map(n => ({ ...n }));
  const links = data.edges.map(e => ({ ...e }));

  const sim = d3.forceSimulation(nodes)
    .force('link', d3.forceLink(links).id(d => d.id).distance(90).strength(0.8))
    .force('charge', d3.forceManyBody().strength(-260))
    .force('center', d3.forceCenter(W / 2, H / 2))
    .force('collide', d3.forceCollide().radius(d => nodeRadius(d) + 6));

  const link = root.append('g').attr('stroke-opacity', 0.7).selectAll('line')
    .data(links).enter().append('line')
    .attr('stroke', d => EDGE_COLOR[d.kind] || '#30363d')
    .attr('stroke-width', 1.5)
    .attr('stroke-dasharray', d => d.kind === 'tension' ? '5,4' : null);

  let dragMoved = false;
  const node = root.append('g').selectAll('g')
    .data(nodes).enter().append('g')
    .style('cursor', 'pointer')
    .call(d3.drag()
      .on('start', (e, d) => { dragMoved = false; if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag', (e, d) => { dragMoved = true; d.fx = e.x; d.fy = e.y; })
      .on('end', (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }))
    .on('click', (e, d) => {
      if (dragMoved) return;          // don't trigger on drag-release
      e.stopPropagation();
      if (currentGraphAgent) openNote(currentGraphAgent, d.id);
    });

  node.append('circle')
    .attr('r', nodeRadius)
    .attr('fill', d => NODE_COLOR[d.kind] || '#6e7681')
    .attr('fill-opacity', d => d.status === 'active' || d.status === 'open' ? 1.0 : 0.35)
    .attr('stroke', '#0a0d12').attr('stroke-width', 2);

  node.append('text')
    .attr('class', 'node-label')
    .attr('dx', d => nodeRadius(d) + 4).attr('dy', 3)
    .text(d => {
      const h = d.headline || d.id;
      return h.length > 28 ? h.slice(0, 28) + '…' : h;
    });

  node.append('title').text(d => {
    const parts = [`${d.id} (${d.kind})`];
    if (d.headline) parts.push(d.headline);
    if (d.confidence != null) parts.push(`confidence ${d.confidence.toFixed(2)}`);
    if (d.centrality) parts.push(`centrality ${d.centrality}`);
    if (d.status) parts.push(`status ${d.status}`);
    parts.push(`${d.degree || 0} links`);
    return parts.join('\n');
  });

  sim.on('tick', () => {
    link
      .attr('x1', d => d.source.x).attr('y1', d => d.source.y)
      .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
    node.attr('transform', d => `translate(${d.x},${d.y})`);
  });
}

function nodeRadius(d) {
  if (d.kind === 'belief') {
    return 9 + (d.confidence || 0.5) * 7 + Math.min(6, (d.degree || 0));
  }
  return 7 + Math.min(5, (d.degree || 0));
}

document.getElementById('graph-close').addEventListener('click', closeGraph);
document.getElementById('graph-modal').addEventListener('click', (e) => {
  if (e.target.id === 'graph-modal') closeGraph();
});

// --- note side panel -----------------------------------------------------

let noteHistory = [];   // stack of {agent, id} for back navigation
let currentNoteAgent = null;

async function openNote(agentName, noteId, options = {}) {
  if (!agentName || !noteId) return;
  const panel = document.getElementById('note-panel');
  panel.classList.add('open');
  currentNoteAgent = agentName;
  if (!options.fromBack && options.pushHistory !== false && noteHistory.length && noteHistory[noteHistory.length - 1]?.id !== noteId) {
    noteHistory.push({ agent: agentName, id: noteId });
  } else if (!options.fromBack && !noteHistory.length) {
    noteHistory.push({ agent: agentName, id: noteId });
  }
  document.getElementById('note-id').textContent = noteId;
  document.getElementById('note-kind').textContent = '…';
  document.getElementById('note-kind').className = 'note-kind';
  document.getElementById('note-peer-tag').style.display = 'none';
  document.getElementById('note-meta').innerHTML = '';
  document.getElementById('note-body').innerHTML = '<div class="empty">loading…</div>';
  document.getElementById('note-history-back').classList.toggle('visible', noteHistory.length > 1);

  try {
    const r = await fetch(`/api/note/${encodeURIComponent(agentName)}/${encodeURIComponent(noteId)}`, { cache: 'no-store' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('http ' + r.status));

    const kind = d.kind || '';
    const kindEl = document.getElementById('note-kind');
    kindEl.textContent = kind;
    kindEl.className = 'note-kind ' + kind;

    if (d.is_peer_note) {
      const t = document.getElementById('note-peer-tag');
      t.textContent = 'in ' + d.found_in + "'s vault";
      t.style.display = 'inline-block';
    }

    const meta = renderMeta(d.frontmatter || {});
    document.getElementById('note-meta').innerHTML = meta;
    document.getElementById('note-body').innerHTML = renderMarkdown(d.body || '');
  } catch (e) {
    document.getElementById('note-body').innerHTML = `<div class="empty">error: ${esc(e.message)}</div>`;
  }
}

function closeNote() {
  document.getElementById('note-panel').classList.remove('open');
  noteHistory = [];
  currentNoteAgent = null;
}

function renderMeta(fm) {
  const skipKeys = new Set(['id']);
  const order = ['confidence', 'centrality', 'status', 'created', 'last_revised', 'date', 'tags', 'url_or_path', 'between'];
  const items = [];
  for (const key of order) {
    if (key in fm && fm[key] !== null && fm[key] !== '') {
      let val = fm[key];
      if (Array.isArray(val)) val = val.join(', ');
      items.push(`<span><b>${esc(key)}</b>${esc(String(val))}</span>`);
    }
  }
  return items.join('') || '<span style="color: var(--text-dimmer)">no metadata</span>';
}

// minimal markdown renderer for note bodies — handles headings, bold, lists,
// wikilinks, paragraphs. The notes follow a known structure so this is enough.
function renderMarkdown(md) {
  if (!md) return '<div class="empty">empty</div>';
  // 1. escape HTML
  let s = md.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  // 2. wikilinks → clickable
  s = s.replace(/\[\[([^\]]+)\]\]/g, (_, target) => {
    const id = target.split('/').pop().replace(/\.md$/, '');
    return `<a class="wikilink" data-id="${esc(id)}" data-target="${esc(target)}">${esc(target)}</a>`;
  });
  // 3. block-level: split into paragraphs separated by blank lines
  const blocks = s.split(/\n\s*\n/);
  const out = [];
  for (let block of blocks) {
    block = block.trim();
    if (!block) continue;
    // heading?
    const h = block.match(/^(#{1,3}) (.+)$/);
    if (h && !h[2].includes('\n')) {
      const lvl = h[1].length;
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
      continue;
    }
    // list?
    if (/^- /.test(block)) {
      const items = block.split('\n').filter(l => l.startsWith('- ')).map(l => `<li>${inline(l.slice(2))}</li>`);
      out.push(`<ul>${items.join('')}</ul>`);
      continue;
    }
    // paragraph (preserve internal line breaks as <br>)
    out.push(`<p>${inline(block).replace(/\n/g, '<br>')}</p>`);
  }
  return out.join('\n');
}

function inline(text) {
  // bold (**...**) — wikilinks already turned into anchor tags, so safe
  return text.replace(/\*\*([^*]+?)\*\*/g, '<strong>$1</strong>');
}

async function openFile(agentName, relPath) {
  if (!agentName || !relPath) return;
  const panel = document.getElementById('note-panel');
  panel.classList.add('open');
  currentNoteAgent = agentName;
  document.getElementById('note-id').textContent = relPath;
  document.getElementById('note-kind').textContent = 'file';
  document.getElementById('note-kind').className = 'note-kind';
  document.getElementById('note-peer-tag').style.display = 'none';
  document.getElementById('note-meta').innerHTML = '';
  document.getElementById('note-body').innerHTML = '<div class="empty">loading…</div>';
  document.getElementById('note-history-back').classList.remove('visible');
  noteHistory = [];

  try {
    const url = `/api/file/${encodeURIComponent(agentName)}?path=${encodeURIComponent(relPath)}`;
    const r = await fetch(url, { cache: 'no-store' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || ('http ' + r.status));

    document.getElementById('note-meta').innerHTML =
      `<span><b>size</b>${d.size.toLocaleString()} bytes</span>` +
      `<span><b>type</b>${esc(d.ext || '?')}</span>` +
      `<span><b>agent</b>${esc(d.agent)}</span>`;

    const body = document.getElementById('note-body');
    if (d.is_svg && d.text) {
      // strip script tags defensively, then inline
      const safe = d.text.replace(/<script[\s\S]*?<\/script>/gi, '<!-- script removed -->');
      body.innerHTML = `<div style="background:#fff; padding:1rem; border-radius:6px; max-width:100%; overflow:auto">${safe}</div>` +
        `<details style="margin-top:1rem"><summary style="cursor:pointer; color:var(--text-dim); font-size:0.78rem">view source</summary><pre style="white-space:pre-wrap; font-size:0.78rem; color:var(--text-dim); margin-top:0.5rem">${esc(d.text)}</pre></details>`;
    } else if (d.is_html && d.text) {
      body.innerHTML = `<div style="background:#161b22; padding:0.5rem; border-radius:4px; color:var(--text-dim); font-size:0.78rem; margin-bottom:0.5rem">html shown as source — open the file directly to render in browser</div>` +
        `<pre style="white-space:pre-wrap; font-family:ui-monospace,Menlo,monospace; font-size:0.78rem; line-height:1.55">${esc(d.text)}</pre>`;
    } else if (d.data_url) {
      body.innerHTML = `<img src="${d.data_url}" style="max-width:100%; border-radius:4px" alt="${esc(relPath)}">`;
    } else if (d.is_text && d.text != null) {
      const fence = d.ext === '.md' ? renderMarkdown(d.text)
                  : `<pre style="white-space:pre-wrap; font-family:ui-monospace,Menlo,monospace; font-size:0.82rem; line-height:1.55; margin:0">${esc(d.text)}</pre>`;
      body.innerHTML = fence;
    } else {
      body.innerHTML = `<div class="empty">binary file — ${d.size.toLocaleString()} bytes (${esc(d.mime || d.ext || 'unknown')}). Open directly in your editor.</div>`;
    }
  } catch (e) {
    document.getElementById('note-body').innerHTML = `<div class="empty">error: ${esc(e.message)}</div>`;
  }
}

document.getElementById('note-close').addEventListener('click', closeNote);
document.getElementById('note-back').addEventListener('click', () => {
  if (noteHistory.length <= 1) return;
  noteHistory.pop();                         // discard current
  const prev = noteHistory[noteHistory.length - 1];
  openNote(prev.agent, prev.id, { fromBack: true });
});
// Wikilink delegation: any click on a .wikilink inside the panel
document.getElementById('note-body').addEventListener('click', (e) => {
  const a = e.target.closest('a.wikilink');
  if (!a) return;
  e.preventDefault();
  const id = a.getAttribute('data-id');
  if (id && currentNoteAgent) openNote(currentNoteAgent, id);
});

// Esc handler: close note panel first if open, then graph modal
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const noteOpen = document.getElementById('note-panel').classList.contains('open');
  const graphOpen = document.getElementById('graph-modal').classList.contains('open');
  if (noteOpen) closeNote();
  else if (graphOpen) closeGraph();
});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return  # silence default request logging

    def do_GET(self):  # noqa: N802
        if self.path == "/" or self.path == "/index.html":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/file/"):
            from urllib.parse import unquote, urlsplit, parse_qs
            split = urlsplit(self.path)
            agent_name = unquote(split.path[len("/api/file/"):])
            params = parse_qs(split.query)
            rel = params.get("path", [""])[0]
            try:
                agents = discover_agents(SCAN_ROOT)
                agent = next((a for a in agents if a.name == agent_name), None)
                if agent is None:
                    self.send_response(404); self.send_header("Content-Type", "application/json"); self.end_headers()
                    self.wfile.write(json.dumps({"error": f"agent not found: {agent_name}"}).encode()); return
                # Resolve and verify the path is inside the agent's project dir (no traversal)
                target = (agent.path / rel).resolve()
                project = agent.path.resolve()
                if project not in target.parents and target != project:
                    self.send_response(403); self.send_header("Content-Type", "application/json"); self.end_headers()
                    self.wfile.write(json.dumps({"error": "path outside project"}).encode()); return
                if not target.exists() or not target.is_file():
                    self.send_response(404); self.send_header("Content-Type", "application/json"); self.end_headers()
                    self.wfile.write(json.dumps({"error": f"not a file: {rel}"}).encode()); return
                size = target.stat().st_size
                MAX = 1_000_000  # 1 MB cap
                if size > MAX:
                    self.send_response(413); self.send_header("Content-Type", "application/json"); self.end_headers()
                    self.wfile.write(json.dumps({"error": f"file too large ({size} bytes, max {MAX})"}).encode()); return
                ext = target.suffix.lower()
                text_exts = {".md", ".py", ".sh", ".txt", ".json", ".yaml", ".yml", ".toml", ".html", ".htm", ".css", ".js", ".svg", ".csv", ".tsv"}
                is_text = ext in text_exts
                payload = {
                    "agent": agent_name,
                    "path": rel,
                    "ext": ext,
                    "size": size,
                    "is_text": is_text,
                    "is_svg": ext == ".svg",
                    "is_html": ext in (".html", ".htm"),
                }
                if is_text:
                    try:
                        payload["text"] = target.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        payload["text"] = None
                        payload["is_text"] = False
                        payload["note"] = "could not decode as utf-8"
                else:
                    import base64, mimetypes
                    mime, _ = mimetypes.guess_type(target.name)
                    payload["mime"] = mime or "application/octet-stream"
                    if mime and mime.startswith("image/") and size < 200_000:
                        payload["data_url"] = f"data:{mime};base64,{base64.b64encode(target.read_bytes()).decode('ascii')}"
                body = json.dumps(payload, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                err = json.dumps({"error": str(e), "trace": traceback.format_exc()}).encode()
                self.send_response(500); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(err)
            return
        if self.path.startswith("/api/note/"):
            from urllib.parse import unquote
            rest = self.path[len("/api/note/"):]
            parts = rest.split("/", 1)
            if len(parts) != 2:
                self.send_response(400); self.end_headers(); return
            agent_name = unquote(parts[0])
            note_id = unquote(parts[1])
            try:
                agents = discover_agents(SCAN_ROOT)
                agent = next((a for a in agents if a.name == agent_name), None)
                if agent is None:
                    err = json.dumps({"error": f"agent not found: {agent_name}"}).encode()
                    self.send_response(404); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(err); return
                path, found_in = _find_note_anywhere(agents, agent, note_id)
                if path is None:
                    err = json.dumps({"error": f"note not found: {note_id}", "agent": agent_name}).encode()
                    self.send_response(404); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(err); return
                fm, body = _parse_note(path)
                raw = path.read_text(encoding="utf-8")
                payload = {
                    "id": note_id,
                    "agent": agent_name,                    # the agent the user is viewing
                    "found_in": found_in.name,              # the vault the note actually lives in (may be peer)
                    "is_peer_note": found_in.name != agent_name,
                    "kind": (PREFIX_TO_KIND.get(note_id.split("-", 1)[0], "?")).rstrip("s") if note_id.split("-", 1)[0] != "j" else "journal",
                    "frontmatter": fm,
                    "body": body,
                    "raw": raw,
                    "path": str(path),
                }
                body_bytes = json.dumps(payload, indent=2, default=str).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)
            except Exception as e:
                err = json.dumps({"error": str(e), "trace": traceback.format_exc()}).encode()
                self.send_response(500); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(err)
            return
        if self.path.startswith("/api/graph/"):
            from urllib.parse import unquote
            name = unquote(self.path[len("/api/graph/"):])
            try:
                agents = discover_agents(SCAN_ROOT)
                agent = next((a for a in agents if a.name == name), None)
                if agent is None:
                    err = json.dumps({"error": f"agent not found: {name}"}).encode()
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(err)
                    return
                payload = _build_graph(agent)
                body = json.dumps(payload, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                err = json.dumps({"error": str(e), "trace": traceback.format_exc()}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err)
            return
        if self.path == "/api/status":
            try:
                payload = build_payload()
                body = json.dumps(payload, indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                err = json.dumps({"error": str(e), "trace": traceback.format_exc()}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(err)
            return
        self.send_response(404)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"not found")


def main() -> int:
    global SCAN_ROOT
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--scan-root", default=str(SCAN_ROOT))
    args = p.parse_args()
    SCAN_ROOT = Path(args.scan_root).expanduser().resolve()

    agents = discover_agents(SCAN_ROOT)
    names = ", ".join(a.name for a in agents) or "(none)"
    print(f"dashboard: http://localhost:{args.port}")
    print(f"scan root: {SCAN_ROOT}")
    print(f"discovered {len(agents)} agents: {names}")
    print("(Ctrl-C to stop)")

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down")
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
