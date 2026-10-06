# Signal page: research note (3.2)

Status: **research, for the user's sign-off. Nothing is built.** Written 2026-10-06 on `phase/3`.
Decisions already made (2026-10-06): charts rather than tables; OpenAI is the main comparison; no private data is supplied now; the OpenRouter models list was allowed only if it helps (it did not, section 4).

## 1. What the page is for

The Briefing says what each signal did. The Signal page answers three narrower questions, and shows nothing it cannot defend:

1. **Is Anthropic doing better or worse than OpenAI?** A weak quarter for Anthropic is still a good sign if OpenAI's was worse, and a strong one is hollow if the whole market rose. Every signal is read against OpenAI.
2. **Does one public signal lead another?** For example "npm downloads rise, then PyPI downloads rise three weeks later". A real lead is a reason to watch the earlier series.
3. **Which series can be trusted?** How long, how noisy, and whether two series are really one (duplicates).

It never claims one thing *causes* another, and "tested, nothing reliable" is a normal and useful result.

## 2. What we already have

All public, every row with provenance. Weeks are complete Monday-to-Sunday weeks, with spikes removed by the existing `dev_adoption.flag_spikes`.

| Signal | Anthropic | OpenAI | Cadence | Starts | Weeks (Anthropic) |
|---|---|---|---|---|---|
| Wikipedia views, company article | yes | yes | daily | 2023-02 | 191 |
| Wikipedia views, product article (Claude / ChatGPT) | yes | yes | daily | 2024-02 | 139 |
| PyPI SDK downloads | yes | yes | daily | 2023-02 | 189 |
| npm SDK downloads | yes | yes | daily | 2023-02 | 190 |
| npm CLI downloads (Claude Code / Codex) | yes | yes | daily | 2025-03 | 83 |
| GitHub commits co-authored by Claude | yes | no | daily | 2025-03 | 83 |
| Status-page incidents | yes | only since 2026-09 (25 incidents) | event | 2023-03 | 185 |
| Hacker News "who is hiring" share | yes | yes | monthly | 2023-01 | monthly only |
| Open roles | yes | yes | monthly (Wayback snapshots, plus the live count) | 2024-05 | monthly only |
| SEC filings naming the company | yes | yes | quarterly (filings dated daily) | 2023-02 | quarterly only |
| Fund marks | yes | no | quarterly | see the Capital page | quarterly only |

Monthly and quarterly series are too short for lag work (31 months at best, and the lag itself is a month). They stay on the Briefing; the Signal page uses the weekly ones.

**OpenAI has no commits or fund-mark series, and only three weeks of incidents**, so those are read on Anthropic's own history.

## 3. Method, step by step

Levels all trend up together, so any two of them correlate. In a test against Anthropic's stated run-rate, even incident counts scored r 0.86. So the page never correlates levels.

1. **Weekly grid.** Daily series are summed to complete weeks, spikes removed. A partial first or last week is dropped.
2. **Growth, not level.** Weekly log-growth `ln(x_t) − ln(x_{t-1})`. Incidents use the weekly change in count.
3. **Pre-whiten.** An AR(2) fit removes each series' own momentum, so a series that is simply persistent is not mistaken for one that leads.
4. **Cross-correlate at lags −8…+8 weeks.** Lag 0 means the same week (a duplicate or a market-wide move); +1…+8 means x leads y.
5. **Null by circular shift.** Rotate y by a random amount (1,500 draws), keep the best-of-17-lags |r| each time, and call the observed value real only if it beats that. This accounts for having tried 17 lags.
6. **FDR across all tests.** Benjamini-Hochberg over every pair and direction tested, so testing more pairs does not make a fluke more likely to show.
7. **Stability.** The same lag and sign in the first and second half of the history. A relationship that fades is labelled so.
8. **Relative to OpenAI.** Repeat on `Anthropic growth − OpenAI growth`. What survives is Anthropic-specific; what disappears was the market.
9. **Label.** *Finding* (passes 5-7), *Hypothesis* (passes 5-6 but not stability, or n too small to check), *Not supported* (fails). The page says which.

