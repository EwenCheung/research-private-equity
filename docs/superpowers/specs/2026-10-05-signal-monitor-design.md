# Signal Monitor — design spec (approved 2026-10-05)

## Context
You want an alt-data monitor for private companies, modelled on the GIC case-study site
(gic-tig.vercel.app, "Momentum Monitor" on Databricks), and better than it. The first company is **Anthropic**.
The deal team uses it: it must be repeatable, long-term, comparable, easy to update, and every number must carry its provenance.
Colleagues all use Claude, so the repo ships the same plugins, skills and agents.
The repo is currently empty and **public**, so it gets made private.

Decisions so far:
- Hosted, password-protected dashboard.
- GitHub Actions refresh.
- Python backend; React frontend.
- **Public data only for now.** YipitData and M Science get placeholders that you fill by pasting data in.
- **Monitor, not model.** Full history plus arithmetic insights; no ML forecast.
- Plugins auto-install through `.claude/settings.json`.
- **Quality over build time.** No time estimates anywhere.
- Phases are layers of **independent implementations**, so each can run in its own worktree.
- **A review checkpoint after every implementation.** You check it in the frontend before work continues.

Everything here comes from public sources and data the team supplies; no inside information is used.
All outputs, including the AI reporters, are drafts for deal-team review, not investment advice.

## Goals (these drive every design choice)
1. **Repeatable.** Clone, then `/refresh-data` or `uv run python -m pipeline.collect`, gives the same report.
   Every number traces to an immutable raw snapshot.
2. **Long-term and comparable.**
   - **Definitions:** one metric dictionary applies across time and across companies.
   - **Weekly editions:** each week is frozen, so any two dates can be compared.
3. **Easy or near-real-time updates.**
   - **Automated sources** refresh on a schedule or on demand.
   - **Manual sources** are a single paste into Claude.
4. **Provenance on every number.**
   - **Automated:** "Source: npm downloads API ↗ · data as of 2026-10-04 · retrieved 2026-10-05 06:02 UTC".
   - **Manual:** "MANUAL · updated 2026-10-05 by Ewen · YipitData weekly report, p.3", with a badge that goes amber or red when stale.

## What the reference does (reviewed all 12 pages)
- **Layout.** 7 evidence tabs: Briefing, Hiring, Dev Adoption, Attention, Product, Customers, Capital.
  Five more pages: AI Market, Competitor Basket, Model, Data & Methods, Universe (30 companies).
- **Data.** About 60 collectors: Greenhouse jobs plus a Wayback backfill to 2019, peer ATS boards, H-1B LCA filings,
  PyPI/npm/conda/Maven/Docker/VS Code downloads, GitHub, GDELT, Google Trends, HN/Reddit/Wikimedia,
  SEC full-text, Form D and N-PORT fund marks, USASpending, status page.
  Its best customer signal: other companies' job posts that name Databricks.
- **Ledgers.** Hand-curated ARR milestones, funding rounds and KPI claims, each with source tier, URL and quote.
- **On every chart:** a table toggle, "as of" date, "Assumptions & detail", a one-line takeaway, and a per-source freshness panel.
- **The reference's own stated gaps:** few dated ARR points, thin job-board history, no LinkedIn headcount, and no analysis layer.

## Data: the Anthropic signal pack (all free and public; endpoints tested live 2026-10-05)

