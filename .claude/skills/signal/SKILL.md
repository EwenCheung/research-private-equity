---
name: signal
description: Use when the user asks whether Anthropic is ahead of OpenAI, whether a model release or one public signal leads another, how valuation compares with the signals, or says "/signal". Refreshes the inputs, rebuilds the Signal charts and reads them back without overclaiming.
---

# Signal

The Signal page asks four things of public data: is Anthropic gaining on OpenAI, do model releases move SDK downloads, does one signal lead another by 1 to 8 weeks,
and what moved when the announced valuation did. Every chart draws both series whether or not a pattern was found; a label says whether it held up
(Holds up, Possible, Could be luck). The method and its limits are in `docs/research/signal-page.md` and under each chart's "Assumptions & detail".

## Steps
1. **Refresh the inputs.** The weekly series come from the daily sources, so run `/refresh-data` first when they are more than a few days old. Then
   `uv run python -m pipeline.collect --source model_releases arena_text_leaderboard openrouter_benchmarks` for the release list, Arena's human-preference scores
   and the Intelligence Index with OpenRouter's own task costs (weekly). The benchmarks source needs `OPENROUTER_API_KEY` in `.env`; without it the source is skipped and
   the last snapshot stays on the page, so say so rather than presenting old scores as new.
2. **A new funding round?** The valuation yardstick is the cited ledger `data/ledgers/signal_funding_rounds.csv`. Add a row only from Anthropic's own
   announcement: `round_post_money_usd`, the announcement date, the link, a verbatim quote as `evidence`, and who entered it. Never estimate a valuation.
3. **Build.** `uv run python -m pipeline.build` rebuilds the twelve `signal.*` charts with everything else.
4. **Read the page** (Signal in the sidebar) or `data/marts/signal.*.json`. Report what each chart says in its own words: the takeaway, the label and the number of
   pairs tested. If nothing is supported, say so plainly: it is a result.
5. **Commit on a branch and open a PR**, never straight to `main`: `chore(data): refresh signal inputs`, with the raw snapshots in their own commit.
   Conventional Commits, the user as author, no co-author or AI tag.

## Never
- Say one signal causes another. A lead shows what moved first.
- Change the lags, the number of surrogates, the seed or the thresholds to get a result that holds up, or add a pair after seeing the results. Changing a definition is a
  visible change to `pipeline/marts/signal.py` and the docs, in its own PR.
- Edit anything in `data/raw/`: fix forward.
- Present a valuation figure that has no cited announcement, or let the ledger feed a Briefing verdict.
