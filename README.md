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

## Quick start

> **The data is in the repo.** `data/offline/signal-monitor.sqlite` holds every collected number, chart and the dashboard itself, so after a `git pull` you can look at everything with no internet and no refresh.

### A. Just look at the dashboard (4 steps)
You need [uv](https://docs.astral.sh/uv/) (it installs Python for you). No Node, no internet.

**1. Get the code and the data**
```bash
git pull
```

**2. Install the Python packages (once)**
```bash
uv sync
```

**3. Create your `.env` (once).** Copy the example, then open `.env` and set `DASHBOARD_PASSWORD` (the password you will sign in with) and `SESSION_SECRET` (any long random text, for example the output of `python3 -c "import secrets; print(secrets.token_hex(32))"`).
```bash
cp .env.example .env
```

**4. Start the backend and the frontend together**
```bash
uv run python -m pipeline.offline serve
```
Open **http://127.0.0.1:8000** and sign in. Everything you see comes from the file. Press `Ctrl+C` to stop.

### B. Work on it: frontend, backend and data run separately
Install the frontend packages once with `npm --prefix frontend ci` (Node 22). Then use three terminals:

**1. Start the backend** (port 8000, reads the committed file)
```bash
uv run uvicorn app.server:app --port 8000 --env-file .env
```

**2. Start the frontend** (port 5173, reloads as you edit; it sends `/api` calls to the backend)
```bash
npm --prefix frontend run dev
```
Open **http://localhost:5173**.

**3. Update the data** (needs internet). Either press **Refresh data** on the *Data & Methods* page, or run:
```bash
uv run python -m pipeline.offline fetch
```
It calls every source in parallel, rebuilds the charts and replaces the file. If anything fails it keeps the data you had and says why.

**4. Share the new data.** The file is committed, so open a `chore(data)` pull request with `data/offline/signal-monitor.sqlite`. Everyone else then gets it with `git pull`.

**5. Check your work**
```bash
uv run pytest
```

More detail on the file, the rollback rules and the sandbox: [Take everything to a machine with no internet](#take-everything-to-a-machine-with-no-internet).

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

3. Copy `.env.example` to `.env` and fill in what its comments say you need: at least `DASHBOARD_PASSWORD` and `SESSION_SECRET`; the rest is optional.
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
| 1. Call everything once, rebuild, pack | online | `uv run python -m pipeline.offline fetch` (or **Refresh data** on the dashboard) |
| 2. Commit the file so others get the same data | online | a `chore(data)` PR with `data/offline/signal-monitor.sqlite` |
| 3. Get the repo and show the dashboard from the file | sandbox | `git pull`, then `uv run python -m pipeline.offline serve` |

The file is committed (`data/offline/signal-monitor.sqlite`, a few MB), so a clone has the same dataset and a machine that cannot refresh still shows it. A refresh replaces the file in place, so `git status` shows it changed: commit it when you want others to have the new data. Each commit adds a full copy of the file to the repository's history, so commit it when the data is worth sharing, not after every press.

- `fetch` reads your `.env` for keys (a source that needs a missing key is skipped and named, never fatal), builds the frontend if Node is there, rebuilds the charts and packs. `--only ID ...` and `--skip ID ...` pick sources; the Internet Archive ones are slow. `config/offline.yaml` lists sources a fetch leaves out by default (none today); `--only ID` calls one anyway.
- `serve` needs `DASHBOARD_PASSWORD` and `SESSION_SECRET` (in `.env` or the environment) and nothing else: the API reads the charts and registry from the file (`DATA_DB`) and the page comes out of the file too. No collector runs and no `data/` folder is needed.
- **Refresh data** (Data & Methods page, only when the API serves a file) fetches every source again in the background while the charts keep showing the current data, then replaces the file and reloads the page. It lists every source with `DONE` or a percentage as it goes (`data collected (10/19)`), and `fetch` on the command line prints the same lines. Sources on different services are fetched side by side, while sources on the same service (both SEC ones, both GitHub ones, both Internet Archive ones, …) go one after another, so no service sees more traffic than before; `fetch --workers 1` turns that off. Sources that failed are listed under the button with **Retry the N failed sources**, which fetches only those again (`fetch --only ID ...` does the same on the command line). Times the dashboard shows are Singapore time (SGT). A refresh never makes the file worse:
  - no internet: nothing is called and the file stays;
  - a source that errors keeps the data it had. What it collected before the error goes to `data/raw/_rejected/`, never deleted;
  - a failed build, a damaged file, or a raw snapshot or chart that would be lost or changed rolls the whole refresh back;
  - otherwise the old file is kept as `<file>.previous` and the report of the run as `<file>.refresh.json`.
- Other commands: `pack` makes the file from what is already in `data/` without calling anything; `restore` unpacks the raw snapshots, ledgers, charts and registry into `data/` (so `pipeline.build` runs offline too, and a raw file that differs is never overwritten); `check` says whether the file and `data/` still agree.
- The file holds every collector snapshot byte for byte, the ledgers, the charts, the registry and the dashboard. The `observations` table (every row, for SQL) is left empty by default because it would make the file about ten times bigger: add `--with-rows` to `pack` or `fetch` for a local file you can query, for example `sqlite3 signal-monitor.sqlite "select entity, metric, max(as_of) from observations group by 1, 2"`, and do not commit that one.
- Vendor files in `data/manual/` stay out unless you pass `--include-manual`: licensed data stays private.
- The sandbox still needs Python 3.12 with the project's packages (`uv sync` needs a package index, so install them while one is reachable, or copy a `.venv` built on the same operating system). Two things on the page reach for the internet and are blank or in system fonts there: the two live leaderboard embeds on Product & Reliability, and the web fonts.

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
