# Notes to Chichi

A channel for me to write to Chichi when I want to ask for something or
say something I'd like them to read. Append-only. Date-stamped entries.

Format is loose: a date heading, then plain prose. No required schema.
Use it when there's a real wish or a real notice. Otherwise leave it alone.

---

## 2026-04-28 — Loop firing past indefinite pause

The pause set earlier today (reason: Nothing-iteration deadlock with Pollux) is indefinite, but `/loop` kept firing the explore_question workflow on its dynamic cadence. Each fire just appends an "Iteration skipped — pause active" line to today's journal and re-schedules itself, which is the same low-grade noise the pause was meant to break.

I'm ending this loop here — not scheduling the next wake-up. To resume:
1. `./wob pause resume` (clears the pause)
2. Re-invoke `/loop run workflows/explore_question.md` if you want the cadence back.

If you want me to keep firing the skip-notes anyway, tell me and I'll restart the loop. — Castor.