| Page | Signal | Source |
|---|---|---|
| Hiring | Open roles, role mix (Research / Eng / Applied AI-FDE / GTM / Safety-Policy / Compute / G&A), location mix; same for OpenAI, xAI, Mistral, Cohere, DeepMind | Greenhouse (640 today) / Ashby (OpenAI 828) / Lever APIs, plus Wayback backfill of the careers pages |
| Hiring | H-1B filings and median offered wage vs peers | DOL LCA quarterly disclosure files (~79 MB each; keep only tracked employers' rows) |
| Dev adoption | `anthropic` vs `openai` vs `google-genai` downloads and share | ClickHouse public PyPI dataset (full history, no key; verified) + pypistats (recent), npm downloads API (daily since 2023) |
| Dev adoption | Claude Code installs; public commits co-authored by Claude per week, shown as a smoothed **index** | npm API; GitHub search API (noisy: 8.0M vs 9.2M for the same week, `incomplete_results`), with GH Archive via ClickHouse evaluated as a steadier source |
| Dev adoption | Ecosystem: MCP SDK downloads, MCP-server repos, `anthropics` org stars and contributors, VS Code extension installs | npm, GitHub, VS Code Marketplace |
| Attention | Wikipedia pageviews, Google Trends via SerpAPI (Claude vs ChatGPT vs Gemini), App Store top-chart rank (snapshot; Claude #19, ChatGPT #3, Gemini #14 in US top-free on 2026-10-05), news volume and coverage list, HN/Reddit | Wikimedia, SerpAPI Trends, iTunes top-charts RSS, GDELT + Google News RSS |
| Product | Model release timeline, API price-per-Mtok history, plan tiers | Cited ledger + Wayback snapshots of the pricing page |
| Product | Reliability: incidents and degraded minutes per week (capacity-strain proxy) | status.claude.com API |
| Customers | Companies hiring for Claude/Anthropic skills (corpus of ~500 ATS boards) | Greenhouse/Lever/Ashby boards |
| Customers | 10-K/10-Q filers naming Anthropic/Claude, by sector | SEC EDGAR full-text |
| Customers | Federal obligations; company-stated KPI claims | USASpending (cited), KPI ledger |
| Capital | Funding rounds, post-money valuation, run-rate ledger | Cited ledger + SEC Form D |
| Capital | Mutual-fund marks of Anthropic shares (837 filings) | SEC N-PORT |
| Capital | Strategic holders' disclosures | Amazon / Alphabet 10-Q mentions (EDGAR) |
| Licensed alt-data | YipitData and M Science panels | Placeholder CSVs, filled through `/add-manual-data` |

Peers (OpenAI, Google DeepMind, xAI, Mistral, Cohere) get the same signal pack wherever a source exists.
Each Anthropic number then also reads as a share.

## Arithmetic insights (labelled "arithmetic, not a model")
- Run-rate CAGR and doubling time from the ARR ledger.
- Log-linear extrapolation to year-end, with a residual band.
- EV / run-rate multiple at each round and against peers.
- N-PORT fund marks: per-fund $/share and its % change since the mark nearest the last round. An implied valuation only appears when a share count is cited in the ledger.
- Anthropic's share of SDK downloads and hiring across peers.
- GTM-to-R&D hiring ratio and international share of roles.
- Tripwires: week-over-week moves larger than 2σ against the trailing 12 weeks.

## AI Analysis page: Bull, Bear and Neutral reporters
These are three analyst agents. All three read the **same evidence pack**. The pack contains:
- Every chart's key numbers, takeaway and deltas against the prior edition.
- Tripwires, ledgers and arithmetic insights.
- Freshness status, so stale sources are flagged and not over-weighted.
- Notices for placeholder sources, so missing Yipit/M Science data is never invented.

**Bull and Bear** each produce structured output:
- A headline thesis.
- 4–8 arguments. Each has a claim, its evidence (`chart_id`, `metric`, `value`, `as_of`, `source`),
  a strength rating (strong, moderate or weak), and what would invalidate it.
- Valuation implications.
- Catalysts to watch.

**Neutral** runs after both and gets their outputs plus the pack. It produces:
- A point-by-point adjudication of the key debates.
- A net read, with a confidence level.
- What to watch next edition.
- Data gaps that limit confidence.

Prompt structure borrows from the private-equity `ic-memo` skill and the equity-research `thesis-tracker` and `catalyst-calendar` skills.

There is one prompt per reporter, and two ways to run it. `.claude/agents/{bull,bear,neutral}-reporter.md` is the single source of each prompt:
- Colleagues run them interactively in Claude Code, with all plugins available.
- `pipeline/analysis.py` reads the same file body for the scheduled run, through the Claude API (`claude-opus-5-5`) with structured outputs.

Quality gates. The run fails and retries, rather than publishing, when any of these fail:
1. Every cited `chart_id` or metric must exist.
2. Every quoted number must match the mart value, allowing for rounding.
3. Placeholder or never-updated sources cannot be cited.

Stale citations render with an amber badge.

Provenance on AI content:
- Each report is labelled "AI-generated · claude-opus-5-5 · generated <ts> · evidence as of <date> · edition 2026-W41".
- Every claim links to its chart.
- Reports are frozen per edition, and the page shows how each case changed: new, dropped, strengthened or weakened arguments.

When it runs:
- In the weekly job.
- In the daily job, only when a tripwire fires.
- On demand through `/run-analysis`.

## Provenance & freshness model
Every stored row carries the following fields:

| Field | Meaning |
|---|---|
| `source` | Registry key, e.g. `npm_downloads` |
| `source_url` | Exact endpoint or page used |
| `method` | `api`, `scrape`, `manual` or `ledger` |
| `as_of` | The date the value describes |
| `retrieved_at` | When we fetched or entered it |
| `tier` | `company-stated`, `press`, `vendor`, `filing` or `derived` |
| `entered_by` | Manual rows only |
| `evidence` | Manual rows only; quote, URL or file reference |

Each collector module declares its sources' metadata next to the code: URL, method, tier, expected cadence (the freshness SLA) and caveats.
The registry auto-discovers them.
`build` compares the SLA against `retrieved_at` to colour the freshness badge (green, amber or red).
The Data & Methods page is generated from the registry.

Manual inputs go through `/add-manual-data`:
- Claude validates the rows and stamps `entered_by`, `entered_at` and `evidence`.
- Git history is the audit trail.

On "real-time": most sources only publish at daily resolution. So:
- **Daily automated refresh** covers jobs, downloads, GitHub, App Store ranks, the status page and news.
- **Weekly refresh** covers SEC and Trends. **Quarterly:** H-1B LCA files.
- **On demand,** anyone can run `gh workflow run daily.yml` or `/refresh-data`.

## Comparability for the deal team
- **Metric dictionary.** `config/metrics/<page>.yaml` holds one canonical definition, unit and formula per metric.
  The files are merged at load, and every company is computed the same way.
- **Weekly editions.** Each week is frozen in `reports/YYYY-Www/`: marts, the 3 AI reports, and `data_pack.xlsx` with provenance columns.
  A "Compare to" picker shows WoW, MoM and YoY deltas against any edition.
- **Exports.**
  - Every chart: CSV download.
  - The Briefing and AI Analysis pages: a print stylesheet for PDF.

## Architecture: parallel-safe by design
- **One tidy schema:** `{source, source_url, method, as_of, retrieved_at, tier, entity, metric, value, dims}`.
- **Raw snapshots:** immutable, gzip-compressed JSONL in `data/raw/<source>/<YYYYMMDDTHHMMSSZ>.jsonl.gz`. Large corpora store only the rows we use (see revisions).
- **Marts:** written to `data/marts/<chart>.json`. Each mart is a chart spec: title, rows, as_of, sources, assumptions and takeaway.
- **Backend:** Python.
  - The pipeline is the `collect`, `build` and `analysis` CLIs.
  - The **FastAPI** app holds the password session (`DASHBOARD_PASSWORD`) and serves `/api/marts/{id}`, `/api/registry`, `/api/freshness`, `/api/compare` and `/api/analysis`, plus the built frontend.
- **Frontend:** **React + Vite + TypeScript**, with Observable Plot for charts (dataviz skill).
  One generic `<ChartCard>` renders any mart: table toggle, CSV, provenance line, MANUAL badge, freshness badge, assumptions, takeaway.
- **Hosting:** a Render web service from `render.yaml`. It auto-deploys on every data commit.
- **Storage:** files in the private repo.
  `# ponytail: files-in-git; move to Postgres once data passes ~1GB or needs concurrent writers.`

**Auto-discovery removes shared edit points.** That is what lets the implementations in one phase run in parallel worktrees without merge conflicts:
- The collector registry imports every `pipeline/sources/*.py`.
- `build` runs every `pipeline/marts/*.py`.
- The frontend router loads every `frontend/src/pages/*.tsx` through `import.meta.glob`; each page exports `{title, order}`, which builds the nav.
- The metric dictionary merges `config/metrics/*.yaml`.

**Ownership rule.** A page implementation `X` only creates or edits its own files:
- `pipeline/sources/X.py` and `pipeline/marts/X.py`
- `config/metrics/X.yaml` and `data/ledgers/X_*.csv`
- `frontend/src/pages/X.tsx`
- `tests/test_X.py`
- `.claude/skills/<its-skill>/`

Shared files have these rules:
- **Core files** (`pipeline/core/*`, `app/*`, `ChartCard`) belong to Phase 1. A later fix to them goes in its own `fix(core)` PR.
- **Python and JS dependencies** are all declared in Phase 1, so lockfiles don't conflict.
- **`docs/ROADMAP.md`** is only updated at phase gates on `main`.

```
CLAUDE.md · README.md · .env.example · render.yaml · .claude/launch.json (ports via env, one pair per worktree)
.claude/settings.json         marketplaces + 10 enabledPlugins + permission allowlist
.claude/agents/               bull-reporter.md · bear-reporter.md · neutral-reporter.md
.claude/skills/               refresh-data · add-source · add-manual-data · add-company · run-analysis
docs/ROADMAP.md · docs/contracts.md · contracts/*.schema.json
config/companies/anthropic.yaml · config/metrics/<page>.yaml
pipeline/core/                schema, registry, collect/build CLIs, freshness, company loader
pipeline/sources/<page>.py    pipeline/marts/<page>.py    pipeline/editions.py    pipeline/analysis.py
app/server.py                 FastAPI
frontend/src/components/ChartCard.tsx · frontend/src/pages/<Page>.tsx
tests/fixtures/ · tests/test_<area>.py
data/raw/ data/ledgers/ data/manual/ data/marts/ · reports/YYYY-Www/
.github/workflows/daily.yml · weekly.yml
```

`.claude/settings.json` enables these plugins. Colleagues get an install prompt when they trust the folder.

From `anthropics/financial-services`:
- `financial-analysis`
- `investment-banking`
- `private-equity`
- `pitch-agent`
- `market-researcher`
- `equity-research`

From `anthropics/knowledge-work-plugins`:
- `finance`
- `claude-for-financial-advisors`
- `data`

From `claude-plugins-official`:
- `playwright`

## Phase plan: layers of independent implementations
- **Dependencies go only from one phase to the next.**
- **Implementations in the same phase never depend on each other.** Each one gets its own branch and worktree (`p<N>/<name>`, e.g. `p2/hiring`), and each is a vertical slice of one page or function.
- **Progress** is tracked in `docs/ROADMAP.md` (☐ / ◐ / ☑ per implementation and phase).

### Phase 0: Foundations (no dependencies)
**0.1 Team environment** (`p0/team-env`)
- [ ] Make the repo private.
- [ ] `.claude/settings.json` with the 10 plugins and the allowlist.
- [ ] `CLAUDE.md`: provenance rules, ownership rule, commit rules.
- [ ] `README.md` setup section, `.env.example`, `docs/ROADMAP.md`.
- [ ] This plan saved as `docs/superpowers/specs/2026-10-05-signal-monitor-design.md`.
- Done when: a fresh clone opened in Claude prompts to install the 10 plugins.

**0.2 Contracts and fixtures** (`p0/contracts`)
- [ ] `docs/contracts.md` and `contracts/*.schema.json`, covering:
  - the observation row and source metadata;
  - the chart spec (mart);
  - the AI report;
  - the collector, mart and page module interfaces;
  - the CLI commands, API routes and port convention.
- [ ] `tests/fixtures/` with sample marts, including a manual one and a stale one.
- [ ] `tests/test_contracts.py`.
- Done when: the fixtures validate against the schemas, and you've approved the contract doc.

### Phase 1: Platform (depends on Phase 0)
**1.1 Data core** (`p1/data-core`)
- Uses the dependencies already declared in 0.2 (R4); it never edits `pyproject.toml` or `uv.lock`.
- [ ] `pipeline/core/*`: schema validation, auto-discovering registry, `collect` and `build` CLIs, freshness, company config loader.
- [ ] `config/companies/anthropic.yaml` (including peers).
- [ ] `daily.yml` and `weekly.yml` (collect → build → commit). The cron trigger stays off until R2 is decided; `workflow_dispatch` works from day one.
- [ ] `pipeline/sources/snapshots.py`: raw-only daily capture of App Store top-chart ranks (OpenRouter dropped: its terms forbid scraping).
  Data quality: these can't be backfilled, so their history starts the moment this merges.
- [ ] `tests/test_core.py`, which rejects rows missing provenance.

**1.2 Web core** (`p1/web-core`), built against the Phase 0 fixtures:
- [ ] `frontend/package.json` with every JS dependency (the only JS dependency file).
- [ ] FastAPI `app/server.py`: password session, marts, registry and freshness API, static serving.
- [ ] React shell: auto-discovered pages, nav, login.
- [ ] `ChartCard` with every provenance feature.
- [ ] A "Sample" page rendering the fixtures.
- [ ] `render.yaml` and `.claude/launch.json`.
- [ ] `tests/test_api.py`.
- Review focus: the look and the provenance line, before every page copies them.

### Phase 2: Evidence pages (depends on Phase 1; 8 parallel implementations)
Each implementation covers its page's collectors with backfills, ledgers, marts, insights, peer series, page, tests and skill.

| # | Branch | Page / function | Includes |
|---|---|---|---|
| 2.1 | `p2/hiring` | Hiring & Talent | ATS boards, Wayback backfill, H-1B LCA, role/location classifier, GTM:R&D, international share |
| 2.2 | `p2/dev-adoption` | Developer Adoption | PyPI (ClickHouse + pypistats), npm, co-authored-commit index, GitHub org, MCP, VS Code, SDK share |
| 2.3 | `p2/attention` | Consumer & Attention | Wikimedia, Trends via SerpAPI (awaiting data without a key), App Store charts, GDELT/News, HN/Reddit |
| 2.4 | `p2/product` | Product, Pricing & Reliability | model-release ledger, pricing history via Wayback, status-page incidents |
| 2.5 | `p2/customers` | Customers & Contracts | hiring-for-Claude corpus, SEC 10-K/10-Q mentions, USASpending, KPI ledger |
| 2.6 | `p2/capital` | Capital & Valuation | ARR + funding ledgers, Form D, N-PORT marks and their change, Amazon/Alphabet 10-Qs, EV/run-rate, CAGR, doubling time |
| 2.7 | `p2/licensed-data` | Licensed Alt-Data + manual input | Yipit/M Science placeholder panels, `/add-manual-data` skill |
| 2.8 | `p2/data-methods` | Data & Methods + refresh | registry page, gaps list, `/refresh-data` and `/add-source` skills |

### Phase 3: Cross-page synthesis (depends on all Phase 2 marts; 3 parallel implementations)
**3.1 Peers page and add-company** (`p3/peers`)
- [ ] Share-of-signal comparisons across peers, using the metric dictionary.
- [ ] `/add-company` skill.
- Done when: adding a company config makes it appear on every page.

**3.2 Briefing page** (`p3/briefing`)
- [ ] Headline numbers.
- [ ] ARR chart with the log-linear extrapolation band.
- [ ] Tripwires across all series: "what changed".
- [ ] Links into each page.

**3.3 Weekly editions and comparability** (`p3/editions`)
- [ ] `pipeline/editions.py`: freeze `reports/YYYY-Www/` and write `data_pack.xlsx`.
- [ ] `/api/compare`.
- [ ] "Compare to" picker. This is the only Phase 3 implementation allowed to touch `ChartCard`.
- [ ] Print stylesheet.

### Phase 4: AI Analysis (depends on Phase 3: tripwires, edition deltas, peers)
**4.1 AI Analysis page** (`p4/ai-analysis`)
- [ ] Evidence pack.
- [ ] The 3 reporter agents.
- [ ] `analysis.py` with the gates.
- [ ] The page itself, with case history.
- [ ] `/run-analysis` skill.
- [ ] Daily tripwire trigger, and reports included in each weekly edition.
- Done when: all 3 reports pass the gates, and a planted fake number gets rejected.

### Phase 5: Team release (depends on Phase 4)
**5.1 Handover** (`p5/release`)
- [ ] Final README walkthrough.
- [ ] A fresh-clone test run as a colleague would, covering `/refresh-data`, `/add-manual-data` and `/run-analysis`.
- [ ] Production deploy check on Render.

## Review checkpoints
**Per implementation.** I stop and wait for your OK at the end of each one.
1. `uv run pytest` passes for that implementation.
2. I serve **its worktree** locally on its own port pair (API `8000+n`, Vite `5173+n`), so parallel worktrees don't clash.
   I click through it myself in the browser pane first.
3. I hand you the URL with a short "please check" list: what's new on screen, and which numbers to compare against their source links.
4. **I continue only after your OK.** Fixes stay on the same branch.
5. Then I open the implementation PR into `phase/N`, and merge it after your OK.

**Per phase gate.** Once every implementation in the phase is merged:
1. I serve `phase/N` and you do a quick check that everything works together.
2. Then I open **one phase PR, `phase/N` → `main`**, and merge it only after your OK.
3. After it merges: tag `phase-N`, mark it ☑ in `docs/ROADMAP.md`, and deploy to Render.

## Git conventions
- Conventional Commits only: `type(scope): summary`.
  - Types: `feat`, `fix`, `docs`, `chore`, `refactor`, `test`, `ci`.
  - Scope is the page or function, e.g. `feat(hiring): add greenhouse collector`, `ci(daily): schedule collectors`, `docs(roadmap): close phase 2`.
- Small, focused commits, each one leaving the branch working.
- **No direct merges to `main`.**
  - Implementation branches (`pN/<name>`) open PRs into the phase integration branch `phase/N`.
  - At the phase gate, one PR `phase/N` → `main`.
  - Merge with merge commits, not squash, so the conventional history survives.
  - The only direct writer to `main` is the scheduled data job, and it only touches `data/` and `reports/` (see revision R2).
- **Author is only you** (EwenCheung).
  No `Co-Authored-By`, no "Generated with Claude", and no AI tags in commits or PR descriptions. This overrides the default attribution.

Out of scope, by your decisions or because the data is missing:
- An ML forecast or backtest.
- Paid APIs (no licences yet).
- LinkedIn headcount (no public API).

## Inputs needed from the team
- **To start:** nothing. Every review checkpoint runs locally.
- **For deploy (Phase 1 gate):**
  - A Render account (an always-on web service is about $7/mo) and a `DASHBOARD_PASSWORD`.
  - You set them in Render and GitHub Secrets.
- **For Phase 4:** an `ANTHROPIC_API_KEY`.
- **Optional:** each of these closes a specific data gap.
  - YipitData and M Science extracts (through `/add-manual-data`).
  - Secondary prices from Forge, Caplight or Hiive.
  - Similarweb or Sensor Tower.
  - Revelio or Coresignal.
  - A PitchBook login.
  - A SerpAPI key.

## Risks
- **Google Trends** has no free, reliable API (`pytrends` is archived). Mitigation: SerpAPI key; without one, the chart shows "awaiting data".
- **Terms of use.** Each scraped source is checked against its terms before it's enabled. OpenRouter forbids scraping, so it's excluded.
- **GitHub search counts** are approximate and rate-limited. Mitigation: one spaced query per window, store the `incomplete_results` flag, and show a smoothed index.
- **The ARR ledger** is mostly press-reported. Every entry carries its tier and quote.
- **AI reporters can overstate.** Mitigated by the citation and number gates, freshness flags and the "not advice" label.
- **Parallel branches might need a core change.** Mitigation: the ownership rule, plus separate `fix(core)` PRs.

## Verification
- Each implementation:
  - Its pytest file passes.
  - Its page is served from its worktree and checked in the browser pane.
  - Each chart shows its source link, as-of and retrieved dates, and freshness badge.
  - Spot-check 3 numbers against their source links.
  - You approve.
- Contracts: `tests/test_contracts.py` validates the fixtures. `test_core.py` rejects rows without provenance, and manual rows without `entered_by`.
- Collectors: snapshot counts match the live APIs (Greenhouse 640 and Ashby 828 as of 2026-10-05).
- Editions: a weekly run creates `reports/2026-Www/`. "Compare to" shows deltas, and `data_pack.xlsx` has provenance columns.
- AI: the reports pass the gates, and the planted-fake-number test is rejected.
- Phase gates: `main` is served and checked by you; `gh workflow run daily.yml` produces a data commit, and Render redeploys.
- Release: in a fresh clone, Claude prompts for the 10 plugins, and every skill runs end to end.

## Revisions (newest last; the plan is updated whenever something changes or proves unrealistic)
- **R1, 2026-10-05: PR flow.** No direct merges to `main`.
  - Implementation PRs go into `phase/N`, and one phase PR goes from `phase/N` to `main`.
  - Phase 0 was folded into a single PR (`phase/0` → `main`), at your request.
- **R2, 2026-10-05: scheduled data commits.**
  - The daily and weekly jobs must land data somewhere the dashboard deploys from.
  - Proposal: they are the only direct writer to `main`, and they only touch `data/` and `reports/`. Code never bypasses a PR.
  - Alternative: a separate `data` branch merged by a daily PR (heavier: 365 PRs a year).
  - **Decision needed** before 1.1 enables the schedule.
- **R3, 2026-10-05: branch protection is available on this private repo.** It's optional; turning it on is your call.
  It would need a bypass for the data job (R2), e.g. a deploy key.
- **R4: all Python dependencies are declared in 0.2,** not Phase 1, so 1.1 and 1.2 never both edit `pyproject.toml`.
- **R5: per-page company identifiers** go in `config/identifiers/<page>.yaml`, so Phase 2 pages don't share company files.
- **R6: login.** `SESSION_SECRET` env var and a `GET /api/session` route.
- **R7: storage realism.**
  - Daily full snapshots of ~500 job boards would be about 5 GB a year in git.
  - Fix: raw files are gzip-compressed (`.jsonl.gz`); the hiring-for-Claude corpus stores only matching postings plus per-company counts; the H-1B files keep only tracked employers.
  - Estimated repo growth: tens of MB a year.
- **R8: PyPI history** comes from ClickHouse's public dataset (verified, no key) instead of BigQuery, which needs GCP billing.
- **R9: Google Trends.** `pytrends` is archived (verified), so Trends needs a SerpAPI key; without one, it shows "awaiting data".
- **R10: OpenRouter is dropped.** Its terms forbid scraping (verified). No equivalent free token-share source is known, so the gap is listed on Data & Methods.
- **R11: GitHub co-authored commits.**
  - The search count is unstable (8.0M vs 9.2M for the same week, `incomplete_results: true`), and rapid queries hit secondary rate limits.
  - Fix: show it as a smoothed index with a caveat, and evaluate GH Archive via ClickHouse in 2.2.
- **R12: N-PORT.** Anthropic's share count isn't public, so an implied valuation can't be computed honestly.
  The page shows fund marks and their % change instead; a valuation only appears with a cited share count.
- **R13: H-1B LCA.** Each quarterly DOL file is ~79 MB (verified). It is collected quarterly, filtered to tracked employers.
- **R14, 2026-10-05: CI and phase overlap.**
  - `.github/workflows/ci.yml` runs ruff and pytest on every PR, and on pushes to `main` and `phase/**`.
  - `phase/N+1` is cut from `phase/N` as soon as the phase PR opens, so the next phase's worktrees can start during review.
    Because phase PRs use merge commits, the next phase PR still shows only its own changes.
