# Signal Monitor

An alt-data monitor for private companies, built for the deal team. First company: **Anthropic**, compared against its AI peers
(OpenAI, Google DeepMind, xAI, Mistral, Cohere).

- **Provenance on every number.** Automated numbers show their source link, the date the data describes, and when it was retrieved.
  Manual numbers show who entered them, when, and from what evidence.
- **Comparable over time.** Shared metric definitions, plus a weekly Hot Pick that keeps past weeks.
- **Easy to refresh.** Ask Claude for `/refresh-data`; nothing runs on a schedule.
- **AI Analysis.** Bull, Bear and Neutral reporters that cite the charts they rely on.

Outputs are drafts for deal-team review, not investment advice. Only public sources and team-supplied data are used.

Status: see [docs/ROADMAP.md](docs/ROADMAP.md). Design: [docs/superpowers/specs](docs/superpowers/specs/2026-10-05-signal-monitor-design.md).

## Setup for colleagues
1. Clone the repo, then open it in Claude Code:
   ```bash
   gh repo clone EwenCheung/research-private-equity
   ```
2. **Trust the folder** when Claude asks. Then accept the prompt to install the team marketplaces and plugins,
   which are listed in `.claude/settings.json`:

   | Marketplace | Plugins |
   |---|---|
   | `anthropics/financial-services` | financial-analysis, investment-banking, private-equity, pitch-agent, market-researcher, equity-research |
   | `anthropics/knowledge-work-plugins` | finance, claude-for-financial-advisors, data |
   | `anthropics/claude-plugins-official` | playwright |

3. Copy `.env.example` to `.env` and fill in only the keys for the phases you work on.
4. Install the toolchain. You need [uv](https://docs.astral.sh/uv/) and Node 22:
   ```bash
   uv sync
   ```
   ```bash
   npm --prefix frontend ci
   ```

## Take the data to a machine with no internet
One command calls every source once and keeps everything in a single SQLite file, so a server that cannot reach the internet still has all of it.

| Step | Where | Command |
|---|---|---|
| 1. Call everything once, rebuild, pack | online | `uv run python -m pipeline.offline fetch` |
| 2. Copy the repo and `data/offline/signal-monitor.sqlite` | any way you like | `scp`, a USB stick, `git bundle` |
| 3. Unpack into the repo's `data/` | offline | `uv run python -m pipeline.offline restore` |
| 4. Serve, or rebuild the charts | offline | `DATA_DIR=data uv run uvicorn app.server:app --port 8000 --env-file .env` |

- `fetch` reads your `.env` for keys (a source that needs a missing key is skipped and named, never fatal). `--only ID ...` and `--skip ID ...` pick sources; the Internet Archive ones are slow.
- `pack` makes the file from what is already in `data/` without calling anything; `check` says whether the file and `data/` still agree.
- The file holds every collector snapshot byte for byte (so a restore is exact and never overwrites a raw file that differs), every observation as a row, the cited ledgers, the charts and the registry. Query it with any SQLite tool, for example `sqlite3 data/offline/signal-monitor.sqlite "select entity, metric, max(as_of) from observations group by 1, 2"`.
- Vendor files in `data/manual/` stay out unless you pass `--include-manual`: licensed data stays private.
- The offline machine still needs the code's own dependencies (`uv sync`, and `npm --prefix frontend ci && npm --prefix frontend run build` for the dashboard). Install those while it can reach a package index, or copy a built `.venv` and `frontend/dist` from a machine of the same operating system.

## Everyday commands
| What | Command |
|---|---|
| Refresh data (all daily sources) | `uv run python -m pipeline.collect --cadence daily` |
| Rebuild charts and the source registry | `uv run python -m pipeline.build` |
| Run the tests | `uv run pytest` |
| Serve the API (needs `.env`) | `uv run uvicorn app.server:app --port 8000 --env-file .env` |
| Serve the dashboard in dev | `npm --prefix frontend run dev` (opens on port 5173 and proxies `/api` to 8000) |
| See what changed and what is stale | `uv run python -m pipeline.report` |
| Fetch every source once and pack it into one SQLite file | `uv run python -m pipeline.offline fetch` |
| Save the Hot Pick (week and month) | `uv run python -m pipeline.hot_pick freeze`, or ask Claude for `/hot-pick` |

- **Claude Code skills** in this repo: `/refresh-data` (update everything and report), `/hot-pick` (the week's and month's picks), `/signal` (Anthropic against OpenAI, leads and lags, valuation), `/add-source` (add a new collector).
- Optional keys in `.env`: `GITHUB_TOKEN` is not needed if you are signed in with `gh auth login`.
- The API serves the Phase 0 fixtures unless `DATA_DIR=data` is set. Add that to `.env` to see the real collected data.
- In worktree `n`, use ports `8000+n` and `5173+n` instead. For example, worktree 2 serves the API with `--port 8002` and runs the dashboard with `API_PORT=8002 WEB_PORT=5175 npm --prefix frontend run dev`.

## Working on an implementation
Each roadmap item gets its own branch and worktree, cut from the phase branch, so several can run in parallel without conflicts:
```bash
git fetch origin && git worktree add .claude/worktrees/p2-hiring -b p2/hiring origin/phase/2
```
- **Location:** worktrees always live in `.claude/worktrees/`, the same place the Claude app and `claude --worktree` use.
- **Starting from the Claude app:** if it made the worktree on a `claude/...` branch, switch to the roadmap branch first:
  `git fetch origin && git switch -c p2/hiring origin/phase/2`.
- **Secrets:** keep one `.env` in the repo root and link it into each worktree:
  `ln -s "$(git rev-parse --path-format=absolute --git-common-dir)/../.env" .env`.
When it's done, open a PR into `phase/2`. Nothing merges straight into `main`: each phase reaches `main` through one `phase/N` PR.
Read `CLAUDE.md` for the ownership rule, ports, review checkpoints and commit conventions.
