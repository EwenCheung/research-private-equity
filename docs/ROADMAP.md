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

## Phase 3: Cross-page synthesis ◐ (branch `phase/3`)
Re-planned after the cut (spec R20, R21): about Anthropic, judged by its own direction against its own history, with peers kept as context; signals grouped into families; no hand-entered number in any verdict. Build order: Briefing, Hot Pick, then Signal after research.
Nothing runs on a schedule (R19); Hot Pick and analysis run on demand.

| # | Implementation | Branch | Status |
|---|---|---|---|
| 3.1 | Briefing: Anthropic's verdict per signal family, divergence flags, tripwires | `p3/briefing` | ☐ |
| 3.2 | Signal: the sharpest relationships between signals, including peers' moves; research first | `p3/signal` | ☐ (research) |
| 3.3 | Hot Pick: the week's hottest insights and news about Anthropic, with a weekly archive | `p3/hot-pick` | ☐ |

## Phase 4: AI Analysis ☐
| # | Implementation | Branch | Status |
|---|---|---|---|
| 4.1 | Bull / Bear / Neutral reporters, gates, page, `/run-analysis` (on demand) | `p4/ai-analysis` | ☐ |

## Phase 5: Team release ☐
| # | Implementation | Branch | Status |
|---|---|---|---|
| 5.1 | README walkthrough, fresh-clone test, production deploy check | `p5/release` | ☐ |
