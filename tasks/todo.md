# Tasks — Web of Belief

**About:** An always-active Claude instance that reads, forms beliefs, links them in an Obsidian vault, and revises them under evidence. Tests the Quinean "web of belief" as a design target for AI epistemic agency.

## Active

- **Priority:** Med | **Status:** Pending | **Desc:** Make the peer dialogue a workflow step (read peer, detect N consecutive "Nothing" iterations, halt) instead of a constitution-only rule.
- **Priority:** Med | **Status:** Pending | **Desc:** Fail loudly when a tool runs without `WOB_VAULT_ROOT` from an instance folder, instead of silently writing to the master vault.
- **Priority:** Low | **Status:** Pending | **Desc:** Enforce "no core on day one" and the 0-1 confidence range in `vault_ops.py`, not only in the constitution.

## Resolved

- 2026-04-24: Plan approved. Architecture: Obsidian vault + Python tool layer + session loop + nightly routine. Seed question: "What is a belief?"
- 2026-04-24: Core tools and workflow SOPs built; bootstrap run formed 4 beliefs from the SEP "Belief" entry.
- 2026-04-24: Forked four instances (Atlas, Vesta solo; Castor, Pollux paired) from this template.
- 2026-04-26: Added `pause.py` (agent self-pause) and `dashboard.py` (live view across agents).
- 2026-04-28: Castor/Pollux deadlock: both paused indefinitely. All instances dormant since 2026-04-29.
- 2026-11-17: Prepared as a public repo (README, examples/, .env.example, requirements.txt).
