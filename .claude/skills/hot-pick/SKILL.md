---
name: hot-pick
description: Use when the user wants the Hot Pick ("hot pick", "what stood out this week or month", "any news on Anthropic", "save the picks", "/hot-pick"). Refreshes the news, blog, Hacker News, dev.to, GitHub and filing sources, rebuilds the picks and saves the week and month.
---

# Hot Pick

Hot Pick is the few most notable things about Anthropic over the last week and the last month: news several outlets ran, curated blogs,
the most-discussed Hacker News stories, popular dev.to posts, new GitHub repositories tagged Claude, new filings that name Anthropic, and
the biggest moves in the Briefing's signals. The page toggles between Week and Month. The rules are in `config/hot_pick.yaml` and are
written out under each chart's "Assumptions & detail". Each kind is scored in its own unit and a quiet period shows fewer picks, not filler.

## Steps
1. **Refresh the sources.** `uv run python -m pipeline.collect --source news_headlines ai_feeds hn_stories community_posts github_repos sec_filings_naming`.
   Each feed keeps only its latest items (a few days for busy news sites), so run this at least weekly or the month has holes. A feed
   that fails is named on stderr and skipped. For the insights, the other signals must be current too: run `/refresh-data` first when
   they are more than a few days old.
2. **Build.** `uv run python -m pipeline.build`. `hot_pick.this_week` and `hot_pick.this_month` are rebuilt with everything else.
3. **Read the picks** on the Hot Pick page (toggle Week and Month), or in `data/marts/hot_pick.this_*.json`. Open two or three links and check the headline,
   the outlet count and the date against the source. A news group that mixes two stories, or two groups that are one story, is a
   reason to look at `similarity` and `repeat_shared` in the config, not to edit the file by hand.
4. **Save the period.** `uv run python -m pipeline.hot_pick freeze`. It writes `data/marts/hot_pick.week_YYYY_WW.json` and
   `data/marts/hot_pick.month_YYYY_MM.json`. Saving again in the same week or month replaces that file; a past one is never overwritten.
5. **Commit on a branch and open a PR**, never straight to `main`: `chore(data): save hot pick for week YYYY-Www and month YYYY-MM`, with the raw
   snapshots under `data/raw/` in their own commit. Conventional Commits, the user as author, no co-author or AI tag.

## Never
- Edit a saved week or month or anything in `data/raw/`: fix forward.
- Add a pick by hand, or rewrite a headline. Headlines are shown with their link, not reproduced.
- Describe a pick as important or as investment advice: it is a rule applied to a public feed.
