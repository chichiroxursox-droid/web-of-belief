# Lessons — Web of Belief

Patterns learned during this project, to avoid re-discovery.

## 2026-04-24
- **Reuse check paid off.** `embed_sentences.py` and `search_sentences.py` in `/School work/tools/` already implement sentence-transformers retrieval; imported rather than rewrote.
- **No YAML frontmatter utility existed** across projects. Built a thin wrapper in `vault_ops.py` using PyYAML.
- **Journal is a mass noun.** Don't pluralize the directory. Schema-bound globs must use kind-prefix filters (`b-*.md`, `q-*.md`) so human-readable digest files (`digest-YYYY-MM-DD.md`) don't trip validation.
- **Regex `\s*` is too greedy when validating structured fields.** For the refutability check, switched to `[ \t]*` followed by `(.*?)` so the capture stops at the blank line instead of eating through to the next `**Section:**` header.
- **Every non-journal `new` needs an explicit headline.** Made the CLI error fast if it's missing; journal entries synthesize one from the date.
