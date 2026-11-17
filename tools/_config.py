"""Shared configuration resolver for all vault tools.

Every tool reads paths through this module so an agent project can point them
at its own vault via environment variables:

    WOB_VAULT_ROOT       absolute path to this agent's vault/
    WOB_CREDIT_LEDGER    absolute path to this agent's credit ledger JSON
    WOB_ENV_FILE         absolute path to this agent's .env
    WOB_PEER_VAULT       (paired agents only) absolute path to peer's vault/

Defaults — when no env vars are set — point at the master "Web of Belief"
project, preserving backwards compatibility for the original agent.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(var: str, default: Path) -> Path:
    val = os.environ.get(var)
    return Path(val).expanduser().resolve() if val else default


_THIS = Path(__file__).resolve()
_MASTER_ROOT = _THIS.parent.parent            # /Web of Belief/
_MASTER_TOOLS = _THIS.parent                  # /Web of Belief/tools/

VAULT_ROOT: Path = _env_path("WOB_VAULT_ROOT", _MASTER_ROOT / "vault")
CREDIT_LEDGER: Path = _env_path("WOB_CREDIT_LEDGER", _MASTER_TOOLS / ".credit_ledger.json")
ENV_FILE: Path = _env_path("WOB_ENV_FILE", _MASTER_ROOT / ".env")

_peer = os.environ.get("WOB_PEER_VAULT")
PEER_VAULT: Path | None = Path(_peer).expanduser().resolve() if _peer else None
PEER_NAME: str = os.environ.get("WOB_PEER_NAME", "peer")
AGENT_NAME: str = os.environ.get("WOB_AGENT_NAME", "agent")
