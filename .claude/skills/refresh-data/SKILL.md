---
name: refresh-data
description: Use when the user wants to update the Signal Monitor data ("refresh the data", "update the Anthropic report", "what is stale", "run the collectors", "/refresh-data"). Collects public sources, rebuilds the charts, and reports what changed and what needs attention.
---

# Refresh the data

The pipeline is: **collect** (public sources → immutable raw files) → **build** (raw + hand-entered data → charts and a source registry) →
**report** (what changed, what is stale). Every number keeps its source, the date it describes, and when it was retrieved.

## Before you start
- Work on a branch, never directly on `main` (`git switch -c data/refresh-<date>`), unless the user has said the scheduled job writes to `main`.
- `.env` must exist (copy `.env.example`). `SEC_USER_AGENT` is needed for SEC sources; `SERPAPI_KEY` only for Google Trends. A missing optional
  key is reported as `SKIP`, not as a failure.
- Know the cadence: daily sources are cheap; weekly ones (GDELT, Hacker News, GitHub commit search) are slow and rate-limited.

## Steps
1. **Collect.**
   - Everything due: `uv run python -m pipeline.collect --cadence daily` (or `--cadence weekly`).
   - One source: `uv run python -m pipeline.collect --source <id>`. One company: add `--company <slug>`.
   - Long backfills (GDELT, GitHub commit search, Internet Archive) can take an hour. Run them in the background and keep working. They
     resume where they stopped, and rows collected before a failure are kept.
2. **Read the output.** Each source prints `wrote <file>`, `no rows`, `SKIP <why>` or `ERROR <why>`.
   - `429` / "Too Many Requests" / "refusing connections": the provider is rate-limiting. Wait and re-run; do not loop faster.
   - `ConnectError` / DNS: the network dropped. Re-run.
   - `SKIP ... set SERPAPI_KEY`: expected without the key. Say so; it is not a problem.
3. **Build.** `uv run python -m pipeline.build`. It prints an `ERROR` per chart that failed and still builds the rest.
4. **Report.** `uv run python -m pipeline.report`. It lists each source's freshness and row change since the last commit, which sources need
   attention and the next step for each, and which charts are awaiting data. Tell the user these in plain words.
5. **Spot-check.** Pick three numbers on the dashboard and compare each with the source link under its chart. If one disagrees, stop and
   investigate before committing.
6. **Commit** in focused commits: `chore(data): add <source> collection` for `data/raw/...`, then `chore(data): rebuild charts` for
   `data/marts/` and `data/registry.json`. Conventional Commits, the user as author, no co-author or AI tag.

## Hand-maintained sources (the report names them when they go stale)
- **YipitData / M Science**: `/add-manual-data`.
- **H-1B filings** (quarterly): download the Department of Labor LCA file in a browser, then
  `uv run python -m pipeline.sources.hiring import-lca <file>`.
- **Model releases, API prices, plans, funding rounds, run-rate claims** (ledgers): add rows to the CSV in `data/ledgers/` with a source link and a
  quote copied word for word from the page. Never enter a figure you cannot quote.

## Never
- Edit or delete anything in `data/raw/`: fix forward with new rows.
- Run a collector in a loop to get past a rate limit.
- Paste licensed data anywhere outside this private repository.
