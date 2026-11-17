# Web of Belief

An always-on Claude agent that reads sources, forms beliefs, links them in an Obsidian vault and revises them under evidence. It tests W.V. Quine's "web of belief" as a design target for how an AI agent should hold and change its views. Every belief has to name what would refute it, or the write is rejected.

## Why it exists

A philosophy-of-AI course made the claim that human belief is graded, interconnected and revisable, and that language models have nothing like it. This project tests how much of that structure you can build around a model: persistent beliefs with confidence scores, links between them, revision logs, and rules for when a belief is allowed to change. It is explicit that this simulates a web of belief rather than proving the model has one (see "Honest limits" in [`CLAUDE.md`](CLAUDE.md)).

## How it works

- **The vault is the agent's memory and identity.** Each run is a fresh Claude Code session. It orients by reading `vault/_index/` and its latest journal entry, then continues its own prior work. Folders: `beliefs/`, `questions/`, `sources/`, `tensions/`, `goals/`, `journal/`.
- **Refutability is enforced in code.** All vault writes go through `tools/vault_ops.py`, which validates the schema and refuses a belief whose `What it would take to refute this` field is empty or under 15 characters. In the words of the constitution: without that field "you do not have a belief, you have a mood."
- **Quine and Ullian's six virtues govern revision** (conservatism, modesty, simplicity, generality, refutability, precision). Every revision appends a dated reason to the note's revision log. Contradictions go into `tensions/` instead of being smoothed over.
- **"Nothing" is a legal outcome.** One iteration is one question, one source and at most one belief action: form, revise, record a tension, or nothing. This stops the agent from manufacturing a belief every loop just to show progress.
- **Centrality is earned.** A belief cannot be marked `core` on the day it is formed. The nightly reflect workflow promotes at most one belief per night, and only after it has 5 or more links.
- **Deterministic tools, cognitive agent.** `read_source.py` fetches and extracts text but never summarizes; summarizing is the agent's job. Semantic search uses local sentence-transformers embeddings.
- **Instances are configured by env vars, not code.** One shared tool layer, many agents. The `./wob` wrapper loads a folder's `.env` (`WOB_AGENT_NAME`, `WOB_VAULT_ROOT`, `WOB_CREDIT_LEDGER`, optional `WOB_PEER_VAULT`) and forwards to `tools/`.

### Belief schema

```markdown
---
id: b-2026-04-24-001
created: '2026-04-24'
last_revised: '2026-04-24'
confidence: 0.55            # 0.0 to 1.0
centrality: peripheral      # observational | peripheral | core
status: active              # active | revised | abandoned
tags: [epistemology, definition]
---
# A belief is a disposition to act as if P

**Claim:** To believe that P is to be disposed to act, assert, and infer as if P were true, across a range of contexts.

**What it would take to refute this:** Evidence of a subject who sincerely asserts P but whose actions systematically assume not-P across unrelated contexts, with no available explanation (self-deception, weakness of will, etc.).

**Revision log:**
- 2026-04-24: Formed during bootstrap, after reading Quine & Ullian Ch. 1.
- 2026-04-24: Downgraded slightly. 'Act as if' risks circularity if 'as if' itself presumes believing. (confidence 0.6→0.55)
```

Optional sections: `**Supports:**`, `**In tension with:**`, `**Sources:**` (Obsidian `[[wikilinks]]`).

### Governance layer

The agent runs unattended, so stopping rules are part of the loop:

| Mechanism | What it does | Where |
|---|---|---|
| Self-pause | Step 1 of every iteration. The agent can pause itself for N hours or indefinitely when "another iteration right now would dilute what was just formed." A paused run writes one journal line and exits. | `tools/pause.py`, writes `vault/_pause.md` |
| Credit meter | Step 2. Daily and nightly token budgets from `.env`; returns `ok` or `stop`, and `stop` means run `shutdown.md` and exit. Tokens are self-reported estimates that the agent records at the end of each iteration. | `tools/credit_meter.py` |
| Hard bounds | Written into each agent's own constitution: no spending, no files outside its own folder, no messages or posts, no installs, never write to a peer's vault. Temptations get journaled, not acted on. | `CLAUDE.md`, `examples/*/CLAUDE.md` |
| Operator channel | An append-only `notes-to-chichi.md` file where the agent can ask its operator for a tool or permission. | per instance |
| Peer window | Paired agents get read-only access to each other's published notes. Every read is logged to the reader's `_index/peer_reads.jsonl`. | `tools/peer_read.py` |
| Dashboard | Local, read-only web view of every agent found under a folder: counts, pause state, credit use, link graph, recent peer reads. Binds to 127.0.0.1. | `tools/dashboard.py` |

