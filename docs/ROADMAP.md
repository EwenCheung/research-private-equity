# Roadmap

Status: ☐ not started · ◐ in progress · ☑ done (merged and approved)

The rules:
- Implementations inside one phase are independent: one branch and one worktree each, and they can run in parallel.
- A phase starts only when the phase before it is ☑.
- This file is only updated at phase gates, on `main`.

The full design is in [the spec](superpowers/specs/2026-10-05-signal-monitor-design.md).

## Phase 0: Foundations ◐
| # | Implementation | Branch | Status |
|---|---|---|---|
| 0.1 | Team environment: shared plugins, CLAUDE.md, README, roadmap, spec | `p0/team-env` | ◐ |
| 0.2 | Contracts & fixtures: row, chart-spec and AI-report schemas, module interfaces, sample marts | `p0/contracts` | ◐ |

## Phase 1: Platform ☐
| # | Implementation | Branch | Status |
|---|---|---|---|
| 1.1 | Data core: registry, collect/build CLIs, freshness, daily/weekly workflows, snapshot capture | `p1/data-core` | ☐ |
| 1.2 | Web core: FastAPI, password, React shell, ChartCard, sample page, Render config | `p1/web-core` | ☐ |

## Phase 2: Evidence pages ☐
| # | Implementation | Branch | Status |
|---|---|---|---|
| 2.1 | Hiring & Talent | `p2/hiring` | ☐ |
| 2.2 | Developer Adoption | `p2/dev-adoption` | ☐ |
| 2.3 | Consumer & Attention | `p2/attention` | ☐ |
| 2.4 | Product, Pricing & Reliability | `p2/product` | ☐ |
| 2.5 | Customers & Contracts | `p2/customers` | ☐ |
| 2.6 | Capital & Valuation | `p2/capital` | ☐ |
| 2.7 | Licensed Alt-Data + `/add-manual-data` | `p2/licensed-data` | ☐ |
| 2.8 | Data & Methods + `/refresh-data`, `/add-source` | `p2/data-methods` | ☐ |

## Phase 3: Cross-page synthesis ☐
| # | Implementation | Branch | Status |
|---|---|---|---|
| 3.1 | Peers + `/add-company` | `p3/peers` | ☐ |
| 3.2 | Briefing (headlines, ARR extrapolation, tripwires) | `p3/briefing` | ☐ |
| 3.3 | Weekly editions, Compare-to, data pack, print | `p3/editions` | ☐ |

## Phase 4: AI Analysis ☐
| # | Implementation | Branch | Status |
|---|---|---|---|
| 4.1 | Bull / Bear / Neutral reporters, gates, page, `/run-analysis` | `p4/ai-analysis` | ☐ |

## Phase 5: Team release ☐
| # | Implementation | Branch | Status |
|---|---|---|---|
| 5.1 | README walkthrough, fresh-clone test, production deploy check | `p5/release` | ☐ |
