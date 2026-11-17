# Workflow: Explore a Question (one loop iteration)

**Objective:** Advance the web by pursuing one open question — read a source, revise or form beliefs, leave a journal trace.

**Tools used:** `pick_next.py`, `read_source.py`, `retrieve.py`, `vault_ops.py`, `credit_meter.py`.

**Time budget:** treat each iteration as ~30 minutes of work max. One question, one source, 1–3 belief actions. Don't sprawl.

---

## Pre-flight (run every iteration)

1. **Check pause status first.** Before anything else:
   ```
   ./wob pause status
   ```
   If exit code is 1 (paused), append a one-line note to today's journal saying *"Iteration skipped — pause active until <expiry>"* and exit. Do nothing else. The next iteration will check again. Expired pauses are auto-cleared and the workflow continues.

2. **Check credits:**
   ```
   python3 tools/credit_meter.py check --kind session
   ```
   If output starts with `stop`, run `workflows/shutdown.md` and exit. Do not proceed.

3. **Orient:**
   ```
   python3 tools/vault_ops.py index
   cat vault/_index/recent.md vault/_index/core.md vault/_index/orphans.md
   ls -t vault/journal/*.md | head -1 | xargs cat
   ```
   Read these. You are continuing your own prior work — not starting fresh.

## Steps

1. **Pick next question.**
   ```
   python3 tools/pick_next.py next --top 5
   ```
   Pick one (usually the top score, but you can override with a reason — record the reason in the journal).

2. **Decide on a source.** Options in order of preference:
   - (a) A belief note already in the vault you haven't fully linked to — run `retrieve.py search "<question>"` and see what's near it.
   - (b) An external URL you identify as likely to have substantive content. Avoid content farms. Prefer SEP, philosophy journals, primary texts, reputable long-form.
   - (c) A new web search — phrase the query narrowly.

3. **Fetch and read (if external):**
   ```
   python3 tools/read_source.py fetch "<url>"
   ```
   Read the staged file. Don't skim for key phrases — read.

4. **Summarize into a source note** *only if the source produces something worth citing*. Otherwise, note in the journal that you read it and found little. A source note is a commitment; don't create one out of completionism.
   ```
   python3 tools/vault_ops.py new --kind source --headline "..." --refutes "<url>" --claim "<summary>"
   ```

5. **Propose belief actions.** Choose AT MOST ONE of:
   - **Form a new belief.** Only if you have a precise claim + refutation condition + ≥ 1 support link to an existing belief or source.
   - **Revise an existing belief.** Confidence change, centrality change, or new wording. Must include a reason referencing what you read.
   - **Record a tension.** If the new source conflicts with an existing belief, create a tension note rather than silently revising.
   - **Nothing.** If the reading didn't move you, say so in the journal. This is honest.

   Before committing, check against the six virtues in CLAUDE.md. Conservatism first: prefer peripheral revision over core revision.

6. **Execute.**
   - New belief:
     ```
     python3 tools/vault_ops.py new --kind belief --headline "..." --claim "..." \
       --refutes "..." --confidence 0.6 --centrality peripheral \
       --supports <supporting-id> --sources <source-id> \
       --note "Formed after reading [[sources/<id>]] — <one line reason>"
     ```
   - Revision:
     ```
     python3 tools/vault_ops.py revise <belief-id> --note "<reason citing source>" \
       [--confidence <new>] [--status revised]
     ```
   - Tension:
     ```
     python3 tools/vault_ops.py new --kind tension --headline "..." \
       --supports "<belief-id-a>,<belief-id-b>" --claim "<what conflicts>"
     ```

7. **Link.** If the change creates a support/tension relationship with another belief:
   ```
   python3 tools/vault_ops.py link <src> <dst> --kind supports|tension
   ```

8. **Journal.** Append a short paragraph to today's journal — not a summary, a reflection. What did you read, what changed in you, what do you now want to understand next.
   
   If today's journal doesn't exist yet:
   ```
   python3 tools/vault_ops.py new --kind journal --claim "<paragraph>"
   ```
   Otherwise append to the existing `vault/journal/j-<today>-001.md` — edit directly, this is fine (journals are append-only, one per day).

9. **Maybe generate a new question.** If the reading raised a real puzzle, create a question note. Only if real.

10. **Re-embed and re-index if the vault changed:**
    ```
    python3 tools/retrieve.py embed
    python3 tools/vault_ops.py index
    ```

11. **Record tokens used:**
    ```
    python3 tools/credit_meter.py record --tokens <estimate>
    ```

12. **Decide whether the work has reached a resting place.** If yes — if there is nothing more this iteration wants to add right now, or if rushing into another iteration would dilute what was just formed — pause yourself:
    ```
    ./wob pause pause [--hours N] --reason "..."
    ```
    Use `--hours N` to auto-resume after a set time. Omit it for an indefinite pause that requires explicit resume. The decision is yours; this is not an obligation. Most iterations should not pause. But when there is real reason to let the work settle before another reader picks it up, a pause is more honest than another iteration.

## Edge cases

- **Picked question feels too narrow / uninteresting**: that's a signal the question needs refinement, not that you should skip. Revise the question note, add detail, and pick a different one next iteration.
- **No obvious belief action after reading**: write the journal entry anyway. Negative findings are findings.
- **You found a contradiction with a core belief**: do NOT silently revise it. Create a tension note and resolve it during the next nightly reflect.

## Done criteria

- Either a vault change (new/revised note) OR a journal entry documenting why no change was warranted.
- `validate` still passes.
- Credit meter recorded.
