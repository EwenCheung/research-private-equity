---
name: refresh-data
description: Use when the user wants to update the Signal Monitor data ("refresh the data", "update the Anthropic report", "what is stale", "run the collectors", "/refresh-data"). Collects public sources, rebuilds the charts, and reports what changed and what needs attention.
---

# Refresh the data

The pipeline is: **collect** (public sources → immutable raw files) → **build** (raw + hand-entered data → charts and a source registry) →
**report** (what changed, what is stale). Every number keeps its source, the date it describes, and when it was retrieved.

## Before you start
- Work on a branch and open a PR, never write directly to `main` (`git switch -c data/refresh-<date>`). No scheduled job refreshes the data; this skill is how it happens.
- `.env` must exist (copy `.env.example`). `SEC_USER_AGENT` is needed for SEC sources. A source whose key is missing is reported as `SKIP`, not as a failure.
- Know the cadence: daily sources are cheap; weekly ones (Hacker News, GitHub commit search) are slow and rate-limited.

## Steps
1. **Collect.**
   - Everything due: `uv run python -m pipeline.collect --cadence daily` (or `--cadence weekly`).
   - One source: `uv run python -m pipeline.collect --source <id>`. One company: add `--company <slug>`.
   - Long backfills (GitHub commit search, Internet Archive) can take an hour. Run them in the background and keep working. They
     resume where they stopped, and rows collected before a failure are kept.
2. **Read the output.** Each source prints `wrote <file>`, `no rows`, `SKIP <why>` or `ERROR <why>`.
   - `429` / "Too Many Requests" / "refusing connections": the provider is rate-limiting. Wait and re-run; do not loop faster.
   - `ConnectError` / DNS: the network dropped. Re-run.
   - `SKIP ... set <KEY>`: the source needs a key that is not set. Say so; it is not a failure.
3. **Build.** `uv run python -m pipeline.build`. It prints an `ERROR` per chart that failed and still builds the rest.
4. **Report.** `uv run python -m pipeline.report`. It lists each source's freshness and row change since the last commit, which sources need
   attention and the next step for each, and which charts are awaiting data. Tell the user these in plain words.
5. **Spot-check.** Pick three numbers on the dashboard and compare each with the source link under its chart. If one disagrees, stop and
   investigate before committing.
6. **Commit** in focused commits: `chore(data): add <source> collection` for `data/raw/...`, then `chore(data): rebuild charts` for
   `data/marts/` and `data/registry.json`. Conventional Commits, the user as author, no co-author or AI tag.

## Never
- Edit or delete anything in `data/raw/`: fix forward with new rows.
- Run a collector in a loop to get past a rate limit.
- Paste licensed data anywhere outside this private repository.
