---
name: add-manual-data
description: Use when the user pastes, reads out or exports figures from a licensed vendor (YipitData, M Science) or any other hand-entered source, and wants them added to the Signal Monitor with who/when/where provenance. Triggers on "add this to the Yipit data", "update M Science", "here is this week's panel", "/add-manual-data".
---

# Add manual (licensed) data

Licensed vendor data has no API. The team reads a report and enters its figures. Every figure must show **who entered it, when, and
where in the report it came from**, and nothing already entered is ever overwritten.

## Privacy first
- Licensed data stays in this private repository, in `data/manual/<source>.csv`. Never paste it into issues, pull requests, public
  sites, or any external service, and never include figures in a commit message or PR description.
- Do not post it anywhere to "check" it. Verify against what the user pasted.

## Steps
1. **Identify the source.** `yipit_consumer` (YipitData) or `mscience_panel` (M Science). Run
   `uv run python -m pipeline.licensed status` to see what is already entered and when it was last updated.
2. **Read what the user gave you** (pasted table, CSV, screenshot or text) and map it to rows. Each row needs:
   - `as_of`: the date the figure describes, `YYYY-MM-DD`. For weekly data use the first day of the week, and say which weekday the vendor uses.
   - `entity`: the company slug (`anthropic`, `openai`, `google-deepmind`, `xai`, `mistral`, `cohere`).
   - `metric`: `panel_spend_usd`, `panel_users`, or `panel_growth_yoy` (enter growth as a ratio: +25% is `0.25`).
   - `value`: a plain number. Do not round, convert currencies, or fill gaps. If a cell is blank or unreadable, leave the row out and tell the user.
   - `dims` (optional JSON): the product or panel, e.g. `{"product": "Claude Pro", "panel": "US card"}`. Use the same spelling every week so series stay continuous.
   - `evidence`: the report name, its date, the page or table, and the figure as printed. This is required.
3. **Show the user the rows first**, as a short table with the evidence, and ask them to confirm. Do not write unconfirmed figures.
4. **Write them** with the validated command, never by editing the CSV by hand:
   - one figure: `uv run python -m pipeline.licensed add <source> --as-of ... --entity ... --metric ... --value ... --dims '{...}' --evidence "..."`
   - many: save a CSV with columns `as_of,entity,metric,value,dims,evidence` and run
     `uv run python -m pipeline.licensed import-csv <source> file.csv --evidence "<report name and date>"`
   The command checks every row, stamps `entered_by` from `git config user.name` and `retrieved_at` from the clock, skips rows already
   entered, and writes nothing if any row is invalid. Fix the reported problem and run it again.
5. **Rebuild and check.** `uv run python -m pipeline.build`, then open the Licensed Alt-Data page: the entered week should appear, the
   freshness badge should be green, and the line should read "Last entered <today> by <name>".
6. **Commit** on a branch, never directly on `main`: `data(licensed): add yipit week of 2026-09-28`. Conventional Commits, author is
   the user, no co-author or AI tag, and no figures in the message.

## Corrections
Never edit or delete an entered row. If a figure was wrong, add a new row for the same week with evidence explaining the correction;
the chart uses the newest entry. If the vendor restates history, enter the restated weeks as new rows with the restatement noted in `evidence`.

## When something does not fit
A new metric (for example, order counts) needs a definition in `config/metrics/licensed_data.yaml` and the metric name added to
`METRICS` in `pipeline/sources/licensed_data.py`. That is a code change, so make it on a branch with a test, and tell the user.
A new vendor needs a source declaration in `pipeline/sources/licensed_data.py`; use `/add-source`.
