#!/usr/bin/env python3
"""read_source — fetch a URL or PDF, extract readable text, stage it for the agent.

This is a DETERMINISTIC fetcher. It does not summarize — summarization is a
cognitive act, and the agent performs it. After fetching, the agent reads the
staged text file, writes a summary, and creates a source note via
    vault_ops.py new --kind source ...

Usage:
    read_source.py fetch <url>              # URL → .tmp/sources/<slug>.txt
    read_source.py pdf <path>               # PDF → .tmp/sources/<slug>.txt
    read_source.py list                     # list staged sources not yet ingested
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _config import VAULT_ROOT  # noqa: E402
STAGE_DIR = VAULT_ROOT.parent / ".tmp" / "sources"
VAULT_SOURCES = VAULT_ROOT / "sources"

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


def _slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip()).strip("-").lower()
    return (s[:limit] or "untitled").rstrip("-")


def _stage_path(label: str, url_or_path: str) -> Path:
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(url_or_path.encode()).hexdigest()[:8]
    return STAGE_DIR / f"{_slug(label)}--{digest}.txt"


def _extract_html(html: str) -> tuple[str, str]:
    """Return (title, readable_text). Strips scripts, nav, footer — keeps <article>/<main> preferentially."""
    soup = BeautifulSoup(html, "lxml" if _has("lxml") else "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "nav", "footer", "aside", "form"]):
        tag.decompose()
    title = (soup.title.string.strip() if soup.title and soup.title.string else "").strip()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    # collapse whitespace per block
    blocks = []
    for el in main.find_all(["h1", "h2", "h3", "h4", "p", "li", "blockquote", "pre"]):
        txt = el.get_text(" ", strip=True)
        if txt:
            if el.name.startswith("h"):
                blocks.append(f"\n## {txt}\n")
            else:
                blocks.append(txt)
    text = "\n\n".join(blocks)
    # fall back to full body text if we got almost nothing
    if len(text) < 200:
        text = main.get_text("\n", strip=True)
    return title, text


def _has(modname: str) -> bool:
    try:
        __import__(modname); return True
    except ImportError:
        return False


def cmd_fetch(args: argparse.Namespace) -> int:
    url = args.url
    host = urlparse(url).netloc or "source"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"fetch failed: {e}", file=sys.stderr); return 1

    ctype = r.headers.get("content-type", "").lower()
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        # write PDF to tmp and route through pdf extractor
        pdf_path = STAGE_DIR / f"__download--{hashlib.sha1(url.encode()).hexdigest()[:8]}.pdf"
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(r.content)
        return _write_pdf(pdf_path, label=host, source=url)

    title, text = _extract_html(r.text)
    label = title or host
    out = _stage_path(label, url)
    out.write_text(_frame(label, url, text), encoding="utf-8")
    print(out)
    print(f"  {len(text):,} chars · title={label!r}", file=sys.stderr)
    return 0


def cmd_pdf(args: argparse.Namespace) -> int:
    p = Path(args.path).expanduser().resolve()
    if not p.exists():
        print(f"no such file: {p}", file=sys.stderr); return 1
    return _write_pdf(p, label=p.stem, source=str(p))


def _write_pdf(pdf_path: Path, *, label: str, source: str) -> int:
    try:
        import pypdf  # type: ignore
    except ImportError:
        print("pypdf is not installed. Install with: pip3 install --user pypdf", file=sys.stderr)
        return 2
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as e:
        print(f"PDF extraction failed: {e}", file=sys.stderr); return 1
    text = "\n\n".join(f"[p.{i+1}]\n{t}" for i, t in enumerate(pages) if t.strip())
    out = _stage_path(label, source)
    out.write_text(_frame(label, source, text), encoding="utf-8")
    print(out)
    print(f"  {len(text):,} chars · {len(pages)} pages · source={source}", file=sys.stderr)
    return 0


def _frame(label: str, source: str, text: str) -> str:
    return (
        f"# Staged source: {label}\n"
        f"# Source: {source}\n"
        f"# Length: {len(text)} chars\n"
        f"# Next step: read, summarize, run\n"
        f"#   python3 tools/vault_ops.py new --kind source --headline \"<label>\" \\\n"
        f"#     --refutes \"<source URL or path>\" --claim \"<summary paragraph>\"\n"
        f"# Then delete this staged file once ingested.\n"
        f"# ─────────────────────────────────────────────────────────────\n\n"
        f"{text}\n"
    )


def cmd_list(_args: argparse.Namespace) -> int:
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    staged = sorted(STAGE_DIR.glob("*.txt"))
    if not staged:
        print("(no staged sources)"); return 0
    for f in staged:
        size = f.stat().st_size
        print(f"{f.relative_to(STAGE_DIR.parent.parent)}\t{size:>8} bytes")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch"); f.add_argument("url"); f.set_defaults(fn=cmd_fetch)
    pd = sub.add_parser("pdf"); pd.add_argument("path"); pd.set_defaults(fn=cmd_pdf)
    ls = sub.add_parser("list"); ls.set_defaults(fn=cmd_list)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
