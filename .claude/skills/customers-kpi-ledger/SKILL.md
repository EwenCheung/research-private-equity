---
name: customers-kpi-ledger
description: Add or refresh customer-count claims in data/ledgers/customers_kpi_claims.csv. Use when Anthropic's newsroom states a new customer number (business customers, customers above $100K or $1M, Fortune-N counts).
---

# Customers KPI ledger

The Customers page shows what Anthropic says about its own customer base. Source only Anthropic's newsroom (`https://www.anthropic.com/news/<slug>`) or its own filings. Never press reports, never estimates.

1. Find the sentence. Read the newsroom page, not a summary of it.
2. Append a tuple to `CLAIMS` in `build_ledger.py`: page slug, the date printed on the page, `as_of` (the announcement date), the number, `what` (exactly what was counted), `threshold` (the cut-off as worded) and the quote copied word for word.
3. Run `uv run python .claude/skills/customers-kpi-ledger/build_ledger.py`. It fetches each page and fails, writing nothing, if a quote or date is not on the page.
4. Leave out anything you cannot quote. Vague claims ("hundreds of thousands", "nearly 7x") and relative dates ("two years ago") are not entered; list them as gaps in the chart's assumptions.
5. `uv run pytest tests/test_customers.py`, then rebuild the marts.

`entered_by` is always `EwenCheung via Claude`: the assistant compiled the row and the build machine-verified the quote.
