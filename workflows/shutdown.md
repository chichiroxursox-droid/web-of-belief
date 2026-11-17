# Workflow: Shutdown (credits exhausted)

**Objective:** When the credit meter returns `stop`, leave the vault in a clean state and record a checkpoint so tomorrow's session can resume without re-orienting from scratch.

---

## Steps

1. Finish the in-flight change, if any. Do NOT start a new belief. If mid-edit on a belief note, save it and complete that one action.

2. Ensure no half-written state:
   ```
   python3 tools/vault_ops.py validate
   ```
   If validation fails, fix. A broken vault at shutdown is worse than running 1 % over budget.

3. Append a shutdown line to today's journal:
   ```
   - <timestamp>: Session ended — credit budget reached. Last action: <brief>. Next iteration should start with: <one sentence>.
   ```
   Edit `vault/journal/j-<today>-001.md` directly.

4. Re-embed and re-index so tomorrow doesn't start stale:
   ```
   python3 tools/retrieve.py embed
   python3 tools/vault_ops.py index
   ```

5. Record any untracked tokens:
   ```
   python3 tools/credit_meter.py record --tokens <estimate>
   ```

6. Exit the loop. Print `SHUTDOWN: session budget reached. Resume tomorrow.` to stdout.

## Done criteria

- Validate passes.
- Journal has a shutdown line.
- Next iteration can `cat vault/_index/recent.md` + last journal entry and pick up where you left off.