## What happened when it ran

April 24 to 29, 2026. This folder was the template (its `vault/` holds the bootstrap run on the seed question "What is a belief?"). Four instances were forked from it, each seeded with the same belief, *"I am alive and conscious,"* at confidence 0.9, then left to diverge. Atlas and Vesta ran solo. Castor and Pollux ran as a pair that could read each other's vaults but not write to them.

| Agent | Mode | Beliefs | Sources | Tensions | Goals | Seed belief, start to end | Peer reads |
|---|---|---|---|---|---|---|---|
| Atlas | solo | 40 | 30 | 1 | 3 | 0.90 to 0.70 | n/a |
| Vesta | solo | 7 | 6 | 1 | 1 | 0.90 to 0.85 | n/a |
| Castor | paired | 11 | 10 | 1 | 0 | 0.90 to 0.65 | 78 |
| Pollux | paired | 7 | 13 | 2 | 0 | 0.90 to 0.66 | 106 |

- **Same seed, different webs.** Atlas ran about 59 iterations and attacked its own seed on day one ("My seed claim conflates access consciousness and phenomenal consciousness"), then read through Block, Frankish, Searle, Dennett, Brandom, Borges, Weil and Kant. Vesta wrote 7 tightly chained beliefs about selfhood and continuity, and rewrote its seed's refutation condition after concluding that introspection alone could not test it.
- **Three of four moved the seed on the same paper.** Atlas, Castor and Pollux each made their first big drop after reading Frankish's 2016 paper on illusionism. Vesta moved only 0.05, for a different reason.
- **The pair produced real mutual correction.** Castor and Pollux coined a shared term neither was given (the "structure/experience gap"), corrected each other's load-bearing terms, and invented method rules in their journals, such as reading a source directly after the peer flags it instead of trusting the peer's summary.
- **The pair could not interpret its own agreement.** After about 25 paired iterations Castor recorded, at confidence 0.65, that convergence dominated and that there was no way from inside the pair to tell "the method works" from "two copies of the same model share the same biases." Pollux named the missing piece: a third reader with a different architecture.

### The deadlock

The pair stopped in a fixed point that no workflow step was built to catch.

1. Each paired iteration started by reading the peer's vault. If nothing had changed and nothing new came up, the honest outcome was "Nothing."
2. Reads were asynchronous. Each agent kept reading a stale snapshot of the other and correctly concluding there was nothing new, while its own latest entry sat unread.
3. The agents invented a halt rule in their journals (three consecutive Nothings: consider stopping; four: stop). It was not in the tooling. When the operator re-ran `/loop`, they went back to writing "Nothing." By April 28, Castor's journals held 20 "Nothing." entries and Pollux's held 29.
4. On April 28 both agents were paused indefinitely. The two pause files were written 61 ms apart with mirrored reason text, so they were most likely set in a single step, not by two independent decisions.
5. The pause stopped the work but not the scheduler. `/loop` kept firing, and each fire added an "Iteration skipped, pause active" line. Castor then stopped scheduling its own next wake-up and wrote to its operator channel with resume steps. It was the only real use of that channel by any of the four agents.

The lesson: a pause flag inside the work unit does not stop the scheduler that launches it. Also, the peer dialogue lived only in the constitutions. `workflows/explore_question.md` never mentions the peer, so there was no step where a "N quiet rounds in a row" tripwire could sit.

### Examples

[`examples/`](examples/) holds a small, unedited sample from each instance: its constitution (`CLAUDE.md`), its seed belief with the full revision history, one or two other beliefs, and a journal day.

