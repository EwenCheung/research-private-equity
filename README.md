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

## Take everything to a machine with no internet
One command calls every source once and keeps everything in a single SQLite file: the collected data, the cited ledgers, the charts and the built dashboard. A sandbox that blocks all access shows the dashboard from that file alone.

```
online, when you press Refresh or run `fetch`:  sources ─▶ collectors ─▶ new SQLite file ─▶ checked ─▶ replaces the old file
every other time, also in the sandbox:           browser ─▶ backend API ─▶ SQLite file          (nothing is fetched)
```
The dashboard shows the data in the file until you press **Refresh data** on the Data & Methods page. A page load never calls a source.

| Step | Where | Command |
|---|---|---|
| 1. Call everything once, rebuild, pack | online | `uv run python -m pipeline.offline fetch` |
| 2. Copy the repo and `data/offline/signal-monitor.sqlite` | any way you like | `scp`, a USB stick, `git bundle` |
| 3. Show the dashboard from the file | sandbox | `uv run python -m pipeline.offline serve --db signal-monitor.sqlite` |

- `fetch` reads your `.env` for keys (a source that needs a missing key is skipped and named, never fatal), builds the frontend if Node is there, rebuilds the charts and packs. `--only ID ...` and `--skip ID ...` pick sources; the Internet Archive ones are slow. `config/offline.yaml` lists sources left out by default (Wikipedia); `--only wikipedia_pageviews` calls it anyway.
- `serve` needs `DASHBOARD_PASSWORD` and `SESSION_SECRET` (in `.env` or the environment) and nothing else: the API reads the charts and registry from the file (`DATA_DB`) and the page comes out of the file too. No collector runs and no `data/` folder is needed.
- **Refresh data** (Data & Methods page, only when the API serves a file) fetches every source again in the background while the charts keep showing the current data, then replaces the file and reloads the page. It lists every source with `DONE` or a percentage as it goes (`data collected (10/19)`), and `fetch` on the command line prints the same lines. Sources on different services are fetched side by side, while sources on the same service (both SEC ones, both GitHub ones, both Internet Archive ones, …) go one after another, so no service sees more traffic than before; `fetch --workers 1` turns that off. Sources that failed are listed under the button with **Retry the N failed sources**, which fetches only those again (`fetch --only ID ...` does the same on the command line). Times the dashboard shows are Singapore time (SGT). A refresh never makes the file worse:
  - no internet: nothing is called and the file stays;
  - a source that errors keeps the data it had. What it collected before the error goes to `data/raw/_rejected/`, never deleted;
  - a failed build, a damaged file, or a raw snapshot or chart that would be lost or changed rolls the whole refresh back;
  - otherwise the old file is kept as `<file>.previous` and the report of the run as `<file>.refresh.json`.
- Other commands: `pack` makes the file from what is already in `data/` without calling anything; `restore` unpacks the raw snapshots, ledgers, charts and registry into `data/` (so `pipeline.build` runs offline too, and a raw file that differs is never overwritten); `check` says whether the file and `data/` still agree.
- The file holds every collector snapshot byte for byte, and every observation as a row. Query it with any SQLite tool, for example `sqlite3 data/offline/signal-monitor.sqlite "select entity, metric, max(as_of) from observations group by 1, 2"`.
- Vendor files in `data/manual/` stay out unless you pass `--include-manual`: licensed data stays private.
- The sandbox still needs Python 3.11 with the project's packages (`uv sync` needs a package index, so install them while one is reachable, or copy a `.venv` built on the same operating system). Two things on the page reach for the internet and are blank or in system fonts there: the two live leaderboard embeds on Product & Reliability, and the web fonts.

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
