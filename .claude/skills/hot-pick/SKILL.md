---
name: hot-pick
description: Use when the user wants this week's Hot Pick ("hot pick", "what stood out this week", "any news on Anthropic", "save this week's picks", "/hot-pick"). Refreshes the news, Hacker News and filing sources, rebuilds the picks and saves the week.
---

# Hot Pick

Hot Pick is the week's few most notable things about Anthropic: news several outlets ran, the most-discussed Hacker News story, new
filings that name Anthropic, and the biggest moves in the Briefing's signals. The rules are in `config/hot_pick.yaml` and are written
out under the chart's "Assumptions & detail". Each kind is scored in its own unit and a quiet week shows fewer picks, not filler.

## Steps
1. **Refresh the sources.** `uv run python -m pipeline.collect --source news_headlines hn_stories sec_filings_naming`. For the insights, the
   other signals must be current too: run `/refresh-data` first when they are more than a few days old.
2. **Build.** `uv run python -m pipeline.build`. `hot_pick.this_week` is rebuilt with everything else.
3. **Read the picks** on the Hot Pick page, or in `data/marts/hot_pick.this_week.json`. Open two or three links and check the headline,
   the outlet count and the date against the source. A news group that mixes two stories, or two groups that are one story, is a
   reason to look at `similarity` and `repeat_shared` in the config, not to edit the file by hand.
4. **Save the week.** `uv run python -m pipeline.hot_pick freeze`. It writes `data/marts/hot_pick.week_YYYY_WW.json`. Saving again
   in the same week replaces that week's file; a past week is never overwritten.
5. **Commit on a branch and open a PR**, never straight to `main`: `chore(data): save hot pick for week YYYY-Www`, with the raw
   snapshots under `data/raw/` in their own commit. Conventional Commits, the user as author, no co-author or AI tag.

## Never
- Edit a saved week or anything in `data/raw/`: fix forward.
- Add a pick by hand, or rewrite a headline. Headlines are shown with their link, not reproduced.
- Describe a pick as important or as investment advice: it is a rule applied to a public feed.