**Power.** After the corrections a pair needs roughly |r| ≥ 0.34 at 80 weeks and ≥ 0.22 at 190 weeks to count. Smaller relationships may exist and cannot be seen with this much history; the page says so rather than reporting weak ones.

## 4. What the probe found (real data, weekly, n 70-190)

- **132 ordered pairs, lags −8…+8:** 34 pass FDR, but nearly all are duplicates or market-wide moves, not leads: Wikipedia product vs company article (r 0.84), Anthropic PyPI vs OpenAI PyPI (0.58), Anthropic PyPI vs OpenAI npm (0.54), Anthropic npm vs Anthropic CLI (0.46), Anthropic npm vs OpenAI npm (0.41). These belong on a "moves together, do not double-count" chart.
- **26 lead hypotheses** (x leads y by 1-8 weeks): only **2** pass q < 0.05.
  - Anthropic npm → Anthropic PyPI at 3 weeks (r 0.34, n 186). Fading: r 0.45 in the first half, 0.13 in the second. *Hypothesis*, weakening.
  - Relative Wikipedia product views → relative CLI downloads at 4 weeks (r −0.30, n 70). Negative, and too short to check stability. *Hypothesis*.
- **Retraction.** The earlier note (R20) said "Wikipedia product views led PyPI by one to two months (r about 0.5, n 31, 30 lags tried)". On weekly data it is **not supported**: the best r is +0.16 at 8 weeks, p 0.45. The monthly figure came from 31 points and 30 tries.
- **Model releases do not help** (the OpenRouter public models list, 15 Anthropic and 33 OpenAI release weeks). Average growth in the 4 weeks from a release is no different from a random 4 weeks for any of 11 series, for either company's releases (all q ≥ 0.87; nearest miss: OpenAI releases vs Anthropic CLI, p 0.08, which is lower growth, not higher). Per the decision, **OpenRouter is dropped** from the plan. Caveats: the list gives when OpenRouter listed a model, not the official release, and Anthropic's goes back only to 2025-05, so this tests only 12 events.

## 5. The page, chart by chart (no tables)

Each chart answers one question and its takeaway is computed. Chart kinds are the existing ones (`line`, `bar`, `stacked_bar`, `scatter`); a chart kind the renderer lacks would be its own core PR, so none is assumed.

| # | Question | Chart | Reading |
|---|---|---|---|
| 1 | Is Anthropic ahead of OpenAI? | **Line**: 13-week growth of Anthropic minus OpenAI, one line per signal (Wikipedia, PyPI, npm, CLI) | Above zero: Anthropic is growing faster, or falling less, than OpenAI. This is the "Anthropic bad but OpenAI worse is still good" view. |
| 2 | How much of each move is the market? | **Stacked bar** per signal: share of Anthropic's weekly growth explained by OpenAI's same-week growth, and the rest | The rest is Anthropic-specific; a signal that is nearly all market says little about Anthropic. |
| 3 | Which pairs were tested, and which beat noise? | **Bar**: each tested pair's best |r| as a multiple of the noise ceiling (1.0 = the line to beat) | Only pairs above 1.0 are findings or hypotheses; the bars below show how much was tested and rejected. |
| 4 | What does the strongest lead look like? | **Line**: correlation at each lag −8…+8 for the best pair, with the noise ceiling as two flat lines | A real lead is one clear peak outside the band, not a ripple. |
| 5 | Does it hold in both halves of history? | **Grouped bar**: first-half vs second-half r for each hypothesis or finding | Shows fading (npm → PyPI) at a glance. |
| 6 | Which series move together, so I do not count them twice? | **Bar**: same-week correlation of related series | Duplicates and market-wide moves listed once. |
| 7 | Which series can I trust? | **Bar**: weeks of history per series, with the noisiest named in the takeaway | Short or noisy series carry less weight. |

