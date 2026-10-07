# Signal page: research note (3.2)

Status: **built on `p3/signal` (core chart kind in #31), awaiting the user's review.** Written 2026-10-06; sections 3 to 5 updated 2026-10-07 to match what was built, including a correction to section 4.
Decisions already made (2026-10-06): charts rather than tables; OpenAI is the main comparison; no private data is supplied now; the OpenRouter models list was allowed only if it helps. It shows no average effect (section 4), but the page keeps it as release markers so each release can be judged by eye (revised after review: charts must combine two aspects).

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
3. **Pre-whiten, then rank.** An AR(2) fit removes each series' own momentum, so a series that is simply persistent is not mistaken for one that leads. The residuals are ranked (Spearman correlation), so one extreme week cannot decide a result: with Pearson, a single launch-era week in each series produced a "lead" that ranks erase.
4. **Cross-correlate at leads of 1 to 8 weeks.** Pair order is the direction: x leads y by k weeks. The lag chart also shows y leading x and the same week (−8…+8), as context.
5. **Null by surrogates.** 1,500 surrogate series with the same autocorrelation as y and random timing (phase randomisation); keep the best of the eight lags' |r| each time, and call the observed value real only if it beats that. This accounts for having tried eight lags. Rotating y instead gives only about one surrogate per week of history (about 160), too few to resolve the p-values that a correction across 50 pairs needs (see the correction in section 4). Checked on independent series: about 5% reach p < 0.05, as they should.
6. **FDR across all tests.** Benjamini-Hochberg over every pair and direction tested, so testing more pairs does not make a fluke more likely to show.
7. **Stability.** The same lag and sign in the first and second half of the history. A relationship that fades is labelled so.
8. **Relative to OpenAI.** Repeat on `Anthropic growth − OpenAI growth`. What survives is Anthropic-specific; what disappears was the market.
9. **Label.** *Finding* (passes 5-7), *Hypothesis* (passes 5-6 but not stability, or n too small to check), *Not supported* (fails). The page says which.

**Power.** After the corrections a pair needs roughly |r| ≥ 0.3 at 190 weeks and ≥ 0.4 at 80 weeks to count. Smaller relationships may exist and cannot be seen with this much history; the page says so rather than reporting weak ones.

## 4. What the tests found (real data, weekly, n 60-190)

**Correction, 2026-10-07.** My first probe (reported on 2026-10-06) used Pearson correlation and a null made by rotating one series. A series of 190 weeks can be rotated only about 160 distinct ways, so the smallest p-value it could honestly give was about 0.006, and the thousands of "draws" were repeats; the p-values it printed (0.001 and below) were far too small, and a correction across many pairs cannot pass anything at that resolution. Its results, "34 pairs pass" and "2 of 26 leads survive" (Anthropic npm to PyPI at 3 weeks, relative Wikipedia product views to relative CLI at 4 weeks), are **withdrawn**. The built engine (section 3) replaces them.

- **Built engine, 50 pairs** (every ordered pair of Anthropic's own six weekly series, and of its growth relative to OpenAI's across five): **none survives the correction**. The strongest is relative Wikipedia product article views leading relative PyPI downloads at 8 weeks (rank r +0.27, 129 weeks), at 1.2 times the 95% chance line, and its corrected p is 0.52. The chart of everything tested shows all 50 as Not supported; the page says so and still draws the pairs.
- **Anthropic npm to PyPI at 3 weeks** still shows under Pearson with the corrected null (r 0.33, p 0.001), which alone would not pass a correction across 50 pairs, and falls to p 0.18 once weeks are ranked: it rests on a few extreme weeks. Treat it as a curiosity, not a lead.
- **Duplicates.** The two Wikipedia articles, and Anthropic's PyPI and npm downloads, share noise and move together in the same week. That is shown as a caveat on the chart of pairs, not as a result.
- **Retraction.** The earlier note (R20) said "Wikipedia product views led PyPI by one to two months (r about 0.5, n 31, 30 lags tried)". On weekly data it is **not supported**, and nothing in the 50-pair test supports it either. The monthly figure came from 31 points and 30 tries.
- **Model releases show no average effect** (the OpenRouter public models list: 15 Anthropic and 33 OpenAI release weeks). First test, the four weeks from a release, 11 series: all q ≥ 0.87. Second test, after your review, from 4 weeks before to 12 after and the sum over weeks +4 to +8: for the 11 Anthropic releases (2025-05 on), no series leaves the 95% band at any week, and the "month later" sums are not higher (PyPI +0.15 vs +0.27 for a random 5 weeks, npm +0.25 vs +0.19, CLI +0.32 vs +0.30). For OpenAI's releases the result is the same. Caveats: the list gives when OpenRouter listed a model, not the official release; Anthropic's starts at 2025-05, missing Claude 1 to 3.7 and Claude Code; and 11 events can only see a large effect. The list stays as the marker source for chart 1, with the null result shown, not hidden.

## 5. The page: combined charts, two aspects in each (revised 2026-10-06 after review)

Seven single-series charts did not show a connection. Each chart now puts **two aspects on one frame**, so the eye can see one lead the other. Bars are events or counts, lines are levels or growth, and a right-hand axis is used when the two have different units.

| # | Chart | Bars / markers | Line | What you see |
|---|---|---|---|---|
| 1 | **Model releases over SDK downloads** | one marker per Anthropic release (and OpenAI release in a second colour) | weekly PyPI and npm downloads | Whether a release is followed, about a month later, by a step up. You judge each release, not just an average. |
| 2 | **Around a release: the average** | average weekly growth in each week from 4 before to 12 after a release | what a random week looks like (95% band) | Whether the average release moves anything. A bar outside the band is a real move; inside is noise. |
| 3 | **Anthropic against OpenAI** | the gap (Anthropic growth minus OpenAI growth) per quarter | OpenAI's own growth on the same axis | Anthropic bad but OpenAI worse reads as a positive bar. |
| 4 | **Lead and lag for the strongest pair** | correlation at each lag from −8 to +8 weeks | the noise ceiling | A real lead is one clear bar outside the ceiling, not a ripple. |
| 5 | **Valuation over signals** | each funding round's post-money value (Series E to H) and each fund's per-share mark | a signal (downloads, attention) rebased to 100 at the first mark | How much each signal grew between valuation steps. Descriptive: see the limit below. |
| 6 | **Valuation step against signal growth** | valuation multiple from one round to the next | the same signal's growth over the same interval, one dot per signal | Which signals grew most in the intervals when the valuation stepped up most. |

Chart 1 and chart 2 are the same question, seen two ways: the first shows the events, the second shows whether they matter on average.

**What the data says about chart 1-2 today.** Tested over the weeks from 4 before to 12 after each of the 11 Anthropic releases the public list holds (2025-05 on), no weekly series (PyPI, npm, CLI, Wikipedia, commits) moves outside what a random week does, and the sum of growth over weeks +4 to +8 ("about a month later") is no higher for any of them. The one hit, commits growing *less* after a release (p 0.03), does not survive correcting for 8 series. A longer list of dated releases (Claude 1 to 3.7, Claude Code) would give about twice the events; each would need a source and a quote (section 6).

**The valuation limit.** There are four funding-round post-money values (Series E 2025-03 $61.5B, F 2025-09 $183B, G 2026-02 $380B, H 2026-05 $965B) and 648 fund marks (per-share value from SEC N-PORT filings, since 2023-04). Four rounds cannot support a correlation. They support charts 5 and 6 as a description, labelled "n = 3 intervals". The fund marks have 38 report dates since 2023-04, which is too few to test a lead of one to four quarters, so they are **not used** on this page. Neither source measures how much a signal "affects" valuation; they show what moved in the same period.

**Plot first, test second.** Every chart draws its two series whether or not the test finds a pattern, so the reader can see what the analysis could not (a shape the test missed, a relationship that changes over time, a single outlier driving a result). The test is a label beside the plot, never a gate in front of it:
- *Finding*, *Hypothesis* or *Not supported*, with the correlation, the lag, n and the noise ceiling, sit in the chart's subtitle and takeaway.
- A pair that fails is drawn exactly like one that passes; the page says "no reliable pattern found, drawn so you can judge" and does not hide or restyle it.
- Candidate pairs are listed in a fixed order (releases and valuation first, then the strongest tested pairs) so the page does not reshuffle with every run.
- Nothing is smoothed, interpolated or rescaled without saying so on the chart (a right-hand axis is labelled, a rebased series says "rebased to 100").

If no pair clears the bar, charts 2 and 4 say "no reliable relationship" and still show what was tested. A result is never hidden because it was null.

**A core change is needed first.** The chart renderer draws one kind of mark per chart. A combined chart (bars or markers with a line, an optional right axis, an optional band) needs a new `combo` kind in `contracts/chart_spec.schema.json` and `frontend/src/components/Plot.tsx`, as its own `feat(web)` PR with its own tests, before the Signal page. Both files are core.

Likely build after that: `pipeline/marts/signal.py` (weekly series from the Briefing's own adapters so numbers match; releases and valuation as ledger inputs), a test module with a planted lead recovered, planted noise rejected and a fixed seed, `frontend/src/pages/Signal.tsx`, and a `/signal` skill.

## 6. Data to find

None is needed to build the page. Each item below would let it answer a question it cannot answer today. Hand-entered rows go in `data/ledgers/signal_<name>.csv` with the standard header (`as_of,entity,metric,value,dims,source_url,entered_by,retrieved_at,evidence`), so every row carries a source and a quote. **A hand-entered number never feeds a Briefing verdict**; it is a yardstick that public signals are compared with.

### 6a. Most useful: a yardstick for the business

Today the page can relate public signals to each other, but not to anything that matters to the business. The old run-rate ledger had only 12 irregular points over two years, too few and too uneven to test a lead.

| Data | Why it helps | How it is used | Where it may come from |
|---|---|---|---|
| **Dated Anthropic revenue or run-rate statements, as often as possible, ideally monthly** | The one series that says whether a public signal leads *money* | Growth in that series vs the weekly signals, at lags of 1-12 weeks. With fewer than about 30 points the result is only ever a *Hypothesis* | Company posts, funding announcements, press with a named source. Each needs a URL and a quote |
| **Dated OpenAI run-rate statements** | The same yardstick for the main comparison | Chart 3 reads revenue growth relative to OpenAI | Same |
| **A monthly adoption panel with Anthropic and OpenAI side by side** (for example a published spend or business-adoption index; **candidate, not yet checked**) | A direct, comparable measure of paying customers, not just developer activity | Joins chart 3 as a second view of "ahead of OpenAI" | A public monthly index from a card or expense-data company; check licence before use |

**Already in hand, nothing to find:** Anthropic's funding rounds with post-money values (Series E to H, each with a URL and a quote) are in the git history (`data/ledgers/capital_funding_rounds.csv`, removed in R18) and the 648 per-share fund marks are collected. They come back as `data/ledgers/signal_valuation_rounds.csv`, used only as a yardstick on this page.

### 6b. Useful: dated events

| Data | How it is used |
|---|---|
| Dated Anthropic and OpenAI **API price changes** (with a quote) | Event study like section 4: does growth change in the weeks after a price cut? Needs ≥ 10 events to say anything |
| Dated **model releases before 2025-05 and major product launches** (Claude 1 to 3.7, Claude Code, enterprise plans, a new app), each with a URL and a quote | The OpenRouter list starts at 2025-05 and gives only 11 events; this roughly doubles them and names launches, not listings |
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

## 7. What was built, and what is open

**Built on `p3/signal` (twelve charts; spec R25 records the later changes: OpenAI on every chart, tick boxes, and a scorecard of each new model on Intelligence Index, Arena and price per task):** Anthropic against OpenAI (growth over 13 weeks, the latest eight blocks); each release over weekly SDK growth; the average around a release against random weeks from the same stretch of history; the strongest candidate lead, lag by lag; every pair tested, strongest first; the funding-round valuations over SDK downloads; and the valuation multiple between rounds against how much each signal grew. Differences from the plan above: chart 1 plots growth rather than level, because an exponential level hides any step after an early release; the "everything tested" chart is added so a null is never hidden; the fund marks are not used.

**Open:**
1. **Review of the page** (the review checkpoint), then the PR into `phase/3`, after the core chart kind (#31) is merged.
2. **Whether to add the free series in 6d.**
3. **Whether to pursue any item in 6a or the earlier releases in 6b.** Without them the page cannot tie a public signal to revenue, and with 50 pairs and no survivor it says so.
