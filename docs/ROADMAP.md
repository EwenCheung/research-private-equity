# Roadmap

Status: ☐ not started · ◐ in progress · ⧗ in PR review · ☑ merged to `main`

The rules:
- Implementations inside one phase are independent: one branch and one worktree each, and they can run in parallel.
- The next phase branch is cut from the current one as soon as its PR opens, so work continues while the PR is reviewed.
- PR flow: each implementation opens a PR into `phase/N`. At the gate, one PR `phase/N` → `main`. No direct merges to `main`.
- This file is only updated on the phase branch, as the gate's last commit before the phase PR. Never directly on `main`.

The full design is in [the spec](superpowers/specs/2026-10-05-signal-monitor-design.md).

## Phase 0: Foundations ☑ (merged in #1, tag `phase-0`)
| # | Implementation | Branch | Status |
|---|---|---|---|
| 0.1 | Team environment: shared plugins, CLAUDE.md, README, roadmap, spec | `p0/team-env` | ☑ |
| 0.2 | Contracts & fixtures: row, chart-spec and AI-report schemas, module interfaces, sample marts | `p0/contracts` | ☑ |

## Phase 1: Platform ☑ (merged in #5, tag `phase-1`)
| # | Implementation | Branch | Status |
|---|---|---|---|
| 1.1 | Data core: registry, collect/build CLIs, freshness, snapshot capture (its daily/weekly workflows were removed in R19) | `p1/data-core` | ☑ (#2) |
| 1.2 | Web core: FastAPI, password, React shell, ChartCard, sample page, Render config | `p1/web-core` | ☑ (#3) |
| gate | Integration: live freshness from `data/registry.json`, real-data `DATA_DIR`, login throttle, workflow hardening, worktree standard | `p1/integration` | ☑ (#4) |

## Phase 2: Evidence pages ☑ (tag `phase-2`)
| # | Implementation | Branch | Status |
|---|---|---|---|
| 2.1 | Hiring & Talent | `p2/hiring` | ☑ (#8) |
| 2.2 | Developer Adoption | `p2/dev-adoption` | ☑ (#10) |
| 2.3 | Consumer & Attention (also holds the Customers charts since #23) | `p2/attention` | ☑ (#11, #23) |
| 2.4 | Product & Reliability: two leaderboard embeds and status-page incidents | `p2/product` | ☑ (#9, #14, #21, #23) |
| 2.5 | Customers: Hacker News job posts and SEC filers, shown on Consumer & Attention | `p2/customers` | ☑ (#16, #23) |
| 2.6 | Capital & Valuation: fund marks only | `p2/capital` | ☑ (#15, #23) |
| 2.7 | Licensed Alt-Data + `/add-manual-data` | `p2/licensed-data` | removed in #23 (built in #12) |
| 2.8 | Data & Methods + `/refresh-data`, `/add-source` | `p2/data-methods` | ☑ (#13) |
| 2.9 | Cut to signal: 47 charts to 11, unused collectors and hand-entered ledgers removed, plain-language headers (spec R18) | `p2/prune-signals` | ☑ (#23) |

| core | Shared http, frame helpers, rate-limit patience, concrete provenance links from rows the chart read, hardcoded-ledger labels, and fixed company colours by name (Anthropic orange, OpenAI dark green, Google blue) | `p2/core`, `p2/core-ratelimit`, `p2/core-source-provenance`, `p2/core-provenance-used`, `p2/core-company-colours` | ☑ (#6, #7, #17, #24, #25) |

## Phase 3: Cross-page synthesis ☑ (tag `phase-3`)
Re-planned after the cut (spec R20, R21): about Anthropic, judged by its own direction against its own history, with peers kept as context; signals grouped into families; no hand-entered number in any verdict. Build order: Briefing, Hot Pick, then Signal after research.
Nothing runs on a schedule (R19); Hot Pick and analysis run on demand.

| # | Implementation | Branch | Status |
|---|---|---|---|
| 3.1 | Briefing: Anthropic's verdict per signal family, OpenAI comparison, tripwires | `p3/briefing` | ☑ (#27, #28) |
| 3.2 | Signal: seven charts comparing Anthropic with OpenAI: market or own across four dimensions, releases with each model's score and change, the release effect, every new model, leads and lags, and valuation over usage; research note in `docs/research/signal-page.md` | `p3/signal` | ☑ (#34) |
| 3.3 | Hot Pick: the hottest news, blogs, discussion, technology trends and insights about Anthropic, for the last week and month, with saved archives | `p3/hot-pick` | ☑ (#29) |
| 3.4 | Product: Claude's incidents against OpenAI's, from its feed and the Internet Archive | `p3/product-incidents` | ☑ (#30) |
| core | Combined charts for Signal: the `combo` chart kind, tick boxes for companies and dimensions, stacked panels with their own axes | `p3/core-combo-chart`, `p3/core-series-toggle`, `p3/core-panels` | ☑ (#31, #32, #33) |

## Phase 4: AI Analysis ☐
| # | Implementation | Branch | Status |
|---|---|---|---|
| 4.1 | Bull / Bear / Neutral reporters, gates, page, `/run-analysis` (on demand) | `p4/ai-analysis` | ☐ |
| 4.2 | Offline SQLite store: fetch every source once, serve the dashboard from one file, Refresh data button with rollback, parallel fetch, retry failed | `p4/offline-store` | ✓ merged (#36, #37) |
| 4.3 | Wikipedia removed from every page; Consumer & Attention becomes Customers (spec R28) | `p4/prune-wikipedia` | ✓ merged (#39) |
| 4.4 | Cleanup: unused code and constants, one `month_label`, four unused dependencies | `p4/cleanup-dead-code`, `p4/core-cleanup`, `p4/deps-unused` | ✓ merged (#40, #41, #42) |
| 4.5 | The SQLite dataset is committed (3.6 MB, rows table optional) so a clone shows the same data offline; README quick start | `p4/dataset-in-git` | ✓ merged (#44) |

## Phase 5: Team release ☐
| # | Implementation | Branch | Status |
|---|---|---|---|
| 5.1 | README walkthrough, fresh-clone test, production deploy check | `p5/release` | ☐ |