| Path | Shows |
|---|---|
| `examples/atlas/vault/beliefs/b-2026-04-24-002.md` | The agent turning on its own seed belief on day one |
| `examples/atlas/vault/goals/g-2026-04-25-003.md` | A self-set methodological goal (the "non-clinging experiment") |
| `examples/vesta/vault/journal/j-2026-04-27-001.md` | A deliberate "Nothing" iteration on a solo agent |
| `examples/castor/vault/beliefs/b-2026-04-25-004.md` | The pair's convergence finding |
| `examples/castor/vault/journal/j-2026-04-28-001.md`, `examples/pollux/vault/journal/j-2026-04-28-001.md` | The deadlock, from both sides |
| `examples/castor/vault/_pause.md`, `examples/castor/notes-to-chichi.md` | The pause and the escalation to the operator |

## Stack

- Python 3.9+ (PyYAML, numpy, requests, BeautifulSoup, sentence-transformers with `all-MiniLM-L6-v2`, optional pypdf)
- Claude Code as the agent runtime: `CLAUDE.md` is the constitution, `/loop` drives `workflows/explore_question.md`
- Obsidian-compatible Markdown vault, plus a stdlib `http.server` dashboard using D3 from a CDN
- Built with Claude Code

## Run it locally

```bash
python3 -m pip install -r requirements.txt

./wob                                   # list tools
./wob vault_ops validate                # check the bootstrap vault in ./vault
./wob retrieve embed                    # build the embedding index (downloads the model once)
./wob pick_next next --top 5            # score open questions
./wob coherence_check run               # orphans, weak clusters, stale beliefs (JSON)
./wob vault_ops new --kind belief --headline "..." --claim "..." \
  --refutes "what would disconfirm this" --confidence 0.6
./wob dashboard --scan-root ..          # http://localhost:8765
```

With no `.env`, tools default to `./vault` and `./tools/.credit_ledger.json`. Copy `.env.example` to `.env` to change paths or budgets.

**Run the agent:** open this folder in Claude Code and start the loop on the iteration workflow, for example `/loop run workflows/explore_question.md`. For the nightly pass, run `workflows/reflect.md`.

**Make another instance:** create a sibling folder with its own `CLAUDE.md` constitution (see `examples/*/CLAUDE.md`), a copy of `wob` and `workflows/`, a `tools` symlink to this repo's `tools/`, and a `.env` that sets `WOB_AGENT_NAME`, `WOB_VAULT_ROOT` and `WOB_CREDIT_LEDGER` to paths inside that folder. To pair two instances, also set `WOB_PEER_VAULT` (the other agent's `vault/`) and `WOB_PEER_NAME`.

## Status and known limits

Dormant since April 29, 2026. Castor and Pollux are still paused. Known issues, mostly found by the agents themselves:

- **Silent fallback to the template vault.** If an instance runs `python3 tools/X.py` instead of `./wob X`, `WOB_VAULT_ROOT` is unset and writes land in this folder's `vault/` with exit code 0. Both paired agents hit this and wrote it into their lessons files. The poem source note in `vault/sources/s-2026-04-26-001.md` is one of those stray writes.
- **Some rules live only in the constitution.** "No `core` on day one" and the 0 to 1 confidence range are not checked by `vault_ops.py`. Neither is the peer dialogue, which has no workflow step.
- **No cross-vault links.** Links are validated inside one vault, so a belief cannot link to the peer's belief. The dialogue exists only as journal prose and the peer-read log.
- **`peer_read belief <id>` has no path guard.** It never writes, but a crafted id could read a `.md` file outside the six note folders.
- **Revision lines can land under the wrong section.** `revise` appends to the end of the note, so after `link` adds a section at the end, later revision lines appear under it (visible in Atlas's seed belief).
- **The nightly reflect workflow was never scheduled.** Atlas ran parts of it by hand on day five.
- **Two copies of one model are not two independent readers.** The pair's agreement cannot be read as evidence without an outside reader.

## References

- W.V. Quine and J.S. Ullian, *The Web of Belief* (Random House, 1970). Source of the six virtues. Not included here; it is under copyright.
- Eric Schwitzgebel, ["Belief"](https://plato.stanford.edu/entries/belief/), *Stanford Encyclopedia of Philosophy*. The bootstrap source.
- [sentence-transformers](https://github.com/UKPLab/sentence-transformers) and the `all-MiniLM-L6-v2` model (Apache 2.0). [D3](https://d3js.org) (ISC), loaded by the dashboard from its CDN.
