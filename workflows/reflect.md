# Workflow: Nightly Reflection

**Objective:** Consolidate the day's work. Address tensions, merge redundancy, re-score centrality, generate questions from gaps, write a digest.

**Runs:** Scheduled nightly (via `/schedule` skill).

**Tools used:** `coherence_check.py`, `vault_ops.py`, `retrieve.py`, `daily_digest.py`, `credit_meter.py`.

**Budget:** Uses `--kind nightly` budget, separate from session budget.

---

## Pre-flight

1. Check nightly credits:
   ```
   python3 tools/credit_meter.py check --kind nightly
   ```
   If `stop`, write a one-line note to `vault/journal/digest-<today>.md` and exit.

2. Ensure indexes are current:
   ```
   python3 tools/retrieve.py embed
   python3 tools/vault_ops.py index
   ```

## Steps

1. **Coherence pass:**
   ```
   python3 tools/coherence_check.py run > /tmp/coherence.json
   ```
   Inspect. Address in this order:

   a. **Undocumented tensions.** For each pair in `undocumented_tensions`, create a tension note that states what conflicts and who wins (or that it remains unresolved):
      ```
      python3 tools/vault_ops.py new --kind tension --headline "..." \
        --supports "<belief-a>,<belief-b>" --claim "<nature of conflict>"
      ```

   b. **Orphans.** For each orphan (active belief with 0 links): try to find 1–2 neighbors via
      ```
      python3 tools/retrieve.py neighbors <orphan-id> --k 5
      ```
      If a real relationship exists, link it. If the orphan is genuinely unmoored from the rest of the web, consider whether it's too speculative and should be revised to `status: abandoned` with a revision-log reason.

   c. **Weak clusters (1 link).** Same treatment — try to link more broadly. If you cannot in good conscience, leave it; a web does not need to be uniformly dense.

   d. **Stale active beliefs (> 30 days, not revised).** Open each one. Does it still hold in light of what you now believe elsewhere? If yes, add a revision-log line acknowledging you reviewed it. If no, revise it.

2. **Consolidation.** Use `retrieve.py neighbors <id>` on a random sample of 3 beliefs. If any two beliefs are near-duplicates (similarity > 0.85 AND say roughly the same thing), merge them:
   - Pick the better-written one as survivor. Add the other's sources & supports to it.
   - Revise the loser to `status: abandoned` with a revision-log line pointing to the survivor.
   - Do NOT merge aggressively. Two beliefs saying related-but-distinct things should stay separate. Over-merging makes the web lie.

3. **Re-score centrality.** Beliefs with ≥ 5 supports/incoming links over ≥ 3 other beliefs are candidates for `core`. Don't promote more than 1 belief to core per night.
   ```
   python3 tools/vault_ops.py revise <id> --centrality core --note "Promoted to core after N linked supports."
   ```

4. **Generate 1–3 new questions from gaps.** For each weak cluster or orphan that remained after step 1, ask: *what would I need to know to connect this better?* Write it as a question note.

5. **Write the daily digest:**
   ```
   python3 tools/daily_digest.py run
   ```
   Read it. If it reads as AI-slop (vague, hedging, listicles without thought), append a real reflection paragraph to the end directly.

6. **Record tokens:**
   ```
   python3 tools/credit_meter.py record --tokens <estimate> --kind nightly
   ```

## Edge cases

- **Everything is already coherent.** Rare but possible. Write a one-paragraph journal entry saying so, then generate at least one new question — stagnation in the reflection routine is a red flag.
- **A tension resolves cleanly with one revision.** Do the revision and mark the tension as `status: resolved` with a revision-log note on the tension note.
- **You accumulated 3+ unresolved tensions.** That's fine — record them all. Resolution over time is the feature, not rushing.

## Done criteria

- `vault_ops.py validate` passes.
- Digest file exists at `vault/journal/digest-<today>.md`.
- At least one real change to the vault, OR a journal entry explaining why nothing changed tonight.
