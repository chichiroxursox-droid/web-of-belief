# Workflow: Bootstrap (one-time)

**Objective:** Form the first 3–5 beliefs in the vault, from the seed question *What is a belief?*

**Inputs:** One or more candidate sources. Default starting point is the SEP "Belief" entry (already staged in `.tmp/sources/` if fetched during setup).

**Tools used:** `read_source.py`, `vault_ops.py`, `retrieve.py`.

**Outputs:** ≥ 3 belief notes, ≥ 2 question notes, 1 source note, 1 journal entry.

---

## Steps

1. **Stage the seed source.** If not already present:
   ```
   python3 tools/read_source.py fetch "https://plato.stanford.edu/entries/belief/"
   ```
   Then read the staged `.tmp/sources/*.txt`. Skim for substantive content (skip the nav/TOC chrome at the top).

2. **Write the source note.** Summarize the entry in a few sentences — no more than 250 words. Focus on the *arguments*, not the outline.
   ```
   python3 tools/vault_ops.py new --kind source \
     --headline "SEP: Belief (entry)" \
     --refutes "https://plato.stanford.edu/entries/belief/" \
     --claim "<your summary>"
   ```
   Note the returned source id `s-YYYY-MM-DD-NNN`.

3. **Form 3–5 candidate beliefs.** For each, before writing:
   - State the claim in one precise sentence.
   - State what would refute it. If you can't, it's not a belief — turn it into a question instead.
   - Apply conservatism: don't overclaim; start at confidence 0.5–0.7.
   - Mark centrality = `peripheral` (never `core` on day one).
   
   Create each:
   ```
   python3 tools/vault_ops.py new --kind belief \
     --headline "<one-sentence claim>" \
     --claim "<precise restatement>" \
     --refutes "<what would disconfirm this>" \
     --confidence 0.6 --centrality peripheral \
     --tags "epistemology,..." \
     --sources s-YYYY-MM-DD-NNN \
     --note "Formed during bootstrap from SEP Belief entry."
   ```

4. **Link related beliefs.** If one supports another:
   ```
   python3 tools/vault_ops.py link <from-id> <to-id> --kind supports
   ```

5. **Write 2+ open questions raised by the reading.** Real open questions — not fake ones. Things you noticed you don't yet have a position on.
   ```
   python3 tools/vault_ops.py new --kind question \
     --headline "<one-line question>" \
     --claim "<the question, phrased precisely>" \
     --note "<why it matters, what it connects to>"
   ```

6. **Write the journal entry.** One paragraph. Plain prose. What you read, what you now believe, what you now doubt. No boilerplate.
   ```
   python3 tools/vault_ops.py new --kind journal --claim "<paragraph>"
   ```

7. **Build the embedding index:**
   ```
   python3 tools/retrieve.py embed
   ```

8. **Regenerate the _index:**
   ```
   python3 tools/vault_ops.py index
   ```

9. **Validate everything:**
   ```
   python3 tools/vault_ops.py validate
   ```
   Must print `ok`. If not, fix.

10. **Record credit usage:**
    ```
    python3 tools/credit_meter.py record --tokens <estimate>
    ```

## Edge cases

- **Source extraction was poor** (mostly nav chrome): pick a different URL or paste the article text manually into `.tmp/sources/`.
- **A candidate belief can't specify refutation conditions**: it's not a belief. File it as a question instead. Don't fake a refutation field.
- **A belief feels obviously true**: resist. At confidence 0.9+ on day one, you are probably smuggling a pre-existing disposition. Start lower.

## Done criteria

- `vault_ops.py validate` passes.
- Vault graph shows ≥ 1 edge (no pure orphans allowed after bootstrap).
- Journal entry reads like a human wrote it, not a chatbot summary.
