# Signal Monitor — private-company alt-data research

An alt-data monitor for private companies, used by the deal team. First company: **Anthropic**, compared against its AI peers.
It is a Python pipeline that collects public signals, plus a FastAPI backend and a React dashboard behind a password.
The full design is in `docs/superpowers/specs/2026-10-05-signal-monitor-design.md`; progress is tracked in `docs/ROADMAP.md`.

Outputs are drafts for deal-team review, not investment advice. Use only public sources and data the team supplies.

## Non-negotiables
1. **Provenance on every number.**
   - Every stored row carries: `source`, `source_url`, `method` (`api` | `scrape` | `manual` | `ledger`), `as_of` (the date the value describes),
     `retrieved_at` (when we fetched or entered it), `tier`, `entity`, `metric`, `value` and `dims`.
   - Manual and ledger rows also carry `entered_by` and `evidence` (a quote, URL or file reference).
   - Never invent, interpolate or round a number without saying so on the chart.
2. **As-of discipline.** Raw snapshots in `data/raw/<source>/<YYYYMMDDTHHMMSSZ>.jsonl.gz` are immutable. Never edit or delete them; fix forward with new rows.
3. **Comparable definitions.**
   - A metric means the same thing for every company and every week.
   - Definitions live in `config/metrics/<page>.yaml`.
   - Changing a definition is a visible, documented change.
4. **Licensed data stays private.**
   - YipitData, M Science and similar data only enter through `/add-manual-data`.
   - Never paste it into issues, PRs, public sites or external services.
5. **Interfaces.** `docs/contracts.md` and `contracts/*.schema.json` define the row, chart-spec and AI-report formats,
   and the module interfaces. Code must conform to them.

## How work is organised
- **Phases are layers.** Implementations inside one phase are independent of each other. Each one gets its own branch
  (`p<N>/<name>`) and worktree (`.worktrees/p<N>-<name>`). Dependencies only flow from one phase to the next.
- **Each implementation is a vertical slice** of one page or function: collectors → marts → page → tests → skill.
- **Branches.** Create implementation branches from `phase/N`. `phase/N` is cut from `phase/N-1` as soon as that phase's PR opens.
  Merge commits keep each phase PR showing only its own changes.
- **Ownership rule.** Page implementation `X` only touches its own files:
  - `pipeline/sources/X.py` and `pipeline/marts/X.py`
  - `config/metrics/X.yaml` and `data/ledgers/X_*.csv`
  - `frontend/src/pages/X.tsx`
  - `tests/test_X.py`
  - `.claude/skills/<its-skill>/`
- **Shared files.**
  - Core files (`pipeline/core/*`, `app/*`, `frontend/src/components/ChartCard.tsx`) change only in their own `fix(core)` PR.
  - Dependencies change only in their own `chore(deps)` PR.
  - `docs/ROADMAP.md` is updated only at phase gates, on `main`.
- **Auto-discovery, never shared registries.** These are all picked up automatically:
  - collectors in `pipeline/sources/*.py`;
  - marts in `pipeline/marts/*.py`;
  - pages in `frontend/src/pages/*.tsx`;
  - metric files in `config/metrics/*.yaml`.
- **Ports.** Worktree `n` serves the API on `8000+n` and Vite on `5173+n`. `main` is `n = 0`; implementation `P.k` uses `n = k`.

## Review checkpoint (every implementation)
1. `uv run pytest` passes.
2. Serve the worktree locally and click through it in the browser.
3. Hand the user the URL and a short "please check" list: what's new, and which numbers to compare against their source links.
4. **Stop. Continue only after the user's OK.** Fixes stay on the same branch.
5. Then open the implementation PR into `phase/N`, and merge it after the user's OK.
6. At the phase gate: serve `phase/N`, get the user's OK, open **one PR `phase/N` → `main`**, and merge it only after OK.
   Then tag `phase-N`, update `docs/ROADMAP.md`, and deploy.

## Git
- Conventional Commits only: `type(scope): summary`. Types: `feat` `fix` `docs` `chore` `refactor` `test` `ci`.
  The scope is the page or function, e.g. `feat(hiring): add greenhouse collector`.
- Small commits; each one leaves the branch working.
- **Never merge directly into `main`.**
  - Implementation branch `pN/<name>` → PR into the phase branch `phase/N`.
  - Phase gate: one PR `phase/N` → `main`.
  - Use merge commits, not squash.
  - The only direct writer to `main` is the scheduled data job, and only for `data/` and `reports/` (design spec, revision R2).
- **The author is the human committer only.** Never add `Co-Authored-By`, "Generated with Claude", or any AI-attribution tag,
  in commits or PR descriptions.

## Commands
Commands become available as phases land; see `README.md`.