If no pair clears the bar, charts 4 and 5 show the best candidate and say "no reliable relationship"; the page does not invent a finding. A scatter of the weekly changes at the best lag is an optional drill-down under chart 4.

Likely build: `pipeline/marts/signal.py` (a weekly series builder reusing the Briefing's `signals(ctx, ent)` adapters, so numbers match the other pages), a test module with a planted lead recovered, planted noise rejected and a fixed seed, `frontend/src/pages/Signal.tsx`, and a `/signal` skill. No collector is required to start.

## 6. Data to find

None is needed to build the page. Each item below would let it answer a question it cannot answer today. Hand-entered rows go in `data/ledgers/signal_<name>.csv` with the standard header (`as_of,entity,metric,value,dims,source_url,entered_by,retrieved_at,evidence`), so every row carries a source and a quote. **A hand-entered number never feeds a Briefing verdict**; it is a yardstick that public signals are compared with.

### 6a. Most useful: a yardstick for the business

Today the page can relate public signals to each other, but not to anything that matters to the business. The old run-rate ledger had only 12 irregular points over two years, too few and too uneven to test a lead.

| Data | Why it helps | How it is used | Where it may come from |
|---|---|---|---|
| **Dated Anthropic revenue or run-rate statements, as often as possible, ideally monthly** | The one series that says whether a public signal leads *money* | Growth in that series vs the weekly signals, at lags of 1-12 weeks. With fewer than about 30 points the result is only ever a *Hypothesis* | Company posts, funding announcements, press with a named source. Each needs a URL and a quote |
| **Dated OpenAI run-rate statements** | The same yardstick for the main comparison | Chart 1 reads revenue growth relative to OpenAI | Same |
| **A monthly adoption panel with Anthropic and OpenAI side by side** (for example a published spend or business-adoption index; **candidate, not yet checked**) | A direct, comparable measure of paying customers, not just developer activity | Joins chart 1 as a second view of "ahead of OpenAI" | A public monthly index from a card or expense-data company; check licence before use |

### 6b. Useful: dated events

| Data | How it is used |
|---|---|
| Dated Anthropic and OpenAI **API price changes** (with a quote) | Event study like section 4: does growth change in the weeks after a price cut? Needs ≥ 10 events to say anything |
| Dated **major product launches** that are not model releases (Claude Code, enterprise plans, a new app) | Same event study, tighter than the OpenRouter list because it names launches, not listings |
| OpenAI **incident history** before 2026-09 | Only three weeks are collected; a like-for-like reliability view needs the earlier years, if the status page can supply them |

### 6c. Nice to have: valuation and attention

| Data | Why |
|---|---|
| Secondary-market prices (Forge, Caplight, Hiive) | A price series would show whether public signals lead how the market values the company. Licensed: stays private, never pasted into issues, PRs or external services |
| Web traffic or app downloads (Similarweb, Sensor Tower) | Consumer demand is the weakest part of the picture. Licensed, same rule |
| Cloud marketplace usage (Bedrock, Vertex) | Where Claude is bought through a cloud partner. No public series is known |

### 6d. Free series I can add myself (do not hunt for these)

- **Hacker News story volume and points for Anthropic / Claude**, weekly, back to 2023 (Algolia, backfillable).
- **New GitHub repositories about Claude and Anthropic per week**, back to 2023 (GitHub search, backfillable). Note: the earlier Hot Pick collector reads only the last 30 days; this one would keep a weekly count.

They would give two long attention series to test as leads, and cost nothing but a collector. Say if you want them added.

## 7. Open items

1. **Sign-off on the page design (section 5) and the method (section 3).** Building starts after that, on `p3/signal`, ports 8002 and 5175.
2. **Whether to add the free series in 6d**, as part of 3.2 or later.
3. **Whether to pursue any item in 6a.** Without a yardstick the page is still useful (chart 1, 2, 6, 7), but cannot tie a public signal to revenue.
